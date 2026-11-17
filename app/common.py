"""Shared data access for the SmartBike API and dashboard.

Reads the Gold tables (local Parquet folders, or Delta tables exported from the Fabric Lakehouse to the same
folder) and loads the production model from the MLflow registry (alias `champion`).
"""
from __future__ import annotations

import functools
import os
import pathlib
import time

import numpy as np
import pandas as pd

from smartbike.contracts import validate
from smartbike.features import build_feature_grid

ROOT = pathlib.Path(os.environ.get("SMARTBIKE_ROOT", pathlib.Path(__file__).resolve().parents[1]))
TABLES = ROOT / "data" / "lakehouse" / "Tables"
MODEL_NAME = "smartbike_demand_model"
NY_TZ = "America/New_York"


# File-system metadata is cached for a few seconds: on network / container bind mounts every stat() costs
# milliseconds and was dominating request latency (benchmark: /health 110 ms -> ~2 ms). A rewritten table is
# therefore picked up at most METADATA_TTL_S seconds later.
METADATA_TTL_S = float(os.environ.get("SMARTBIKE_METADATA_TTL_S", "10"))
_meta_cache: dict[tuple, tuple[float, object]] = {}


def _ttl(key: tuple, compute):
    now = time.monotonic()
    hit = _meta_cache.get(key)
    if hit and now - hit[0] < METADATA_TTL_S:
        return hit[1]
    value = compute()
    _meta_cache[key] = (now, value)
    return value


def _version_uncached(name: str) -> float:
    path = TABLES / name
    if not path.exists():
        raise FileNotFoundError(f"table {name} not found in {TABLES} - run the notebooks first")
    return max((f.stat().st_mtime for f in path.rglob("*.parquet")), default=path.stat().st_mtime)


def table_version(name: str) -> float:
    """Last modification time of a table's files: changes whenever a notebook rewrites the table."""
    return _ttl(("version", str(TABLES), name), lambda: _version_uncached(name))


@functools.lru_cache(maxsize=64)
def _read_table(name: str, columns: tuple[str, ...] | None, version: float) -> pd.DataFrame:
    df = pd.read_parquet(TABLES / name, columns=list(columns) if columns else None)
    if columns is None and os.environ.get("SMARTBIKE_VALIDATE", "1") == "1":
        validate(name, df)          # once per table version (cached); raises DataContractError
    return df


def table(name: str, columns: tuple[str, ...] | None = None) -> pd.DataFrame:
    """Read a Gold/Silver table; cached, but re-read as soon as the pipeline rewrites it (long-running API/app)."""
    return _read_table(name, columns, table_version(name))


def _clear_caches() -> None:
    _read_table.cache_clear()
    _rows_by_station.cache_clear()
    _meta_cache.clear()


table.cache_clear = _clear_caches  # type: ignore[attr-defined]  (tests reset the caches)


GBFS_STATUS_URL = "https://gbfs.citibikenyc.com/gbfs/en/station_status.json"


def live_status(timeout: float = 20) -> pd.DataFrame:
    """Current availability of every station, straight from the public GBFS feed, joined to our station ids.

    Returns one row per station with bikes/docks available, `is_empty` / `is_full` flags and `last_reported_utc`.
    Raises `requests.RequestException` when the feed is unreachable (callers decide how to degrade).
    """
    import requests

    resp = requests.get(GBFS_STATUS_URL, timeout=timeout)
    resp.raise_for_status()
    live = pd.DataFrame(resp.json()["data"]["stations"])
    live = live[["station_id", "num_bikes_available", "num_docks_available", "is_renting", "last_reported"]].rename(
        columns={"station_id": "gbfs_station_id"})
    ref = table("silver_stations")[["station_id", "gbfs_station_id", "station_name", "capacity"]]
    out = ref.merge(live, on="gbfs_station_id", how="inner")
    out["last_reported_utc"] = pd.to_datetime(out.last_reported, unit="s", utc=True)
    active = out.is_renting == 1
    out["is_empty"] = active & (out.num_bikes_available == 0)
    out["is_full"] = active & (out.num_docks_available == 0)
    return out.drop(columns="last_reported")


def available_tables() -> list[str]:
    return _ttl(("tables", str(TABLES)), lambda: sorted(p.name for p in TABLES.iterdir() if p.is_dir())
                if TABLES.exists() else [])


