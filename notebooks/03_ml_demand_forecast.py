# %% [markdown]
# # 03 - Demand forecast: departures & arrivals per station and hour (regression + MLflow)
#
# * **Target**: `departures` and `arrivals` per station-hour (counts -> Poisson loss).
# * **Split**: chronological - last 7 days = test (no shuffling: we predict the future from the past).
# * **Baseline**: historical mean of the station for the same hour and weekday/weekend type.
# * **Model selection**: 7 algorithms (Ridge, Poisson GLM, neural network, Random Forest, Extra Trees, gradient
#   boosting with squared and Poisson loss) compared on a VALIDATION week; the test week is used only once.
# * **Features**: calendar + weather + station encodings (computed on the past only).
# * **Metrics**: Poisson deviance (selection), MAE, RMSE (+ improvement vs baseline). Everything tracked in MLflow,
#   the selected model is registered and promoted champion only if it beats the current one.
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


import pickle
import time

import mlflow
import numpy as np
import pandas as pd
from mlflow.models import infer_signature
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import PoissonRegressor, Ridge
from sklearn.metrics import mean_absolute_error, mean_poisson_deviance, mean_squared_error
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder, StandardScaler

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
ENC_COLS = ["enc_dep_station_hour", "enc_arr_station_hour", "enc_dep_recent", "enc_arr_recent",
            "enc_dep_station", "enc_arr_station"]


