"""SmartBike AI - REST API (FastAPI): exposes Gold insights and model predictions to other applications.

Run:   uvicorn app.api:app --port 8000        (docs: http://localhost:8000/docs)
Auth:  if the environment variable SMARTBIKE_API_KEY is set (in Azure: an App Service setting that is a
       Key Vault reference), every /v1 call must send the header `X-API-Key`. The key is never in the code.
"""
from __future__ import annotations

import os
import secrets
from datetime import datetime

import pandas as pd
import requests
from fastapi import Depends, FastAPI, Header, HTTPException, Query

from app import common

app = FastAPI(title="SmartBike AI API", version="1.0.0",
              description="Station availability risk, demand forecasts and network KPIs (Citi Bike NYC).")


def require_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = os.environ.get("SMARTBIKE_API_KEY")
    if expected and not (x_api_key and secrets.compare_digest(x_api_key, expected)):
        raise HTTPException(status_code=401, detail="missing or invalid X-API-Key")


def records(df: pd.DataFrame) -> list[dict]:
    return df.astype(object).where(df.notna(), None).to_dict(orient="records")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "tables": common.available_tables()}


@app.get("/v1/kpis", dependencies=[Depends(require_key)])
def kpis() -> dict:
    d = common.table("gold_daily_kpis")
    return {"days": int(len(d)), "total_trips": int(d.trips.sum()), "avg_trips_per_day": round(float(d.trips.mean())),
            "avg_duration_min": round(float((d.avg_duration_min * d.trips).sum() / d.trips.sum()), 2),
            "member_share_pct": round(float((d.member_share_pct * d.trips).sum() / d.trips.sum()), 1),
            "ebike_share_pct": round(float((d.ebike_share_pct * d.trips).sum() / d.trips.sum()), 1),
            "period": [str(d.date.min()), str(d.date.max())]}


@app.get("/v1/priority", dependencies=[Depends(require_key)])
def priority(limit: int = Query(20, ge=1, le=500), risk: str | None = None) -> list[dict]:
    """Stations to rebalance first (latest run of notebook 04)."""
    p = common.table("gold_station_priority").sort_values("priority_rank")
    if risk:
        p = p[p.risk == risk]
    cols = ["priority_rank", "station_id", "station_name", "num_bikes_available", "num_docks_available", "capacity",
            "pred_departures", "pred_arrivals", "projected_bikes", "risk", "action", "bikes_to_move",
            "snapshot_time_local"]
    return records(p[cols].head(limit))


@app.get("/v1/stations/{station_id}", dependencies=[Depends(require_key)])
def station(station_id: str) -> dict:
    prof = common.table("gold_station_profile")
    row = prof[prof.station_id == station_id]
    if row.empty:
        raise HTTPException(404, f"unknown station {station_id}")
    out = records(row)[0]
    vol = common.table("gold_station_volatility")
    v = vol[vol.station_id == station_id]
    if not v.empty:
        out["volatility"] = records(v[["p50_dep_h", "p90_dep_h", "p99_dep_h", "coef_variation"]])[0]
    return out


@app.get("/v1/stations/{station_id}/live", dependencies=[Depends(require_key)])
def station_live(station_id: str) -> dict:
    """Current availability straight from the public GBFS feed."""
    st = common.table("silver_stations")
    if not (st.station_id == station_id).any():
        raise HTTPException(404, f"unknown station {station_id}")
    try:
        live = common.live_status()
    except requests.RequestException as exc:
        raise HTTPException(503, f"GBFS feed unavailable: {exc}") from exc
    row = live[live.station_id == station_id]
    if row.empty:
        raise HTTPException(404, "station not in the live feed")
    r = row.iloc[0]
    return {"station_id": station_id, "station_name": r.station_name,
            "bikes_available": int(r.num_bikes_available), "docks_available": int(r.num_docks_available),
            "last_reported_utc": r.last_reported_utc.isoformat()}


@app.get("/v1/predict", dependencies=[Depends(require_key)])
def predict(station_id: str, hour: datetime | None = None, temperature_c: float = 15.0,
            precipitation_mm: float = 0.0, wind_kmh: float = 10.0, humidity_pct: float = 60.0) -> dict:
    """Expected departures / arrivals for one station and one local hour (champion model)."""
    hour = pd.Timestamp(hour) if hour else pd.Timestamp.now(tz=common.NY_TZ).tz_localize(None)
    try:
        return common.predict(station_id, hour, {"temperature_c": temperature_c, "precipitation_mm": precipitation_mm,
                                                 "wind_kmh": wind_kmh, "humidity_pct": humidity_pct})
    except KeyError:
        raise HTTPException(404, f"unknown station {station_id}") from None


@app.get("/v1/od-flows", dependencies=[Depends(require_key)])
def od_flows(limit: int = Query(20, ge=1, le=500), origin: str | None = None) -> list[dict]:
    od = common.table("gold_od_flows")
    if origin:
        od = od[od.start_station_id == origin]
    return records(od.sort_values("trips", ascending=False).head(limit))


@app.get("/v1/model", dependencies=[Depends(require_key)])
def model_info() -> dict:
    out = {"metrics": records(common.table("gold_ml_metrics").drop(columns=["model_uri"], errors="ignore"))}
    try:
        out["promotion_log"] = records(common.table("gold_model_promotion_log"))
    except FileNotFoundError:
        pass
    return out