@functools.lru_cache(maxsize=2)
def model(target: str):
    """Production model.

    * SMARTBIKE_MODEL_DIR set (containers, App Service): the exported serving bundle (scripts/export_models.py),
      identified as `serving:<registered model>/v<version>` from its manifest;
    * otherwise the MLflow registry alias `champion`, falling back to the latest registered version.
    """
    import mlflow

    bundle = os.environ.get("SMARTBIKE_MODEL_DIR")
    if bundle:
        import json
        info = json.loads((pathlib.Path(bundle) / "manifest.json").read_text(encoding="utf-8"))["models"][target]
        return (mlflow.sklearn.load_model(str(pathlib.Path(bundle) / target)),
                f"serving:{info['registered_model']}/v{info['version']}")
    mlflow.set_tracking_uri(os.environ.get("MLFLOW_TRACKING_URI", f"sqlite:///{ROOT.as_posix()}/mlruns.db"))
    for uri in (f"models:/{MODEL_NAME}_{target}@champion", f"models:/{MODEL_NAME}_{target}/latest"):
        try:
            return mlflow.sklearn.load_model(uri), uri
        except Exception:  # noqa: BLE001 - try the next URI
            continue
    raise RuntimeError(f"no registered model {MODEL_NAME}_{target} - run notebook 03 first")


@functools.lru_cache(maxsize=8)
def _rows_by_station(name: str, version: float) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """Table split by station_id, built once per table version: one-station lookups become O(1) instead of a merge
    against the whole table (profiling: the merges were half of the /v1/predict latency)."""
    df = table(name)
    return {k: g for k, g in df.groupby("station_id", sort=False)}, df.iloc[:0]


def station_rows(name: str, station_id: str) -> pd.DataFrame:
    groups, empty = _rows_by_station(name, table_version(name))
    return groups.get(station_id, empty)


def build_features(station_id: str, hour_ts: pd.Timestamp, weather: dict) -> pd.DataFrame:
    """Model features for one station and one local hour (thin wrapper over smartbike.features)."""
    row = station_rows("silver_stations", station_id)
    if row.empty:
        raise KeyError(station_id)
    hours = pd.DataFrame([{"hour_ts": pd.Timestamp(hour_ts), **weather}])
    return build_feature_grid(row[["station_id", "capacity", "lat", "lon"]], hours,
                              station_rows("gold_station_hour_encoding", station_id),
                              station_rows("gold_station_encoding", station_id))


def score(grid: pd.DataFrame) -> pd.DataFrame:
    """Add pred_departures / pred_arrivals (champion models, clipped at 0) to a feature grid."""
    out = grid.copy()
    for target in ("departures", "arrivals"):
        m, _ = model(target)
        out[f"pred_{target}"] = np.clip(m.predict(out[list(m.feature_names_in_)]), 0, None)
    return out


def hourly_rates(stations: pd.DataFrame, hours: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Expected departures / arrivals per station (rows, in `stations` order) and hour (columns, in `hours` order)."""
    grid = score(build_feature_grid(stations[["station_id", "capacity", "lat", "lon"]], hours,
                                    table("gold_station_hour_encoding"), table("gold_station_encoding")))
    order = pd.to_datetime(hours["hour_ts"]).dt.floor("h").tolist()
    dep = grid.pivot_table(index="station_id", columns="hour_ts", values="pred_departures", aggfunc="first")
    arr = grid.pivot_table(index="station_id", columns="hour_ts", values="pred_arrivals", aggfunc="first")
    ids = stations["station_id"].tolist()
    return dep.loc[ids, order].to_numpy(), arr.loc[ids, order].to_numpy()


def predict(station_id: str, hour_ts: pd.Timestamp, weather: dict | None = None) -> dict:
    frame = score(build_features(station_id, hour_ts, weather or {}))
    out = {"station_id": station_id, "hour_local": str(pd.Timestamp(hour_ts).floor("h"))}
    for target in ("departures", "arrivals"):
        out[f"pred_{target}"] = round(float(frame[f"pred_{target}"].iloc[0]), 2)
        out[f"model_{target}"] = model(target)[1]
    out["pred_net_flow"] = round(out["pred_arrivals"] - out["pred_departures"], 2)
    return out
