# %% [markdown]
# # 05 - Advanced aggregations & calculations (Apache Spark)
# **SmartBike AI** - aggregates that go beyond GROUP BY: window functions, running sums, CUBE (OLAP),
# percentiles, spatial binning, origin-destination matrix.
#
# | Gold table | Technique | Business question |
# |---|---|---|
# | `gold_od_flows` | pairs + haversine + `dense_rank` | Busiest station-to-station links? |
# | `gold_station_daily` | rolling 7 d, `lag` (DoD, WoW), daily rank | Is demand rising or falling? |
# | `gold_rebalancing_need` / `_daily` | running sum of net flow, `min_by` | Bikes trucks must move, where, when? |
# | `gold_station_volatility` | `percentile_approx` p50/p90/p99, std-dev, `ntile` | Which stations have unpredictable peaks? |
# | `gold_usage_cube` | `CUBE` + `grouping_id` (all sub-totals) | Who rides when, on which bike? |
# | `gold_zone_hourly` | spatial binning (~1 km grid) | Which neighbourhoods are hot at each hour? |
# | `gold_weekly_trend` | weekly aggregate, growth with `lag`, cumulative sum | Is the network growing week after week? |
# | `gold_duration_distribution` | histogram buckets + cumulative % | How long are trips, members vs casual? |

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

# %% ---------- Inputs (column pruning: only what the aggregates need) ----------
trips = read_table("silver_trips", ["started_at", "start_hour", "trip_date", "start_station_id", "end_station_id",
                                    "start_lat", "start_lng", "end_lat", "end_lng", "member_casual",
                                    "rideable_type", "duration_min", "is_round_trip"])
stations = read_table("silver_stations", ["station_id", "station_name", "lat", "lon", "capacity"])
hourly = read_table("gold_station_hourly", ["station_id", "hour_ts", "date", "hour", "is_weekend",
                                            "departures", "arrivals", "net_flow", "capacity"])
trips.cache()
print(f"trips: {trips.count():,}")

# Only complete days (the grid starts with the evening of the day before the first month: trips that started
# on 31/07 and ended in August) and stations with a known capacity (GBFS reports 0 for virtual/closed stations).
full_days = (hourly.groupBy("date").agg(F.countDistinct("hour").alias("n_hours"))
             .filter("n_hours = 24").select("date"))
hourly = hourly.join(F.broadcast(full_days), "date").filter(F.col("capacity") > 0)

# %% ---------- 1. Origin-destination matrix (top flows, distance, speed) ----------
# Haversine distance between stations (km) -> average speed = how "commuter" a link is.
R_KM = 6371.0


def haversine(lat1, lon1, lat2, lon2):
    dlat, dlon = F.radians(lat2 - lat1), F.radians(lon2 - lon1)
    a = F.sin(dlat / 2) ** 2 + F.cos(F.radians(lat1)) * F.cos(F.radians(lat2)) * F.sin(dlon / 2) ** 2
    return 2 * R_KM * F.asin(F.sqrt(a))


od = (trips.filter(~F.col("is_round_trip"))
      .withColumn("dist_km", haversine(F.col("start_lat"), F.col("start_lng"), F.col("end_lat"), F.col("end_lng")))
      .groupBy("start_station_id", "end_station_id")
      .agg(F.count("*").alias("trips"),
           F.round(F.avg("duration_min"), 2).alias("avg_duration_min"),
           F.round(F.expr("percentile_approx(duration_min, 0.5)"), 2).alias("median_duration_min"),
           F.round(F.avg("dist_km"), 3).alias("avg_dist_km"),
           F.round(F.avg(F.when(F.col("member_casual") == "member", 1).otherwise(0)) * 100, 1).alias("member_pct"),
           F.round(F.avg(F.when(F.hour("started_at").between(7, 9) & ~F.dayofweek("started_at").isin(1, 7), 1)
                         .otherwise(0)) * 100, 1).alias("am_peak_pct"))
      .filter("trips >= 20")                                   # keeps the matrix readable (and small)
      .withColumn("avg_speed_kmh", F.round(F.col("avg_dist_km") / (F.col("median_duration_min") / 60), 1))
      .withColumn("rank_global", F.dense_rank().over(Window.orderBy(F.desc("trips"))))
      .withColumn("rank_from_origin",
                  F.row_number().over(Window.partitionBy("start_station_id").orderBy(F.desc("trips")))))
s_o = stations.select(F.col("station_id").alias("start_station_id"), F.col("station_name").alias("origin_name"),
                      F.col("lat").alias("origin_lat"), F.col("lon").alias("origin_lon"))
s_d = stations.select(F.col("station_id").alias("end_station_id"), F.col("station_name").alias("destination_name"),
                      F.col("lat").alias("dest_lat"), F.col("lon").alias("dest_lon"))
od = od.join(F.broadcast(s_o), "start_station_id", "left").join(F.broadcast(s_d), "end_station_id", "left")
save_table(od, "gold_od_flows")

