# %% [markdown]
# # 06 - Star schema for Power BI (fact & dimension tables)
# **SmartBike AI** - the Gold layer reorganised as a star schema around the two business problems:
# **bike shortage** (a station has no bike to rent) and **docking capacity** (a station has no free dock to return
# a bike). Facts hold the measures at a fixed grain, dimensions hold the descriptive attributes used to slice them.
#
# | Table | Grain | Answers |
# |---|---|---|
# | `fact_station_availability` | station x 5-min GBFS snapshot | bikes / free docks available, % of time empty / full |
# | `fact_station_hourly` | station x hour | departures (empty the station) and arrivals (fill it), forecast |
# | `fact_station_daily_stress` | station x day | bikes needed at opening (shortage), free docks needed (capacity) |
# | `fact_station_risk` | station x forecast run | probability to be empty / full within 2 h, bikes to move |
# | `fact_trips` | trip | detailed demand (who, when, from where to where) |
# | `dim_station`, `dim_date`, `dim_time`, `dim_weather` | one row per member | slicers and hierarchies |
#
# Keys: `station_key` (int surrogate), `date_key` (yyyymmdd), `hour_key` (0-23), `weather_key` (yyyymmddHH).

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

# %% ---------- Inputs ----------
stations = read_table("silver_stations", ["station_id", "gbfs_station_id", "station_name", "lat", "lon", "capacity",
                                          "region_id", "match_status"])
profile = read_table("gold_station_profile", ["station_id", "station_role", "avg_daily_departures", "imbalance_ratio"])
weather = read_table("silver_weather_hourly", ["weather_hour", "temperature_c", "precipitation_mm", "wind_kmh",
                                               "humidity_pct", "is_rainy"])
hourly = read_table("gold_station_hourly", ["station_id", "hour_ts", "departures", "ebike_departures",
                                            "member_departures", "arrivals", "net_flow"])
forecast = read_table("gold_demand_forecast", ["station_id", "hour_ts", "pred_departures", "pred_arrivals"])
need = read_table("gold_rebalancing_need", ["station_id", "date", "capacity", "max_drawdown", "max_surplus",
                                            "hour_of_min_stock", "hour_of_max_stock", "swing", "forced_moves",
                                            "needs_truck"])
priority = read_table("gold_station_priority", ["station_id", "snapshot_time_local", "num_bikes_available",
                                                "num_docks_available", "projected_bikes", "projected_docks", "p_empty",
                                                "p_full", "risk", "bikes_to_move", "action", "priority_score",
                                                "priority_rank"])
trips = read_table("silver_trips", ["ride_id", "started_at", "start_station_id", "end_station_id", "rideable_type",
                                    "member_casual", "duration_min", "is_round_trip"])


def date_key(col):
    """yyyymmdd integer (local New York date): the usual date key of a star schema."""
    return F.date_format(col, "yyyyMMdd").cast("int")


def hour_key(col):
    return F.hour(col).cast("int")


# %% ---------- Station state history (GBFS station_status snapshots, the ground truth of both problems) ----------
# Trips tell how many bikes leave / arrive; only the GBFS feed tells how many bikes and free docks a station *has*.
# Snapshots are recorded every 5 min (scripts/gbfs_history.py locally, Eventstream -> Eventhouse in Fabric).
STATUS_COLS = ["station_id", "num_bikes_available", "num_ebikes_available", "num_bikes_disabled",
               "num_docks_available", "num_docks_disabled", "is_renting", "is_returning", "last_reported",
               "snapshot_utc"]
if IS_FABRIC:
    status_raw = spark.read.option("mergeSchema", True).parquet(f"{BRONZE}/gbfs_history/*.parquet")
    for c in STATUS_COLS:                       # older snapshots have fewer columns
        if c not in status_raw.columns:
            status_raw = status_raw.withColumn(c, F.lit(None).cast("int"))
    status_raw = status_raw.select(*STATUS_COLS)