def encodings(past: pd.DataFrame, until: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Historical station levels learnt from `past` only (rows strictly before `until`)."""
    e = (past.groupby(KEYS)
         .agg(enc_dep_station_hour=("departures", "mean"), enc_arr_station_hour=("arrivals", "mean"))
         .reset_index())
    # "Recent" level (last 14 days) lets the model follow the seasonal trend (Aug -> Oct usage drop).
    rec = (past[past["hour_ts"] >= until - pd.Timedelta(days=14)].groupby(KEYS)
           .agg(enc_dep_recent=("departures", "mean"), enc_arr_recent=("arrivals", "mean")).reset_index())
    e_station = (past.groupby("station_id")
                 .agg(enc_dep_station=("departures", "mean"), enc_arr_station=("arrivals", "mean")).reset_index())
    return e.merge(rec, on=KEYS, how="left"), e_station


def add_features(frame: pd.DataFrame, e: pd.DataFrame, e_station: pd.DataFrame) -> pd.DataFrame:
    out = frame.merge(e, on=KEYS, how="left").merge(e_station, on="station_id", how="left")
    out[ENC_COLS] = out[ENC_COLS].fillna(0.0)
    return out


# Feature names are stored in the model (feature_names_in_) and reused by notebook 04 at inference.
FEATURES = ["hour", "dow", "is_weekend", "temperature_c", "precipitation_mm", "wind_kmh", "humidity_pct",
            "capacity", "lat", "lon", *ENC_COLS]
TARGETS = [("departures", "enc_dep_station_hour"), ("arrivals", "enc_arr_station_hour")]


def metrics(y, p, prefix):
    return {f"{prefix}_mae": mean_absolute_error(y, p),
            f"{prefix}_rmse": float(np.sqrt(mean_squared_error(y, p))),
            f"{prefix}_poisson_dev": mean_poisson_deviance(y, np.clip(p, 1e-3, None))}


# %% ---------- 3. Model selection: 7 algorithms compared on a VALIDATION week (test week untouched) ----------
# Protocol. validation = the last 7 days of TRAIN; encodings are recomputed on the weeks before it, so the
# validation rows are as unseen as the test rows will be. Every candidate gets the SAME budget: a random sample of
# SELECTION_ROWS rows (a Random Forest on the 4.6 M rows would take ~25 min per target), scored on the FULL
# validation week. The test week is used once, by the selected model only (section 4): choosing a model on the
# test set would make its score optimistic.
# Metric. The risk simulation (notebook 04) consumes predicted RATES, so what matters is a good conditional mean:
# the mean Poisson deviance is the proper score for that. MAE rewards the median (0 for most station-hours) and
# is reported alongside, as is the serving cost (fit time, artifact size).
# Rule. Lowest deviance; among candidates within 1 % of it, the lightest artifact wins (it is loaded by the API
# and the app, and retrained every month).
SELECTION_ROWS = int(os.environ.get("SMARTBIKE_SELECTION_ROWS", 1_000_000))
TOLERANCE = 0.01


def candidates() -> dict[str, tuple[str, object]]:
    """Seven model families, from linear to non-linear; hyper-parameters are standard, not tuned on the test set."""
    hour_dow = ["hour", "dow"]
    counts = [*ENC_COLS, "precipitation_mm"]                 # skewed, count-like -> log scale for log-link models
    other = [c for c in FEATURES if c not in hour_dow + counts]

    def linear(model, log_counts: bool):
        num = make_pipeline(SimpleImputer(strategy="median"), StandardScaler())
        parts = [("hour_dow", OneHotEncoder(handle_unknown="ignore"), hour_dow)]
        if log_counts:
            parts += [("num", num, other),
                      ("log", make_pipeline(SimpleImputer(strategy="median"), FunctionTransformer(np.log1p),
                                            StandardScaler()), counts)]
        else:
            parts += [("num", num, other + counts)]
        return make_pipeline(ColumnTransformer(parts), model)

    # Bagged trees: each tree sees TREE_ROWS rows, so the artifact size does not grow with the training period
    # (100 deep trees on 4.6 M rows weigh ~2 GB; 50 trees with leaves >= 100 rows weigh ~45 MB and score better).
    trees = dict(n_estimators=50, min_samples_leaf=100, max_features=0.5, bootstrap=True, max_samples=TREE_ROWS,
                 n_jobs=-1, random_state=42)
    hgb = dict(learning_rate=0.08, max_iter=400, max_leaf_nodes=63, min_samples_leaf=50, l2_regularization=1.0,
               early_stopping=True, random_state=42)
    return {
        "ridge": ("linéaire (moindres carrés L2)", linear(Ridge(alpha=1.0), log_counts=False)),
        "poisson_glm": ("linéaire généralisé, lien log", linear(PoissonRegressor(alpha=1e-4, max_iter=300), True)),
        "mlp": ("réseau de neurones 64-32", linear(MLPRegressor(hidden_layer_sizes=(64, 32), batch_size=1024,
                                                                 max_iter=50, early_stopping=True,
                                                                 random_state=42), True)),
        "random_forest": ("forêt aléatoire (bagging)", RandomForestRegressor(**trees)),
        "extra_trees": ("arbres extrêmement aléatoires", ExtraTreesRegressor(**trees)),
        "hgb_squared": ("gradient boosting, perte quadratique", HistGradientBoostingRegressor(**hgb)),
        "hgb_poisson": ("gradient boosting, perte de Poisson",
                        HistGradientBoostingRegressor(loss="poisson", **hgb)),
    }


val_cut = cutoff - pd.Timedelta(days=7)
inner, val = train[train["hour_ts"] < val_cut], train[train["hour_ts"] >= val_cut]
enc_sel, enc_station_sel = encodings(inner, val_cut)
inner, val = add_features(inner, enc_sel, enc_station_sel), add_features(val, enc_sel, enc_station_sel)
sample = inner.sample(min(SELECTION_ROWS, len(inner)), random_state=0)
TREE_ROWS = len(sample)
print(f"selection: fit on {len(sample):,} of {len(inner):,} rows, validate on {len(val):,} rows "
      f"({val.hour_ts.min()} -> {val.hour_ts.max()})")

comparison = []
for target, base_col in TARGETS:
    rows = [{"target": target, "model": "baseline", "family": "moyenne historique station × heure",
             **metrics(val[target], val[base_col], "val"), "fit_seconds": 0.0, "size_mb": 0.0}]
    for name, (family, estimator) in candidates().items():
        with mlflow.start_run(run_name=f"select_{name}_{target}"):
            t0 = time.perf_counter()
            estimator.fit(sample[FEATURES], sample[target])
            fit_s = time.perf_counter() - t0
            pred = np.clip(estimator.predict(val[FEATURES]), 0, None)
            row = {"target": target, "model": name, "family": family, **metrics(val[target], pred, "val"),
                   "fit_seconds": round(fit_s, 1), "size_mb": round(len(pickle.dumps(estimator)) / 1e6, 1)}
            mlflow.set_tags({"stage": "model_selection", "target": target, "model": name})
            mlflow.log_params({"model": name, "target": target, "fit_rows": len(sample), "val_rows": len(val)})
            mlflow.log_metrics({k: v for k, v in row.items() if k.startswith("val_") or k in ("fit_seconds",
                                                                                             "size_mb")})
        rows.append(row)
        print(f"  {target:10s} {name:14s} dev {row['val_poisson_dev']:.4f}  MAE {row['val_mae']:.4f}  "
              f"fit {fit_s:5.0f}s  {row['size_mb']:6.1f} MB", flush=True)
    block = pd.DataFrame(rows)
    models_only = block[block.model != "baseline"]
    best_dev = models_only["val_poisson_dev"].min()
    near = models_only[models_only["val_poisson_dev"] <= best_dev * (1 + TOLERANCE)]
    winner = near.sort_values(["size_mb", "val_poisson_dev"]).iloc[0]["model"]
    base_dev = block.loc[block.model == "baseline", "val_poisson_dev"].iloc[0]
    block["val_dev_gain_vs_baseline_pct"] = (1 - block["val_poisson_dev"] / base_dev) * 100
    block["rank"] = block["val_poisson_dev"].rank(method="min").astype(int)
    block["selected"] = block["model"] == winner
    comparison.append(block)
    print(f"  -> {target}: selected {winner}")

comparison = pd.concat(comparison, ignore_index=True)
SELECTED = dict(comparison.loc[comparison.selected, ["target", "model"]].itertuples(index=False))

# Final encodings: learnt on the whole train period, used by the selected models and at inference.
enc, enc_station = encodings(train, cutoff)
train, test = add_features(train, enc, enc_station), add_features(test, enc, enc_station)


# MLflow >= 3 serializes sklearn models with skops, which only loads explicitly trusted types. We list the
# non-default types of THIS model (the one we just trained), and trust exactly those - not "everything".
def trusted_types(model) -> list[str]:
    import skops.io
    return skops.io.get_untrusted_types(data=skops.io.dumps(model))


def log_model(model, name, signature, registered_name):
    try:
        return mlflow.sklearn.log_model(model, name=name, signature=signature, registered_model_name=registered_name,
                                        skops_trusted_types=trusted_types(model))
    except (TypeError, ImportError):   # older MLflow (e.g. Fabric runtime 1.3): pickle format, `artifact_path`
        return mlflow.sklearn.log_model(model, artifact_path=name, signature=signature,
                                        registered_model_name=registered_name)


# %% ---------- 4. Refit the SELECTED model on the full train period, score it ONCE on the test week ----------
results, predictions = [], test[["station_id", "station_name", "hour_ts", "departures", "arrivals"]].copy()

for target, base_col in TARGETS:
    algo = SELECTED[target]
    with mlflow.start_run(run_name=f"{algo}_{target}") as run:
        family, model = candidates()[algo]
        params = {k: v for k, v in model.get_params().items() if isinstance(v, (int, float, str, bool, type(None)))}
        model.fit(train[FEATURES], train[target])
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

        mlflow.set_tags({"stage": "production_candidate", "model": algo, "family": family})
        mlflow.log_params({**params, "model": algo, "target": target, "n_features": len(FEATURES),
                           "train_rows": len(train), "test_rows": len(test), "cutoff": str(cutoff)})
        mlflow.log_metrics(m)
        head = train[FEATURES].head(500)
        info = log_model(model, f"model_{target}", infer_signature(head, model.predict(head)),
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
        results.append({"target": target, "model": algo, "run_id": run.info.run_id, "model_uri": info.model_uri,
                        **m})

# %% ---------- 5. Persist predictions, metrics and encodings (needed for inference in notebook 04) ----------
predictions["pred_net_flow"] = (predictions["pred_arrivals"] - predictions["pred_departures"]).round(2)
save_pdf(predictions, "gold_demand_forecast")
save_pdf(pd.DataFrame(results), "gold_ml_metrics")
save_pdf(comparison.round(4), "gold_model_comparison")
save_pdf(enc, "gold_station_hour_encoding")
save_pdf(enc_station, "gold_station_encoding")

# %% ---------- 6. MLOps: champion / challenger promotion in the MLflow Model Registry ----------
# The new version is a *challenger*. It becomes *champion* (alias used by inference in notebook 04 and the API)
# only if it beats the current champion AND the naive baseline. Both models are re-scored NOW on the same test
# week with the same criterion as the model selection (Poisson deviance), so an older champion trained with
# other metrics is still judged fairly. Every decision is logged: an auditable promotion trail.
from mlflow import MlflowClient  # noqa: E402

client = MlflowClient()
decisions = []
for r in results:
    name = f"{MODEL_NAME}_{r['target']}"
    new_version = max(int(v.version) for v in client.search_model_versions(f"name='{name}'"))
    client.set_registered_model_alias(name, "challenger", new_version)
    try:
        champ = client.get_model_version_by_alias(name, "champion")
        champ_model = mlflow.sklearn.load_model(f"models:/{name}@champion")
        champ_pred = np.clip(champ_model.predict(test[FEATURES]), 0, None)
        champ_m = metrics(test[r["target"]], champ_pred, "champion")
        champ_algo = client.get_run(champ.run_id).data.params.get("model", type(champ_model).__name__)
    except Exception as exc:     # first run: no champion yet
        print(f"{name}: no usable champion ({type(exc).__name__}) -> challenger competes with the baseline only")
        champ, champ_algo = None, None
        champ_m = {"champion_mae": float("inf"), "champion_poisson_dev": float("inf")}
    champ_mae = champ_m["champion_mae"]
    promote = (r["model_poisson_dev"] < champ_m["champion_poisson_dev"]
               and r["model_poisson_dev"] < r["baseline_poisson_dev"] and r["model_mae"] < r["baseline_mae"])
    if promote:
        client.set_registered_model_alias(name, "champion", new_version)
    client.set_model_version_tag(name, str(new_version), "validation_status", "promoted" if promote else "rejected")
    decisions.append({"model": name, "algorithm": r["model"], "champion_algorithm_before": champ_algo,
                      "criterion": "poisson_deviance", "challenger_version": new_version,
                      "challenger_poisson_dev": round(r["model_poisson_dev"], 4),
                      "champion_poisson_dev_before": None if champ is None else round(champ_m["champion_poisson_dev"], 4),
                      "baseline_poisson_dev": round(r["baseline_poisson_dev"], 4), "challenger_mae": round(r["model_mae"], 4),
                      "champion_version_before": int(champ.version) if champ else None,
                      "champion_mae_before": None if champ is None else round(champ_mae, 4),
                      "baseline_mae": round(r["baseline_mae"], 4), "promoted": promote,
                      "decided_at": pd.Timestamp.now(tz="UTC").isoformat()})
    print(f"{name}: v{new_version} ({r['model']}) deviance {r['model_poisson_dev']:.4f} / MAE {r['model_mae']:.4f} "
          f"vs champion {champ_m['champion_poisson_dev']:.4f} / {champ_mae:.4f} -> "
          f"{'PROMOTED' if promote else 'kept current champion'}")
save_pdf(pd.DataFrame(decisions), "gold_model_promotion_log")
