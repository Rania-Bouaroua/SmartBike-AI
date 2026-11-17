# %% [markdown]
# # 03 - Demand forecast: departures & arrivals per station and hour (regression + MLflow)
#
# * **Target**: `departures` and `arrivals` per station-hour (counts -> Poisson loss).
# * **Split**: chronological - last 7 days = test (no shuffling: we predict the future from the past).
# * **Baseline**: historical mean of the station for the same hour and weekday/weekend type.
# * **Model**: `HistGradientBoostingRegressor(loss="poisson")` with calendar + weather + station encodings.
# * **Metrics**: MAE, RMSE (+ improvement vs baseline). Experiments tracked with MLflow, model registered.
#
# Features deliberately exclude recent trip lags: trip archives are published monthly, so they would not be
# available at prediction time in production. Only calendar, weather forecast and station history are used.

# %% ---------- Environment: Microsoft Fabric vs local run (identical transformations) ----------
import glob
import os
import sys

from pyspark.sql import DataFrame, SparkSession, Window  # noqa: F401  (used by the notebooks)
from pyspark.sql import functions as F
from pyspark.sql import types as T

try:
    import notebookutils  # noqa: F401  -- only exists inside Microsoft Fabric
    IS_FABRIC = True
except ImportError:
    IS_FABRIC = False

if IS_FABRIC:
    BRONZE = "Files/bronze"                         # default Lakehouse: lh_smartbike
    BRONZE_LOCAL_PATH = "/lakehouse/default/Files/bronze"
else:
    ROOT = os.environ.get("SMARTBIKE_ROOT", os.getcwd()).replace("\\", "/")
    BRONZE = BRONZE_LOCAL_PATH = f"{ROOT}/data/raw"
    LOCAL_TABLES = f"{ROOT}/data/lakehouse/Tables"
    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
    # Spark scratch space inside the project (Windows does not always clean %TEMP%/blockmgr-* on exit)
    import shutil
    SPARK_TMP = f"{ROOT}/data/spark-tmp"
    shutil.rmtree(SPARK_TMP, ignore_errors=True)
    spark = (SparkSession.builder.master("local[*, 4]")   # 4 attempts per task, like a cluster
             .appName("smartbike")
             .config("spark.driver.memory", os.environ.get("SMARTBIKE_DRIVER_MEMORY", "8g"))
             .config("spark.driver.maxResultSize", "2g")
             .config("spark.sql.shuffle.partitions", "48")
             .config("spark.ui.showConsoleProgress", "false")
             .config("spark.local.dir", SPARK_TMP)
             .getOrCreate())
    spark.sparkContext.setLogLevel("ERROR")
spark.conf.set("spark.sql.execution.arrow.pyspark.enabled", "true")
# Citi Bike timestamps and the weather extract are New York *local* time -> one session time zone.
spark.conf.set("spark.sql.session.timeZone", "America/New_York")

# Local Windows runs have no Hadoop native IO (winutils.exe / hadoop.dll), so on a laptop files are
# parsed/written with PyArrow and Spark only does the computation. In Fabric the native readers are used.


def read_csv(pattern: str, schema: T.StructType) -> DataFrame:
    if IS_FABRIC:
        return (spark.read.option("header", True).schema(schema).csv(pattern)
                .withColumn("source_file", F.col("_metadata.file_path")))
    import pyarrow as pa
    import pyarrow.csv as pacsv
    opts = pacsv.ConvertOptions(column_types={f.name: pa.string() for f in schema.fields},
                                strings_can_be_null=True)   # "" -> null, like Spark's CSV reader
    df = None
    for i, f in enumerate(sorted(glob.glob(pattern))):      # one file at a time keeps Python memory low
        part = spark.createDataFrame(pacsv.read_csv(f, convert_options=opts)).withColumn("source_file", F.lit(i))
        df = part if df is None else df.unionByName(part)
    df = df.repartition(48)
    return df.select(*[F.col(f.name).cast(f.dataType) for f in schema.fields], "source_file")


