# %% [markdown]
# # 01 - Bronze -> Silver (Apache Spark)
# **SmartBike AI** - cleaning, typing, deduplication, station-ID reconciliation, data-quality report.
#
# | Input (Bronze, Lakehouse `Files/bronze`) | Output (Silver, Delta tables) |
# |---|---|
# | `trips/*.csv` (Citi Bike archives) | `silver_trips` |
# | `gbfs/station_information_*.json` | `silver_stations` |
# | `weather/*.json` (Open-Meteo) | `silver_weather_hourly` |
# | | `gold_data_quality` (DQ report) |
#
# Runs unchanged in **Microsoft Fabric** (attach the `lh_smartbike` Lakehouse as default) and **locally**
# (`python notebooks/01_bronze_to_silver.py` from the repo root) for testing.

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

# %% ---------- 1. Trips: read with an explicit schema ----------
# Station ids MUST be strings: "6257.03" would become 6257.03 (double) and "JC001" would be null.
trip_schema = T.StructType([
    T.StructField("ride_id", T.StringType()),
    T.StructField("rideable_type", T.StringType()),
    T.StructField("started_at", T.StringType()),
    T.StructField("ended_at", T.StringType()),
    T.StructField("start_station_name", T.StringType()),
    T.StructField("start_station_id", T.StringType()),
    T.StructField("end_station_name", T.StringType()),
    T.StructField("end_station_id", T.StringType()),
    T.StructField("start_lat", T.DoubleType()),
    T.StructField("start_lng", T.DoubleType()),
    T.StructField("end_lat", T.DoubleType()),
    T.StructField("end_lng", T.DoubleType()),
    T.StructField("member_casual", T.StringType()),
])

raw_trips = read_csv(f"{BRONZE}/trips/*.csv", trip_schema)
n_raw = raw_trips.count()
print(f"raw trips: {n_raw:,}")

# %% ---------- 2. Typing + quality flags ----------
NYC_BBOX = dict(lat_min=40.45, lat_max=40.95, lng_min=-74.30, lng_max=-73.65)


def in_bbox(lat, lng):
    return lat.between(NYC_BBOX["lat_min"], NYC_BBOX["lat_max"]) & lng.between(NYC_BBOX["lng_min"], NYC_BBOX["lng_max"])


typed = (raw_trips
         .withColumn("started_at", F.to_timestamp("started_at"))
         .withColumn("ended_at", F.to_timestamp("ended_at"))
         .withColumn("start_station_id", F.trim("start_station_id"))
         .withColumn("end_station_id", F.trim("end_station_id"))
         .withColumn("duration_min", (F.unix_timestamp("ended_at") - F.unix_timestamp("started_at")) / 60.0))

# Deduplicate on the business key (keeps the first occurrence).
w = Window.partitionBy("ride_id").orderBy("source_file")
deduped = typed.withColumn("_rn", F.row_number().over(w)).filter("_rn = 1").drop("_rn")
n_dupes = n_raw - deduped.count()

# Each monthly archive also holds a few trips that *started* the evening before the month (e.g. 31/07 23:50
# -> ended in August). They would create a partial first day (biased daily KPIs, wrong period) -> flagged.
# Loaded months = months holding >= 1 % of the rows (compared as 'yyyy-MM' strings: no time-zone conversion).
loaded_months = [r.m for r in deduped.groupBy(F.date_format("started_at", "yyyy-MM").alias("m")).count()
                 .filter(F.col("count") >= 0.01 * n_raw).collect()]
print("loaded months:", sorted(loaded_months))

flagged = deduped.withColumn(
    "dq_issue",
    F.when(F.col("started_at").isNull() | F.col("ended_at").isNull(), "unparseable_timestamp")
     .when(~F.date_format("started_at", "yyyy-MM").isin(loaded_months), "started_outside_loaded_months")
     .when(F.col("start_station_id").isNull(), "missing_start_station")
     .when(F.col("end_station_id").isNull(), "missing_end_station")      # often lost/stolen or dockless e-bikes
     .when(F.col("duration_min") < 1, "duration_lt_1min")               # false starts / re-docks
     .when(F.col("duration_min") > 24 * 60, "duration_gt_24h")
     .when(~in_bbox(F.col("start_lat"), F.col("start_lng")), "start_outside_nyc")
     .when(F.col("end_lat").isNotNull() & ~in_bbox(F.col("end_lat"), F.col("end_lng")), "end_outside_nyc")
)
flagged.cache()

# %% ---------- 3. Data-quality report (kept as a table: anomalies are documented, not hidden) ----------
dq = (flagged.groupBy(F.coalesce("dq_issue", F.lit("valid")).alias("check"))
      .agg(F.count("*").alias("rows"))
      .union(spark.createDataFrame([("duplicate_ride_id", n_dupes)], "check string, rows long"))
      .withColumn("pct_of_raw", F.round(F.col("rows") / F.lit(n_raw) * 100, 3))
      .withColumn("table", F.lit("trips"))
      .withColumn("checked_at", F.current_timestamp()))
dq.orderBy(F.desc("rows")).show(truncate=False)
dq = spark.createDataFrame(dq.collect(), dq.schema)   # tiny table: materialize it now

silver_trips = (flagged.filter(F.col("dq_issue").isNull())
                .drop("dq_issue", "source_file")
                .withColumn("start_hour", F.date_trunc("hour", "started_at"))
                .withColumn("end_hour", F.date_trunc("hour", "ended_at"))
                .withColumn("trip_date", F.to_date("started_at"))
                .withColumn("is_round_trip", F.col("start_station_id") == F.col("end_station_id")))

