# %% [markdown]
# # 04 - Rebalancing priorities (decision support)
#
# Combines **current availability** (latest GBFS snapshot) + **weather forecast** (Open-Meteo) +
# **registered ML model** to project each station's stock over the next `HORIZON_H` hours:
#
#     projected_bikes = bikes_available - predicted_departures + predicted_arrivals
#
# A station is flagged **"risque vide"** if projected bikes fall to <= max(2, 10 % capacity),
# **"risque saturee"** if projected free docks fall to <= max(2, 10 % capacity).
# The output `gold_station_priority` feeds the Power BI page "Interventions prioritaires".
#
# Limitation (documented): the model predicts *observed* trips; demand lost because a station was already
# empty is not observable in trip data, so projections underestimate pressure at chronically empty stations.

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

if not IS_FABRIC:
    mlflow.set_tracking_uri(f"sqlite:///{ROOT}/mlruns.db")   # Fabric has MLflow tracking built in


def read_pdf(name: str) -> pd.DataFrame:
    return read_table(name).toPandas()


def save_pdf(pdf: pd.DataFrame, name: str) -> None:
    save_table(spark.createDataFrame(pdf), name)

HORIZON_H = 2
MODEL_NAME = "smartbike_demand_model"
NY_TZ = "America/New_York"



import json

import requests

# %% ---------- 1. Current status: live GBFS call, raw response archived in Bronze ----------
# Fallback to the latest archived snapshot if the API is unreachable (offline demo).
gbfs_dir = f"{BRONZE_LOCAL_PATH}/gbfs"
os.makedirs(gbfs_dir, exist_ok=True)
try:
    status_json = requests.get("https://gbfs.citibikenyc.com/gbfs/en/station_status.json", timeout=30).json()
    latest = f"{gbfs_dir}/station_status_{pd.Timestamp.now(tz='UTC'):%Y%m%dT%H%M%SZ}.json"
    with open(latest, "w", encoding="utf-8") as fh:
        json.dump(status_json, fh)
except requests.RequestException as exc:
    print(f"[warn] live GBFS unavailable ({exc}) -> using last archived snapshot")
    latest = sorted(glob.glob(f"{gbfs_dir}/station_status_*.json"))[-1]
    status_json = json.load(open(latest, encoding="utf-8"))
snapshot_utc = pd.Timestamp(status_json["last_updated"], unit="s", tz="UTC")
status = pd.DataFrame(status_json["data"]["stations"])[
    ["station_id", "num_bikes_available", "num_docks_available", "is_installed", "is_renting", "is_returning"]
].rename(columns={"station_id": "gbfs_station_id"})
print(f"snapshot: {latest}  ({snapshot_utc.tz_convert(NY_TZ)})")

stations = read_pdf("silver_stations")[["station_id", "gbfs_station_id", "station_name", "lat", "lon", "capacity"]]
cur = stations.merge(status, on="gbfs_station_id", how="inner")
cur = cur[(cur.is_installed == 1) & (cur.capacity > 0)]

# %% ---------- 2. Weather forecast for the next hours ----------
now_local = snapshot_utc.tz_convert(NY_TZ).floor("h").tz_localize(None)
fc = requests.get("https://api.open-meteo.com/v1/forecast", timeout=30, params={
    "latitude": 40.7306, "longitude": -73.9866, "timezone": NY_TZ, "forecast_days": 2,
    "hourly": "temperature_2m,precipitation,wind_speed_10m,relative_humidity_2m"}).json()["hourly"]
wx = pd.DataFrame({"hour_ts": pd.to_datetime(fc["time"]), "temperature_c": fc["temperature_2m"],
                   "precipitation_mm": fc["precipitation"], "wind_kmh": fc["wind_speed_10m"],
                   "humidity_pct": fc["relative_humidity_2m"]})
wx = wx[(wx.hour_ts >= now_local) & (wx.hour_ts < now_local + pd.Timedelta(hours=HORIZON_H))]

# %% ---------- 3. Build features (same as training) and predict ----------
enc = read_pdf("gold_station_hour_encoding")
enc_station = read_pdf("gold_station_encoding")
grid = cur.merge(wx, how="cross")
grid["hour"] = grid.hour_ts.dt.hour
grid["dow"] = (grid.hour_ts.dt.day_of_week + 1) % 7 + 1           # Spark convention: 1 = Sunday
grid["is_weekend"] = grid["dow"].isin([1, 7]).astype(int)
grid["capacity"] = grid["capacity"].astype(float)
grid = (grid.merge(enc, on=["station_id", "hour", "is_weekend"], how="left")
            .merge(enc_station, on="station_id", how="left"))
enc_cols = [c for c in grid.columns if c.startswith("enc_")]
grid[enc_cols] = grid[enc_cols].fillna(0.0)

for target in ["departures", "arrivals"]:
    try:     # production = the version that won the champion/challenger test (notebook 03, step 5)
        model = mlflow.sklearn.load_model(f"models:/{MODEL_NAME}_{target}@champion")
    except Exception:
        model = mlflow.sklearn.load_model(f"models:/{MODEL_NAME}_{target}/latest")
    features = list(model.feature_names_in_)          # exact training feature list, stored in the model
    grid[f"pred_{target}"] = np.clip(model.predict(grid[features]), 0, None)

# %% ---------- 4. Projection, risk and recommended action ----------
agg = grid.groupby(["station_id", "station_name", "lat", "lon", "capacity",
                    "num_bikes_available", "num_docks_available", "is_renting", "is_returning"], as_index=False) \
          .agg(pred_departures=("pred_departures", "sum"), pred_arrivals=("pred_arrivals", "sum"))
cap = agg["capacity"]
threshold = np.maximum(2, 0.10 * cap)
agg["projected_bikes"] = (agg.num_bikes_available - agg.pred_departures + agg.pred_arrivals).clip(0, cap)
agg["projected_docks"] = cap - agg["projected_bikes"]
agg["fill_ratio_now"] = (agg.num_bikes_available / cap).round(3)
agg["risk"] = np.select(
    [agg.num_bikes_available == 0, agg.num_docks_available == 0,
     agg.projected_bikes <= threshold, agg.projected_docks <= threshold],
    ["vide maintenant", "saturee maintenant", "risque vide", "risque saturee"], default="ok")
target_bikes = (0.5 * cap).round()
agg["bikes_to_move"] = np.where(agg.risk.isin(["vide maintenant", "risque vide"]),
                                target_bikes - agg.projected_bikes,
                                np.where(agg.risk.isin(["saturee maintenant", "risque saturee"]),
                                         -(agg.projected_bikes - target_bikes), 0)).round().astype(int)
agg["action"] = np.select([agg.bikes_to_move > 0, agg.bikes_to_move < 0],
                          ["ajouter des velos", "retirer des velos"], default="aucune")
# priority = how many bikes must move x how fast demand is expected (busy stations first)
agg["priority_score"] = (agg.bikes_to_move.abs() * (1 + agg.pred_departures + agg.pred_arrivals)).round(1)
agg["priority_rank"] = agg.priority_score.rank(ascending=False, method="first").astype(int)
agg["snapshot_time_local"] = now_local
agg["horizon_hours"] = HORIZON_H

save_pdf(agg.sort_values("priority_rank"), "gold_station_priority")
pd.set_option("display.width", 200)
print(agg.risk.value_counts())
print(agg.sort_values("priority_rank").head(10)[["station_name", "num_bikes_available", "capacity",
                                                  "pred_departures", "pred_arrivals", "risk", "action", "bikes_to_move"]])