# %% ---------- 2. Station x day with WINDOW functions (rolling 7 days, day-over-day, daily rank) ----------
w_st = Window.partitionBy("station_id").orderBy("date")
w_roll7 = w_st.rowsBetween(-6, 0)
w_day = Window.partitionBy("date")
station_daily = (hourly.groupBy("station_id", "date", "is_weekend", "capacity")
                 .agg(F.sum("departures").alias("departures"), F.sum("arrivals").alias("arrivals"),
                      F.max("departures").alias("max_hourly_departures"))
                 .withColumn("net_flow", F.col("arrivals") - F.col("departures"))
                 .withColumn("dep_rolling_7d", F.round(F.avg("departures").over(w_roll7), 1))
                 .withColumn("dep_prev_day", F.lag("departures").over(w_st))
                 .withColumn("dep_dod_pct", F.round(F.try_divide((F.col("departures") - F.col("dep_prev_day")) * 100,
                                                                 F.col("dep_prev_day")), 1))
                 .withColumn("dep_same_day_last_week", F.lag("departures", 7).over(w_st))
                 .withColumn("dep_wow_pct",
                             F.round(F.try_divide((F.col("departures") - F.col("dep_same_day_last_week")) * 100,
                                                  F.col("dep_same_day_last_week")), 1))
                 .withColumn("rank_in_day", F.dense_rank().over(w_day.orderBy(F.desc("departures"))))
                 .withColumn("share_of_network_pct",
                             F.round(F.col("departures") * 100 / F.sum("departures").over(w_day), 3))
                 .withColumn("turnover_per_dock", F.round(F.try_divide(F.col("departures") + F.col("arrivals"),
                                                                       F.col("capacity")), 2)))
save_table(station_daily, "gold_station_daily")

# %% ---------- 3. Rebalancing need: intraday running stock (cumulative net flow) ----------
# Starting each day at 0, the running sum of arrivals - departures gives the stock change hour by hour.
#   max drawdown (most negative point)  = bikes the station must hold in the morning not to run empty
#   max surplus  (highest point)        = free docks it needs not to saturate
# If drawdown > capacity (or surplus > capacity), *no* starting level avoids a failure: a truck must come.
w_cum = (Window.partitionBy("station_id", "date").orderBy("hour")
         .rowsBetween(Window.unboundedPreceding, Window.currentRow))
cum = hourly.withColumn("cum_net", F.sum("net_flow").over(w_cum))
rebal = (cum.groupBy("station_id", "date", "is_weekend", "capacity")
         .agg(F.min("cum_net").alias("max_drawdown"), F.max("cum_net").alias("max_surplus"),
              F.min_by("hour", "cum_net").alias("hour_of_min_stock"),
              F.max_by("hour", "cum_net").alias("hour_of_max_stock"))
         # the day starts at 0, so the range always includes 0
         .withColumn("swing", F.greatest("max_surplus", F.lit(0)) - F.least("max_drawdown", F.lit(0)))
         # bikes that cannot fit in the station's capacity over the day -> must be moved by truck
         .withColumn("forced_moves", F.greatest(F.col("swing") - F.col("capacity"), F.lit(0)))
         .withColumn("needs_truck", F.col("forced_moves") > 0))
save_table(rebal, "gold_rebalancing_need")

rebal_network = (rebal.groupBy("date", "is_weekend")
                 .agg(F.sum("forced_moves").alias("forced_moves"),
                      F.sum(F.col("needs_truck").cast("int")).alias("stations_needing_truck"),
                      F.count("*").alias("stations"))
                 .withColumn("pct_stations_needing_truck",
                             F.round(F.col("stations_needing_truck") * 100 / F.col("stations"), 2)))
save_table(rebal_network, "gold_rebalancing_daily")

# %% ---------- 4. Demand volatility per station (percentiles, std-dev, peak-to-average) ----------
volatility = (hourly.filter(F.col("hour").between(6, 22))
              .groupBy("station_id")
              .agg(F.round(F.avg("departures"), 3).alias("mean_dep_h"),
                   F.round(F.stddev("departures"), 3).alias("std_dep_h"),
                   F.expr("percentile_approx(departures, array(0.5, 0.9, 0.99))").alias("p"),
                   F.max("departures").alias("max_dep_h"),
                   F.round(F.avg((F.col("departures") == 0).cast("int")) * 100, 1).alias("zero_hours_pct"))
              .select("station_id", "mean_dep_h", "std_dep_h",
                      F.col("p")[0].alias("p50_dep_h"), F.col("p")[1].alias("p90_dep_h"),
                      F.col("p")[2].alias("p99_dep_h"), "max_dep_h", "zero_hours_pct")
              .withColumn("coef_variation", F.round(F.try_divide(F.col("std_dep_h"), F.col("mean_dep_h")), 3))
              .withColumn("peak_to_mean", F.round(F.try_divide(F.col("p99_dep_h"), F.col("mean_dep_h")), 2))
              .withColumn("volatility_decile", F.ntile(10).over(Window.orderBy("coef_variation")))
              .join(F.broadcast(stations), "station_id", "left"))
save_table(volatility, "gold_station_volatility")

