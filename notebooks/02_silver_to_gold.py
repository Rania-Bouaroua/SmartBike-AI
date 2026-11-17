# %% [markdown]
# # 02 - Silver -> Gold (business aggregates for Power BI and ML)
#
# | Gold table | Grain | Used by |
# |---|---|---|
# | `gold_station_hourly` | station x hour (full grid, zeros filled) + weather + calendar | ML features, Power BI |
# | `gold_daily_kpis` | day | KPI cards, trend line |
# | `gold_station_profile` | station | map, top stations, imbalance (sources/sinks) |
# | `gold_hourly_pattern` | day-of-week x hour | heatmap (peak hours) |
# | `gold_weather_effect` | weather bucket | weather impact visual |

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

trips = read_table("silver_trips", ["start_station_id", "end_station_id", "start_hour", "end_hour", "started_at",
                                    "trip_date", "rideable_type", "member_casual", "duration_min"])
stations = read_table("silver_stations")
weather = read_table("silver_weather_hourly")

# %% ---------- 1. Departures / arrivals per station-hour ----------
dep = trips.groupBy(F.col("start_station_id").alias("station_id"), F.col("start_hour").alias("hour_ts")) \
           .agg(F.count("*").alias("departures"),
                F.sum(F.when(F.col("rideable_type") == "electric_bike", 1).otherwise(0)).alias("ebike_departures"),
                F.sum(F.when(F.col("member_casual") == "member", 1).otherwise(0)).alias("member_departures"))
arr = trips.groupBy(F.col("end_station_id").alias("station_id"), F.col("end_hour").alias("hour_ts")) \
           .agg(F.count("*").alias("arrivals"))

# Full grid: an hour with no trip is a real 0 for the model, not a missing row.
# Built inside Spark: collecting min/max to Python would convert them to the *machine* time zone
# and shift the whole grid (bug found while testing on a laptop in UTC+1).
hours = trips.agg(F.explode(F.sequence(F.min("start_hour"), F.max("start_hour"),
                                       F.expr("INTERVAL 1 HOUR"))).alias("hour_ts"))
active = (stations.filter("trip_events_month >= 30")   # ignore stations with almost no activity (temporary/test)
          .select("station_id", "station_name", "lat", "lon", "capacity"))

station_hourly = (active.crossJoin(hours)
                  .join(dep, ["station_id", "hour_ts"], "left")
                  .join(arr, ["station_id", "hour_ts"], "left")
                  .fillna(0, ["departures", "arrivals", "ebike_departures", "member_departures"])
                  .withColumn("net_flow", F.col("arrivals") - F.col("departures"))
                  .withColumn("date", F.to_date("hour_ts"))
                  .withColumn("hour", F.hour("hour_ts"))
                  .withColumn("dow", F.dayofweek("hour_ts"))            # 1 = Sunday ... 7 = Saturday
                  .withColumn("is_weekend", F.col("dow").isin(1, 7))
                  .join(weather, F.col("hour_ts") == F.col("weather_hour"), "left").drop("weather_hour"))

save_table(station_hourly, "gold_station_hourly")
station_hourly = read_table("gold_station_hourly")   # reuse the stored table (cuts lineage)

# %% ---------- 2. Daily KPIs ----------
daily_weather = weather.groupBy(F.to_date("weather_hour").alias("date")).agg(
    F.round(F.avg("temperature_c"), 1).alias("avg_temp_c"),
    F.round(F.sum("precipitation_mm"), 1).alias("total_precip_mm"))

daily = (trips.groupBy(F.col("trip_date").alias("date"))
         .agg(F.count("*").alias("trips"),
              F.round(F.avg("duration_min"), 2).alias("avg_duration_min"),
              F.round(F.avg(F.when(F.col("member_casual") == "member", 1).otherwise(0)) * 100, 1).alias("member_share_pct"),
              F.round(F.avg(F.when(F.col("rideable_type") == "electric_bike", 1).otherwise(0)) * 100, 1).alias("ebike_share_pct"),
              F.countDistinct("start_station_id").alias("active_stations"))
         .join(daily_weather, "date", "left")
         .withColumn("day_name", F.date_format("date", "EEEE"))
         .withColumn("is_weekend", F.dayofweek("date").isin(1, 7)))
save_table(daily, "gold_daily_kpis")

# %% ---------- 3. Station profile: who empties, who fills up? ----------
n_days = trips.select("trip_date").distinct().count()