def read_json(pattern: str) -> DataFrame:
    if IS_FABRIC:
        return spark.read.option("multiLine", True).json(pattern)
    docs = [open(f, encoding="utf-8").read() for f in sorted(glob.glob(pattern))]
    return spark.read.json(spark.sparkContext.parallelize(docs, 1))


def read_table(name: str, columns: list[str] | None = None) -> DataFrame:
    """Reads a table, optionally only some columns (column pruning = less memory and I/O)."""
    if IS_FABRIC:
        df = spark.read.table(name)
        return df.select(*columns) if columns else df
    import pyarrow.parquet as pq
    return spark.createDataFrame(pq.read_table(f"{LOCAL_TABLES}/{name}", columns=columns)).repartition(16)


def save_table(df: DataFrame, name: str) -> None:
    """Fabric: managed Delta table in the Lakehouse (Tables/). Local: a folder of Parquet parts per table,
    written by the Spark workers partition by partition (nothing big is collected on the driver)."""
    if IS_FABRIC:
        df.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable(name)
    else:
        import pathlib
        import shutil
        target = pathlib.Path(LOCAL_TABLES) / name
        shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True)
        out = str(target)

        def _write_partition(batches):
            import uuid

            import pyarrow.parquet as pq
            writer = None
            for b in batches:
                if writer is None:
                    writer = pq.ParquetWriter(f"{out}/part-{uuid.uuid4().hex}.parquet", b.schema)
                writer.write_batch(b)
            if writer is not None:
                writer.close()
            import pyarrow as pa
            yield pa.RecordBatch.from_pydict({"done": pa.array([1], pa.int32())})   # always answer (empty partitions too)

        try:
            df.mapInArrow(_write_partition, "done int").collect()
        except Exception as exc:  # noqa: BLE001
            # Local Windows Python workers occasionally crash on tiny unioned frames: a small table is then
            # written from the driver instead (big tables re-raise: they must not be collected).
            if df.limit(1_000_001).count() > 1_000_000:
                raise
            print(f"[warn] distributed write of {name} failed ({type(exc).__name__}) -> driver write")
            import pyarrow.parquet as pq
            shutil.rmtree(target, ignore_errors=True)
            target.mkdir(parents=True)
            pq.write_table(df.toArrow(), f"{out}/part-driver.parquet")
        if not any(target.iterdir()):          # empty table: keep the schema with an empty file
            import pyarrow.parquet as pq
            pq.write_table(df.limit(0).toArrow(), f"{out}/part-empty.parquet")
    print(f"saved table {name}")


import mlflow
import numpy as np
import pandas as pd
from mlflow.models import infer_signature
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance
from sklearn.metrics import mean_absolute_error, mean_squared_error

if not IS_FABRIC:
    mlflow.set_tracking_uri(f"sqlite:///{ROOT}/mlruns.db")   # Fabric has MLflow tracking built in


def read_pdf(name: str) -> pd.DataFrame:
    return read_table(name).toPandas()


def save_pdf(pdf: pd.DataFrame, name: str) -> None:
    save_table(spark.createDataFrame(pdf), name)

EXPERIMENT = "smartbike-demand-forecast"
MODEL_NAME = "smartbike_demand_model"
mlflow.set_experiment(EXPERIMENT)

# %% ---------- 1. Load gold features + chronological split ----------
df = read_pdf("gold_station_hourly")
df["hour_ts"] = pd.to_datetime(df["hour_ts"])
df = df.sort_values("hour_ts")
for c in ["temperature_c", "precipitation_mm", "wind_kmh", "humidity_pct"]:
    df[c] = df[c].astype(float).fillna(df[c].median())
df["is_weekend"] = df["is_weekend"].astype(int)
df["capacity"] = df["capacity"].astype(float)

cutoff = df["hour_ts"].max().normalize() - pd.Timedelta(days=6)   # last 7 full days -> test
train, test = df[df["hour_ts"] < cutoff].copy(), df[df["hour_ts"] >= cutoff].copy()
print(f"train: {train.hour_ts.min()} -> {train.hour_ts.max()}  ({len(train):,} rows)")
print(f"test : {test.hour_ts.min()} -> {test.hour_ts.max()}  ({len(test):,} rows)")