else:
    import pandas as pd
    parts = [pd.read_parquet(f).reindex(columns=STATUS_COLS)
             for f in sorted(glob.glob(f"{BRONZE_LOCAL_PATH}/gbfs_history/*.parquet"))]
    pdf = pd.concat(parts, ignore_index=True)
    for c in STATUS_COLS[1:8]:
        pdf[c] = pdf[c].astype("Int64")
    status_raw = spark.createDataFrame(pdf)
print(f"GBFS snapshots: {status_raw.select('snapshot_utc').distinct().count():,} "
      f"({status_raw.count():,} station-states)")

# %% ---------- Dimensions ----------
# dim_station: one row per station (trip id = GBFS short_name), surrogate integer key.
dim_station = (stations.join(profile, "station_id", "left")
               .withColumn("station_key", F.dense_rank().over(Window.orderBy("station_id")).cast("int"))
               .withColumn("capacity", F.col("capacity").cast("int"))
               .withColumn("capacity_band", F.when(F.col("capacity").isNull() | (F.col("capacity") == 0), "inconnue")
                           .when(F.col("capacity") < 20, "petite (< 20)")
                           .when(F.col("capacity") < 40, "moyenne (20-39)").otherwise("grande (40 +)"))
               .withColumn("station_role", F.coalesce("station_role", F.lit("sans historique")))
               .select("station_key", "station_id", "gbfs_station_id", "station_name", "lat", "lon", "capacity",
                       "capacity_band", "region_id", "station_role", "avg_daily_departures", "imbalance_ratio",
                       "match_status"))
dim_station.cache()
keys = F.broadcast(dim_station.select("station_key", "station_id"))
gbfs_keys = F.broadcast(dim_station.filter(F.col("gbfs_station_id").isNotNull())
                        .select("station_key", F.col("gbfs_station_id").alias("gbfs_id"), "capacity"))

# dim_date: every day of the trip history and of the GBFS recordings.
status = (status_raw
          .filter(F.year("last_reported") > 2000)                # placeholder rows of not-installed stations
          .withColumn("snapshot_ts", F.col("snapshot_utc").cast("timestamp")))
bounds = (hourly.select(F.to_date("hour_ts").alias("d"))
          .unionByName(status.select(F.to_date("snapshot_ts").alias("d")))
          .agg(F.min("d").alias("d0"), F.max("d").alias("d1")))
FR_DAYS = {1: "dimanche", 2: "lundi", 3: "mardi", 4: "mercredi", 5: "jeudi", 6: "vendredi", 7: "samedi"}
FR_MONTHS = {8: "août", 9: "septembre", 10: "octobre", 11: "novembre", 12: "décembre"}
day_name = F.create_map(*[x for k, v in FR_DAYS.items() for x in (F.lit(k), F.lit(v))])
month_name = F.create_map(*[x for k, v in FR_MONTHS.items() for x in (F.lit(k), F.lit(v))])
dim_date = (bounds.select(F.explode(F.sequence("d0", "d1")).alias("date"))
            .select(date_key(F.col("date")).alias("date_key"), "date",
                    F.year("date").alias("year"), F.month("date").alias("month"),
                    F.coalesce(month_name[F.month("date")], F.date_format("date", "MMMM")).alias("month_name"),
                    F.weekofyear("date").alias("week_of_year"), F.dayofmonth("date").alias("day"),
                    ((F.dayofweek("date") + 5) % 7 + 1).alias("day_of_week"),        # 1 = Monday
                    day_name[F.dayofweek("date")].alias("day_name"),
                    F.dayofweek("date").isin(1, 7).alias("is_weekend"),
                    F.when(F.col("date") <= F.lit("2026-10-31").cast("date"), "historique trajets")
                    .otherwise("collecte temps réel").alias("data_period")))

# dim_time: 24 hours with the operational periods used by the dispatchers.
dim_time = (spark.range(24).select(F.col("id").cast("int").alias("hour_key"))
            .withColumn("hour_label", F.format_string("%02d:00", "hour_key"))
            .withColumn("period", F.when(F.col("hour_key") < 6, "nuit")
                        .when(F.col("hour_key") < 10, "pointe du matin")
                        .when(F.col("hour_key") < 16, "journée")
                        .when(F.col("hour_key") < 20, "pointe du soir").otherwise("soirée"))
            .withColumn("is_peak", F.col("period").startswith("pointe")))