# %% ---------- 5. OLAP cube: CUBE over day type x time of day x user type x bike type ----------
daypart = (F.when(F.hour("started_at").between(6, 9), "1. matin 6-10h")
            .when(F.hour("started_at").between(10, 15), "2. journee 10-16h")
            .when(F.hour("started_at").between(16, 19), "3. soir 16-20h").otherwise("4. nuit 20-6h"))
cube = (trips.withColumn("day_type", F.when(F.dayofweek("started_at").isin(1, 7), "week-end").otherwise("semaine"))
        .withColumn("daypart", daypart)
        .cube("day_type", "daypart", "member_casual", "rideable_type")
        .agg(F.count("*").alias("trips"),
             F.round(F.avg("duration_min"), 2).alias("avg_duration_min"),
             F.round(F.expr("percentile_approx(duration_min, 0.9)"), 1).alias("p90_duration_min"),
             F.grouping_id().alias("grouping_level"))
        .fillna("TOTAL", ["day_type", "daypart", "member_casual", "rideable_type"])
        .withColumn("share_of_total_pct",
                    F.round(F.col("trips") * 100 / F.max("trips").over(Window.partitionBy(F.lit(1))), 2)))
save_table(cube, "gold_usage_cube")

# %% ---------- 6. Spatial aggregation: ~1 km grid cells (zone x hour-of-day demand) ----------
CELL = 0.01   # degrees (~1.1 km north-south, ~0.85 km east-west in NYC)
zones = (trips.withColumn("cell_lat", F.round(F.floor(F.col("start_lat") / CELL) * CELL + CELL / 2, 4))
         .withColumn("cell_lon", F.round(F.floor(F.col("start_lng") / CELL) * CELL + CELL / 2, 4))
         .withColumn("hour", F.hour("started_at"))
         .withColumn("is_weekend", F.dayofweek("started_at").isin(1, 7))
         .groupBy("cell_lat", "cell_lon", "hour", "is_weekend")
         .agg(F.count("*").alias("departures"), F.countDistinct("start_station_id").alias("stations"))
         .withColumn("zone_id", F.concat_ws("_", F.col("cell_lat").cast("string"), F.col("cell_lon").cast("string"))))
n_days_type = trips.groupBy(F.dayofweek("trip_date").isin(1, 7).alias("is_weekend")) \
                   .agg(F.countDistinct("trip_date").alias("n_days"))
zones = (zones.join(F.broadcast(n_days_type), "is_weekend")
         .withColumn("avg_departures_per_day", F.round(F.col("departures") / F.col("n_days"), 2))
         .withColumn("zone_rank_in_hour",
                     F.dense_rank().over(Window.partitionBy("hour", "is_weekend").orderBy(F.desc("departures")))))
save_table(zones, "gold_zone_hourly")

# %% ---------- 7. Weekly trend with growth (lag over weeks) ----------
w_week = Window.orderBy("week_start")
weekly = (trips.groupBy(F.date_trunc("week", "trip_date").cast("date").alias("week_start"))
          .agg(F.count("*").alias("trips"), F.countDistinct("trip_date").alias("days"),
               F.round(F.avg("duration_min"), 2).alias("avg_duration_min"),
               F.round(F.avg(F.when(F.col("rideable_type") == "electric_bike", 1).otherwise(0)) * 100, 1)
               .alias("ebike_share_pct"))
          .withColumn("trips_per_day", F.round(F.col("trips") / F.col("days"), 0))
          .withColumn("wow_growth_pct", F.round((F.col("trips_per_day") / F.lag("trips_per_day").over(w_week) - 1)
                                                * 100, 1))
          .withColumn("cumulative_trips", F.sum("trips").over(w_week.rowsBetween(Window.unboundedPreceding, 0))))
save_table(weekly, "gold_weekly_trend")

# %% ---------- 8. Trip duration distribution (histogram buckets per user type) ----------
bucket = (F.when(F.col("duration_min") < 5, "01. <5 min").when(F.col("duration_min") < 10, "02. 5-10 min")
           .when(F.col("duration_min") < 15, "03. 10-15 min").when(F.col("duration_min") < 20, "04. 15-20 min")
           .when(F.col("duration_min") < 30, "05. 20-30 min").when(F.col("duration_min") < 45, "06. 30-45 min")
           .when(F.col("duration_min") < 60, "07. 45-60 min").otherwise("08. >= 60 min"))
dist = (trips.withColumn("duration_bucket", bucket)
        .groupBy("member_casual", "duration_bucket").agg(F.count("*").alias("trips"))
        .withColumn("pct_of_user_type",
                    F.round(F.col("trips") * 100 / F.sum("trips").over(Window.partitionBy("member_casual")), 2))
        .withColumn("cumulative_pct",
                    F.round(F.sum("pct_of_user_type").over(Window.partitionBy("member_casual").orderBy("duration_bucket")
                                                            .rowsBetween(Window.unboundedPreceding, 0)), 2)))
save_table(dist, "gold_duration_distribution")

trips.unpersist()
print("advanced aggregates done")
