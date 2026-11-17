"""Model input features - one implementation for training-time parity everywhere the model is scored.

The demand models (notebook 03) are trained on one row per (station, local hour) with calendar, weather, station
and target-encoding features. Scoring must rebuild *exactly* the same columns; this module is the single place that
does it (used by notebook 04, the API, the operator app and the alert backtest).
"""
from __future__ import annotations

import pandas as pd

WEATHER_DEFAULTS = {"temperature_c": 15.0, "precipitation_mm": 0.0, "wind_kmh": 10.0, "humidity_pct": 60.0}


def spark_dayofweek(ts: pd.Series) -> pd.Series:
    """Spark `dayofweek` convention used at training time: 1 = Sunday ... 7 = Saturday."""
    return (ts.dt.day_of_week + 1) % 7 + 1


def build_feature_grid(stations: pd.DataFrame, hours: pd.DataFrame, enc_station_hour: pd.DataFrame,
                       enc_station: pd.DataFrame) -> pd.DataFrame:
    """Cross stations x hours and attach every model feature.

    stations          station_id, capacity, lat, lon (+ any extra columns, kept)
    hours             hour_ts (local, naive) + optional weather columns; missing weather -> WEATHER_DEFAULTS
    enc_station_hour  station_id, hour, is_weekend, enc_* (training-period target encodings)
    enc_station       station_id, enc_* (station-level encodings)

    Unknown stations / hours get encodings of 0, exactly like at training time for unseen keys.
    """
    if stations.empty or hours.empty:
        raise ValueError("stations and hours must both be non-empty")
    hours = hours.copy()
    hours["hour_ts"] = pd.to_datetime(hours["hour_ts"]).dt.floor("h")
    for col, default in WEATHER_DEFAULTS.items():
        hours[col] = hours[col].fillna(default) if col in hours else default
    grid = stations.merge(hours, how="cross")
    grid["hour"] = grid.hour_ts.dt.hour
    grid["dow"] = spark_dayofweek(grid.hour_ts)
    grid["is_weekend"] = grid["dow"].isin([1, 7]).astype(int)
    grid["capacity"] = grid["capacity"].fillna(0).astype(float)
    grid = (grid.merge(enc_station_hour, on=["station_id", "hour", "is_weekend"], how="left")
                .merge(enc_station, on="station_id", how="left"))
    enc_cols = [c for c in grid.columns if c.startswith("enc_")]
    grid[enc_cols] = grid[enc_cols].fillna(0.0)
    return grid