# dim_weather: one row per hour of weather, readable bands for slicers.
dim_weather = (weather
               .withColumn("weather_key", F.date_format("weather_hour", "yyyyMMddHH").cast("long"))
               .withColumn("condition", F.when(F.col("is_rainy"), "pluie").otherwise("sec"))
               .withColumn("temperature_band", F.when(F.col("temperature_c") < 10, "< 10 °C")
                           .when(F.col("temperature_c") < 20, "10-19 °C")
                           .when(F.col("temperature_c") < 25, "20-24 °C").otherwise("25 °C +"))
               .select("weather_key", "weather_hour", "temperature_c", "precipitation_mm", "wind_kmh",
                       "humidity_pct", "condition", "temperature_band"))

# %% ---------- Fact 1: fact_station_availability (grain: station x 5-min snapshot) ----------
# Problem 1 (bike shortage): is_empty = no bike to rent. Problem 2 (docking capacity): is_full = no free dock to
# return a bike. Snapshots are evenly spaced, so AVERAGE(is_empty) = share of time a station is empty.
LOW = 2
fact_availability = (status.join(gbfs_keys, status.station_id == gbfs_keys.gbfs_id)
                     .withColumn("bikes", F.col("num_bikes_available").cast("int"))
                     .withColumn("docks", F.col("num_docks_available").cast("int"))
                     .withColumn("in_service", (F.col("is_renting") == 1) & (F.col("is_returning") == 1))
                     .select("station_key", date_key(F.col("snapshot_ts")).alias("date_key"),
                             hour_key(F.col("snapshot_ts")).alias("hour_key"), "snapshot_ts",
                             F.col("bikes").alias("bikes_available"),
                             F.col("num_ebikes_available").cast("int").alias("ebikes_available"),
                             F.col("num_bikes_disabled").cast("int").alias("bikes_disabled"),
                             F.col("docks").alias("docks_available"),
                             F.col("num_docks_disabled").cast("int").alias("docks_disabled"), "capacity",
                             "in_service",
                             (F.col("in_service") & (F.col("bikes") == 0)).cast("int").alias("is_empty"),
                             (F.col("in_service") & (F.col("docks") == 0)).cast("int").alias("is_full"),
                             (F.col("in_service") & (F.col("bikes") <= LOW)).cast("int").alias("is_low_bikes"),
                             (F.col("in_service") & (F.col("docks") <= LOW)).cast("int").alias("is_low_docks"),
                             F.when(F.col("bikes") + F.col("docks") > 0,
                                    F.round(F.col("bikes") / (F.col("bikes") + F.col("docks")), 3))
                             .alias("fill_ratio")))

# %% ---------- Fact 2: fact_station_hourly (grain: station x hour) - demand that empties / fills stations ----------
fact_hourly = (hourly.join(forecast, ["station_id", "hour_ts"], "left").join(keys, "station_id")
               .select("station_key", date_key(F.col("hour_ts")).alias("date_key"),
                       hour_key(F.col("hour_ts")).alias("hour_key"),
                       F.date_format("hour_ts", "yyyyMMddHH").cast("long").alias("weather_key"),
                       "departures", "arrivals", "net_flow", "ebike_departures", "member_departures",
                       F.round("pred_departures", 3).alias("pred_departures"),
                       F.round("pred_arrivals", 3).alias("pred_arrivals")))

