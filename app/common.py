"""Shared data access for the SmartBike API and dashboard.

Reads the Gold tables (local Parquet folders, or Delta tables exported from the Fabric Lakehouse to the same
folder) and loads the production model from the MLflow registry (alias `champion`).
"""
from __future__ import annotations

import functools
import os
import pathlib

import numpy as np
import pandas as pd

ROOT = pathlib.Path(os.environ.get("SMARTBIKE_ROOT", pathlib.Path(__file__).resolve().parents[1]))
TABLES = ROOT / "data" / "lakehouse" / "Tables"
MODEL_NAME = "smartbike_demand_model"
NY_TZ = "America/New_York"


@functools.lru_cache(maxsize=32)
def table(name: str, columns: tuple[str, ...] | None = None) -> pd.DataFrame:
    path = TABLES / name
    if not path.exists():
        raise FileNotFoundError(f"table {name} not found in {TABLES} - run the notebooks first")
    return pd.read_parquet(path, columns=list(columns) if columns else None)


def available_tables() -> list[str]:
    return sorted(p.name for p in TABLES.iterdir() if p.is_dir()) if TABLES.exists() else []


@functools.lru_cache(maxsize=2)
def model(target: str):
    """Production model (MLflow alias `champion`), falling back to the latest registered version."""
    import mlflow
    mlflow.set_tracking_uri(os.environ.get("MLFLOW_TRACKING_URI", f"sqlite:///{ROOT.as_posix()}/mlruns.db"))
    for uri in (f"models:/{MODEL_NAME}_{target}@champion", f"models:/{MODEL_NAME}_{target}/latest"):
        try:
            return mlflow.sklearn.load_model(uri), uri
        except Exception:  # noqa: BLE001 - try the next URI
            continue
    raise RuntimeError(f"no registered model {MODEL_NAME}_{target} - run notebook 03 first")


def build_features(station_id: str, hour_ts: pd.Timestamp, weather: dict) -> pd.DataFrame:
    """Same features as training (notebook 03) for one station and one local hour."""
    st = table("silver_stations")
    row = st[st.station_id == station_id]
    if row.empty:
        raise KeyError(station_id)
    row = row.iloc[0]
    hour_ts = pd.Timestamp(hour_ts).floor("h")
    dow = (hour_ts.day_of_week + 1) % 7 + 1          # Spark convention: 1 = Sunday ... 7 = Saturday
    is_weekend = int(dow in (1, 7))
    f = {"station_id": station_id, "hour": hour_ts.hour, "dow": dow, "is_weekend": is_weekend,
         "temperature_c": weather.get("temperature_c", 15.0), "precipitation_mm": weather.get("precipitation_mm", 0.0),
         "wind_kmh": weather.get("wind_kmh", 10.0), "humidity_pct": weather.get("humidity_pct", 60.0),
         "capacity": float(row.capacity) if pd.notna(row.capacity) else 0.0, "lat": row.lat, "lon": row.lon}
    frame = pd.DataFrame([f])
    enc = table("gold_station_hour_encoding")
    enc_st = table("gold_station_encoding")
    frame = frame.merge(enc, on=["station_id", "hour", "is_weekend"], how="left").merge(enc_st, on="station_id",
                                                                                       how="left")
    enc_cols = [c for c in frame.columns if c.startswith("enc_")]
    frame[enc_cols] = frame[enc_cols].fillna(0.0)
    return frame


def predict(station_id: str, hour_ts: pd.Timestamp, weather: dict | None = None) -> dict:
    frame = build_features(station_id, hour_ts, weather or {})
    out = {"station_id": station_id, "hour_local": str(pd.Timestamp(hour_ts).floor("h"))}
    for target in ("departures", "arrivals"):
        m, uri = model(target)
        out[f"pred_{target}"] = round(float(np.clip(m.predict(frame[list(m.feature_names_in_)]), 0, None)[0]), 2)
        out[f"model_{target}"] = uri
    out["pred_net_flow"] = round(out["pred_arrivals"] - out["pred_departures"], 2)
    return out