# Persist Silver trips now and continue from the stored table: this cuts the Spark lineage (the raw CSV
# parsing + dedup window are not recomputed by the next steps) and frees the cache.
save_table(silver_trips, "silver_trips")
flagged.unpersist()
del raw_trips, typed, deduped, flagged         # release the parsed raw CSV (matters on a small laptop)
import gc  # noqa: E402

gc.collect()
silver_trips = read_table("silver_trips", ["start_station_id", "start_station_name", "start_lat", "start_lng",
                                           "end_station_id", "end_station_name", "end_lat", "end_lng"])

# %% ---------- 4. Stations: GBFS reference + reconciliation with trip ids ----------
# GBFS `station_id` is a UUID; trips use the GBFS `short_name` (e.g. "6257.03") -> join on short_name.
info_raw = read_json(f"{BRONZE}/gbfs/station_information_*.json")
latest_ts = info_raw.agg(F.max("last_updated")).first()[0]
gbfs_stations = (info_raw.filter(F.col("last_updated") == latest_ts)
                 .select(F.explode("data.stations").alias("s"))
                 .select(F.col("s.short_name").alias("station_id"),
                         F.col("s.station_id").alias("gbfs_station_id"),
                         F.col("s.name").alias("station_name"),
                         F.col("s.lat").cast("double").alias("lat"),
                         F.col("s.lon").cast("double").alias("lon"),
                         F.col("s.capacity").cast("int").alias("capacity"),
                         F.col("s.region_id").alias("region_id"))
                 .dropDuplicates(["station_id"]))

# Stations seen in trips (some are retired / temporary and absent from today's GBFS feed).
trip_stations = (silver_trips.select(F.col("start_station_id").alias("station_id"),
                                     F.col("start_station_name").alias("name"),
                                     F.col("start_lat").alias("lat"), F.col("start_lng").alias("lon"))
                 .unionByName(silver_trips.select(F.col("end_station_id").alias("station_id"),
                                                  F.col("end_station_name").alias("name"),
                                                  F.col("end_lat").alias("lat"), F.col("end_lng").alias("lon")))
                 .groupBy("station_id")
                 .agg(F.first("name", ignorenulls=True).alias("trip_name"),
                      F.expr("percentile_approx(lat, 0.5)").alias("trip_lat"),
                      F.expr("percentile_approx(lon, 0.5)").alias("trip_lon"),
                      F.count("*").alias("trip_events")))

silver_stations = (trip_stations.join(gbfs_stations, "station_id", "full_outer")
                   .select("station_id", "gbfs_station_id",
                           F.coalesce("station_name", "trip_name").alias("station_name"),
                           F.coalesce("lat", "trip_lat").alias("lat"),
                           F.coalesce("lon", "trip_lon").alias("lon"),
                           "capacity", "region_id",
                           F.coalesce("trip_events", F.lit(0)).alias("trip_events_month"),
                           F.when(F.col("gbfs_station_id").isNull(), "trips_only")
                            .when(F.col("trip_events").isNull(), "gbfs_only")
                            .otherwise("matched").alias("match_status")))

match = silver_stations.groupBy("match_status").agg(F.count("*").alias("stations"),
                                                    F.sum("trip_events_month").alias("trip_events"))
match.show()
total_events = silver_stations.agg(F.sum("trip_events_month")).first()[0]
matched_events = silver_stations.filter("match_status = 'matched'").agg(F.sum("trip_events_month")).first()[0]
print(f"trip start/end events matched to a live GBFS station: {matched_events / total_events:.2%}")

dq = dq.union(match.select(F.concat(F.lit("station_"), "match_status").alias("check"),
                           F.col("stations").cast("long").alias("rows"),
                           F.round(F.col("trip_events") / F.lit(total_events) * 100, 3).alias("pct_of_raw"),
                           F.lit("stations").alias("table"), F.current_timestamp().alias("checked_at")))

# %% ---------- 5. Weather: Open-Meteo arrays -> one row per hour ----------
wraw = read_json(f"{BRONZE}/weather/*.json")
h = "hourly"
silver_weather = (wraw.select(F.explode(F.arrays_zip(f"{h}.time", f"{h}.temperature_2m", f"{h}.precipitation",
                                                     f"{h}.rain", f"{h}.wind_speed_10m",
                                                     f"{h}.relative_humidity_2m", f"{h}.weather_code")).alias("r"))
                  .select(F.to_timestamp("r.time", "yyyy-MM-dd'T'HH:mm").alias("weather_hour"),
                          F.col("r.temperature_2m").alias("temperature_c"),
                          F.col("r.precipitation").alias("precipitation_mm"),
                          F.col("r.rain").alias("rain_mm"),
                          F.col("r.wind_speed_10m").alias("wind_kmh"),
                          F.col("r.relative_humidity_2m").alias("humidity_pct"),
                          F.col("r.weather_code").alias("weather_code"))
                  .dropDuplicates(["weather_hour"])
                  .withColumn("is_rainy", F.col("precipitation_mm") >= 0.2))

# %% ---------- 6. Persist Silver ----------
save_table(silver_stations, "silver_stations")
save_table(silver_weather, "silver_weather_hourly")
save_table(dq, "gold_data_quality")
print("silver rows:", f"{silver_trips.count():,}")  # = valid rows of the DQ report