# %% ---------- Fact 3: fact_station_daily_stress (grain: station x day) ----------
# Cumulative net flow over the day: the lowest point = bikes a station must hold at opening never to run empty
# (shortage), the highest point = free docks it needs never to be full (docking capacity).
fact_daily = (need.join(keys, "station_id")
              .select("station_key", date_key(F.col("date").cast("date")).alias("date_key"),
                      F.col("capacity").cast("int").alias("capacity"),
                      (-F.col("max_drawdown")).cast("int").alias("bikes_needed_at_open"),
                      F.col("max_surplus").cast("int").alias("docks_needed_at_open"),
                      "hour_of_min_stock", "hour_of_max_stock", "swing", "forced_moves",
                      F.col("needs_truck").cast("int").alias("needs_truck"))
              .withColumn("shortage_exceeds_capacity",
                          (F.col("bikes_needed_at_open") > F.col("capacity")).cast("int"))
              .withColumn("docking_exceeds_capacity",
                          (F.col("docks_needed_at_open") > F.col("capacity")).cast("int")))

# %% ---------- Fact 4: fact_station_risk (grain: station x forecast run) - the 2-hour outlook ----------
fact_risk = (priority.join(keys, "station_id")
             .withColumn("ts", F.col("snapshot_time_local").cast("timestamp"))
             .select("station_key", date_key(F.col("ts")).alias("date_key"), hour_key(F.col("ts")).alias("hour_key"),
                     F.col("ts").alias("forecast_run_ts"), "num_bikes_available", "num_docks_available",
                     "projected_bikes", "projected_docks", "p_empty", "p_full", "risk", "bikes_to_move", "action",
                     "priority_score", "priority_rank"))

# %% ---------- Fact 5: fact_trips (grain: trip) ----------
fact_trips = (trips.join(keys.withColumnRenamed("station_key", "start_station_key")
                         .withColumnRenamed("station_id", "start_station_id"), "start_station_id", "left")
              .join(keys.withColumnRenamed("station_key", "end_station_key")
                    .withColumnRenamed("station_id", "end_station_id"), "end_station_id", "left")
              .select("ride_id", date_key(F.col("started_at")).alias("date_key"),
                      hour_key(F.col("started_at")).alias("hour_key"),
                      F.date_format("started_at", "yyyyMMddHH").cast("long").alias("weather_key"),
                      "start_station_key", "end_station_key", "rideable_type", "member_casual",
                      F.round("duration_min", 2).alias("duration_min"), F.col("is_round_trip").cast("int")
                      .alias("is_round_trip")))

# %% ---------- Save + integrity checks (every fact key must exist in its dimension) ----------
tables = {"dim_station": dim_station, "dim_date": dim_date, "dim_time": dim_time, "dim_weather": dim_weather,
          "fact_station_availability": fact_availability, "fact_station_hourly": fact_hourly,
          "fact_station_daily_stress": fact_daily, "fact_station_risk": fact_risk, "fact_trips": fact_trips}
for name, df in tables.items():
    save_table(df, name)

dates = F.broadcast(dim_date.select("date_key"))
for name in ["fact_station_availability", "fact_station_hourly", "fact_station_daily_stress", "fact_station_risk",
             "fact_trips"]:
    f = read_table(name, ["date_key"])
    orphans = f.join(dates, "date_key", "left_anti").count()
    print(f"{name:<28} {f.count():>12,} rows   date keys missing in dim_date: {orphans}")
    assert orphans == 0, f"{name}: {orphans} rows without a date in dim_date"

# %% ---------- The two questions, answered from the star schema ----------
avail = read_table("fact_station_availability", ["station_key", "is_empty", "is_full", "in_service"])
summary = (avail.filter("in_service").agg(F.round(100 * F.avg("is_empty"), 1).alias("pct_time_empty"),
                                          F.round(100 * F.avg("is_full"), 1).alias("pct_time_full"),
                                          F.countDistinct("station_key").alias("stations")))
summary.show()
(avail.filter("in_service").groupBy("station_key")
 .agg(F.round(100 * F.avg("is_empty"), 1).alias("pct_time_empty"), F.round(100 * F.avg("is_full"), 1)
      .alias("pct_time_full"))
 .join(dim_station.select("station_key", "station_name", "capacity"), "station_key")
 .orderBy(F.desc("pct_time_empty")).show(10, truncate=False))