# %% ---------- 2. Station encodings computed on TRAIN ONLY (no leakage) ----------
KEYS = ["station_id", "hour", "is_weekend"]
enc = (train.groupby(KEYS)
       .agg(enc_dep_station_hour=("departures", "mean"), enc_arr_station_hour=("arrivals", "mean"))
       .reset_index())
# "Recent" level (last 14 days of train) lets the model follow the seasonal trend (Aug -> Oct usage drop).
recent = (train[train["hour_ts"] >= cutoff - pd.Timedelta(days=14)].groupby(KEYS)
          .agg(enc_dep_recent=("departures", "mean"), enc_arr_recent=("arrivals", "mean")).reset_index())
enc = enc.merge(recent, on=KEYS, how="left")
enc_station = (train.groupby("station_id")
               .agg(enc_dep_station=("departures", "mean"), enc_arr_station=("arrivals", "mean")).reset_index())
ENC_COLS = ["enc_dep_station_hour", "enc_arr_station_hour", "enc_dep_recent", "enc_arr_recent",
            "enc_dep_station", "enc_arr_station"]


def add_features(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.merge(enc, on=KEYS, how="left").merge(enc_station, on="station_id", how="left")
    out[ENC_COLS] = out[ENC_COLS].fillna(0.0)
    return out


train, test = add_features(train), add_features(test)
# Feature names are stored in the model (feature_names_in_) and reused by notebook 04 at inference.
FEATURES = ["hour", "dow", "is_weekend", "temperature_c", "precipitation_mm", "wind_kmh", "humidity_pct",
            "capacity", "lat", "lon", *ENC_COLS]


def metrics(y, p, prefix):
    return {f"{prefix}_mae": mean_absolute_error(y, p),
            f"{prefix}_rmse": float(np.sqrt(mean_squared_error(y, p)))}


# MLflow >= 3 serializes sklearn models with skops, which only loads explicitly trusted types.
# We trained this model ourselves, so we trust exactly this one type (not "everything").
TRUSTED_TYPES = ["sklearn.ensemble._hist_gradient_boosting.predictor.TreePredictor"]


def log_model(model, name, signature, registered_name):
    try:
        return mlflow.sklearn.log_model(model, name=name, signature=signature,
                                        registered_model_name=registered_name, skops_trusted_types=TRUSTED_TYPES)
    except TypeError:   # older MLflow (e.g. Fabric runtime 1.3): pickle format, `artifact_path` argument
        return mlflow.sklearn.log_model(model, artifact_path=name, signature=signature,
                                        registered_model_name=registered_name)


# %% ---------- 3. Train one model per target, tracked in MLflow ----------
results, predictions = [], test[["station_id", "station_name", "hour_ts", "departures", "arrivals"]].copy()
params = dict(loss="poisson", learning_rate=0.08, max_iter=400, max_leaf_nodes=63,
              min_samples_leaf=50, l2_regularization=1.0, early_stopping=True, random_state=42)

for target, base_col in [("departures", "enc_dep_station_hour"), ("arrivals", "enc_arr_station_hour")]:
    with mlflow.start_run(run_name=f"hgb_poisson_{target}") as run:
        model = HistGradientBoostingRegressor(**params).fit(train[FEATURES], train[target])
        pred = np.clip(model.predict(test[FEATURES]), 0, None)
        m = {**metrics(test[target], test[base_col], "baseline"), **metrics(test[target], pred, "model")}
        m["mae_improvement_pct"] = (1 - m["model_mae"] / m["baseline_mae"]) * 100
        m["rmse_improvement_pct"] = (1 - m["model_rmse"] / m["baseline_rmse"]) * 100

        busy = test[base_col] >= 2      # station-hours that matter most to operators
        m.update(metrics(test.loc[busy, target], pred[busy.values], "model_busy"))
        m.update(metrics(test.loc[busy, target], test.loc[busy, base_col], "baseline_busy"))

        # Network level (sum of all stations per hour): where weather effects become visible.
        sys_h = pd.DataFrame({"h": test["hour_ts"].values, "y": test[target].values,
                              "p": pred, "b": test[base_col].values}).groupby("h").sum()
        m["system_wape_model_pct"] = (sys_h.y - sys_h.p).abs().sum() / sys_h.y.sum() * 100
        m["system_wape_baseline_pct"] = (sys_h.y - sys_h.b).abs().sum() / sys_h.y.sum() * 100

        mlflow.log_params({**params, "target": target, "n_features": len(FEATURES),
                           "train_rows": len(train), "test_rows": len(test), "cutoff": str(cutoff)})
        mlflow.log_metrics(m)
        sample = train[FEATURES].head(500)
        info = log_model(model, f"model_{target}", infer_signature(sample, model.predict(sample)),
                         f"{MODEL_NAME}_{target}")

        imp = permutation_importance(model, test[FEATURES].sample(30000, random_state=0),
                                     test[target].sample(30000, random_state=0),
                                     n_repeats=3, random_state=0, scoring="neg_mean_absolute_error")
        importance = pd.Series(imp.importances_mean, index=FEATURES).sort_values(ascending=False)
        mlflow.log_dict(importance.round(4).to_dict(), f"permutation_importance_{target}.json")
        print(f"\n== {target} ==  run {run.info.run_id}")
        print({k: round(v, 3) for k, v in m.items()})
        print("top features:", importance.head(5).round(3).to_dict())

        predictions[f"pred_{target}"] = pred.round(2)
        predictions[f"baseline_{target}"] = test[base_col].round(2).values
        results.append({"target": target, "run_id": run.info.run_id, "model_uri": info.model_uri, **m})

# %% ---------- 4. Persist predictions, metrics and encodings (needed for inference in notebook 04) ----------
predictions["pred_net_flow"] = (predictions["pred_arrivals"] - predictions["pred_departures"]).round(2)
save_pdf(predictions, "gold_demand_forecast")
save_pdf(pd.DataFrame(results), "gold_ml_metrics")
save_pdf(enc, "gold_station_hour_encoding")
save_pdf(enc_station, "gold_station_encoding")

# %% ---------- 5. MLOps: champion / challenger promotion in the MLflow Model Registry ----------
# The new version is a *challenger*. It becomes *champion* (alias used by inference in notebook 04 and the API)
# only if it beats the current champion's MAE on the same test week AND beats the naive baseline.
# Every decision is logged: an auditable promotion trail.
from mlflow import MlflowClient  # noqa: E402

client = MlflowClient()
decisions = []
for r in results:
    name = f"{MODEL_NAME}_{r['target']}"
    new_version = max(int(v.version) for v in client.search_model_versions(f"name='{name}'"))
    client.set_registered_model_alias(name, "challenger", new_version)
    try:
        champ = client.get_model_version_by_alias(name, "champion")
        champ_mae = client.get_run(champ.run_id).data.metrics.get("model_mae", float("inf"))
    except Exception:            # first run: no champion yet
        champ, champ_mae = None, float("inf")
    promote = r["model_mae"] < champ_mae and r["model_mae"] < r["baseline_mae"]
    if promote:
        client.set_registered_model_alias(name, "champion", new_version)
    client.set_model_version_tag(name, str(new_version), "validation_status", "promoted" if promote else "rejected")
    decisions.append({"model": name, "challenger_version": new_version, "challenger_mae": round(r["model_mae"], 4),
                      "champion_version_before": int(champ.version) if champ else None,
                      "champion_mae_before": None if champ is None else round(champ_mae, 4),
                      "baseline_mae": round(r["baseline_mae"], 4), "promoted": promote,
                      "decided_at": pd.Timestamp.now(tz="UTC").isoformat()})
    print(f"{name}: v{new_version} MAE {r['model_mae']:.4f} vs champion {champ_mae:.4f} -> "
          f"{'PROMOTED' if promote else 'kept current champion'}")
save_pdf(pd.DataFrame(decisions), "gold_model_promotion_log")