def window_flow(h0, h1, wkday_only=True):
    cond = F.col("hour").between(h0, h1) & (~F.col("is_weekend") if wkday_only else F.lit(True))
    return F.sum(F.when(cond, F.col("net_flow")).otherwise(0))


peak = (station_hourly.groupBy("station_id", "hour").agg(F.sum("departures").alias("d"))
        .withColumn("rk", F.row_number().over(Window.partitionBy("station_id").orderBy(F.desc("d"))))
        .filter("rk = 1").select("station_id", F.col("hour").alias("peak_departure_hour")))

profile = (station_hourly.groupBy("station_id", "station_name", "lat", "lon", "capacity")
           .agg(F.sum("departures").alias("departures"),
                F.sum("arrivals").alias("arrivals"),
                F.sum("ebike_departures").alias("ebike_departures"),
                (window_flow(7, 9) / F.lit(n_days)).alias("am_peak_net_flow_per_day"),
                (window_flow(16, 19) / F.lit(n_days)).alias("pm_peak_net_flow_per_day"))
           .join(peak, "station_id", "left")
           .withColumn("net_flow", F.col("arrivals") - F.col("departures"))
           .withColumn("avg_daily_departures", F.round(F.col("departures") / n_days, 1))
           .withColumn("imbalance_ratio", F.round(F.try_divide(F.col("net_flow"), F.col("arrivals") + F.col("departures")), 3))
           # Over a whole month departures ~= arrivals almost everywhere (trucks rebalance and riders come back),
           # so the role is based on the weekday morning peak (7h-10h) relative to the station capacity.
           .withColumn("am_flow_pct_capacity",
                       F.round(F.try_divide(F.col("am_peak_net_flow_per_day") * 100, F.col("capacity")), 1))
           .withColumn("station_role",
                       F.when(F.col("am_flow_pct_capacity") <= -15, "source le matin (se vide)")
                        .when(F.col("am_flow_pct_capacity") >= 15, "puits le matin (se remplit)")
                        .otherwise("equilibree"))
           # rule of thumb: a weekday AM-peak loss bigger than 30% of capacity empties the station without rebalancing
           .withColumn("am_peak_risk",
                       F.when(F.col("capacity").isNull() | (F.col("capacity") == 0), "inconnu")
                        .when(F.col("am_peak_net_flow_per_day") <= -0.3 * F.col("capacity"), "risque vide")
                        .when(F.col("am_peak_net_flow_per_day") >= 0.3 * F.col("capacity"), "risque saturee")
                        .otherwise("faible")))
save_table(profile, "gold_station_profile")

# %% ---------- 4. Day-of-week x hour pattern (heatmap) ----------
pattern = (trips.withColumn("dow", F.dayofweek("started_at")).withColumn("day_name", F.date_format("started_at", "EEE"))
           .withColumn("hour", F.hour("started_at"))
           .groupBy("dow", "day_name", "hour")
           .agg(F.count("*").alias("trips"),
                F.sum(F.when(F.col("member_casual") == "member", 1).otherwise(0)).alias("member_trips"))
           .join(trips.withColumn("dow", F.dayofweek("trip_date")).groupBy("dow")
                 .agg(F.countDistinct("trip_date").alias("n_days")), "dow")
           .withColumn("avg_trips_per_hour", F.round(F.col("trips") / F.col("n_days"), 1)))
save_table(pattern, "gold_hourly_pattern")

# %% ---------- 5. Weather effect (system-wide trips per hour by weather bucket) ----------
system_hourly = station_hourly.groupBy("hour_ts", "hour", "is_weekend", "temperature_c", "precipitation_mm") \
                              .agg(F.sum("departures").alias("trips"))
weather_effect = (system_hourly
                  .withColumn("rain_bucket", F.when(F.col("precipitation_mm") < 0.2, "1. sec")
                              .when(F.col("precipitation_mm") < 2, "2. pluie faible").otherwise("3. pluie forte"))
                  .withColumn("temp_bucket", F.when(F.col("temperature_c") < 10, "1. < 10 C")
                              .when(F.col("temperature_c") < 18, "2. 10-18 C").otherwise("3. >= 18 C"))
                  .withColumn("daypart", F.when(F.col("hour").between(7, 19), "jour 7h-19h").otherwise("nuit"))
                  .groupBy("rain_bucket", "temp_bucket", "daypart", "is_weekend")
                  .agg(F.count("*").alias("hours"), F.round(F.avg("trips"), 1).alias("avg_trips_per_hour")))
save_table(weather_effect, "gold_weather_effect")

print("gold rows station_hourly:", f"{station_hourly.count():,}")
