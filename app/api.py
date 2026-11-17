"""SmartBike AI - REST API (FastAPI): exposes Gold insights, stock-out risk, truck plans and model predictions.

Run:   uvicorn app.api:app --port 8000        (docs: http://localhost:8000/docs)
Auth:  if the environment variable SMARTBIKE_API_KEY is set (in Azure: an App Service setting that is a
       Key Vault reference), every /v1 call must send the header `X-API-Key`. The key is never in the code.
Ops:   every response carries X-Request-ID and Server-Timing; one JSON log line per request (method, path, status,
       duration) for Log Analytics. Missing tables / broken data contracts answer 503 with an explicit reason.
"""
from __future__ import annotations

import functools
import json
import logging
import os
import secrets
import time
import uuid
from datetime import UTC, datetime
from typing import Literal

import pandas as pd
import requests
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app import common
from smartbike.contracts import DataContractError

log = logging.getLogger("smartbike.api")
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)

app = FastAPI(title="SmartBike AI API", version="1.1.0",
              description="Station stock-out risk, rebalancing truck plans, demand forecasts and network KPIs "
                          "(Citi Bike NYC).")

RiskLabel = Literal["vide maintenant", "saturee maintenant", "risque vide", "risque saturee", "ok"]


# ------------------------------------------------------------------ cross-cutting concerns
@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
    start = time.perf_counter()
    response = await call_next(request)
    ms = (time.perf_counter() - start) * 1000
    response.headers["X-Request-ID"] = rid
    response.headers["Server-Timing"] = f"app;dur={ms:.1f}"
    log.info(json.dumps({"ts": datetime.now(UTC).isoformat(timespec="milliseconds"), "request_id": rid,
                         "method": request.method, "path": request.url.path, "status": response.status_code,
                         "duration_ms": round(ms, 1)}))
    return response


@app.exception_handler(DataContractError)
async def contract_violation(_: Request, exc: DataContractError):
    return JSONResponse(status_code=503, content={"detail": "data contract violated", "reason": str(exc)})


@app.exception_handler(FileNotFoundError)
async def table_missing(_: Request, exc: FileNotFoundError):
    return JSONResponse(status_code=503, content={"detail": "table not available", "reason": str(exc)})


def require_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = os.environ.get("SMARTBIKE_API_KEY")
    if expected and not (x_api_key and secrets.compare_digest(x_api_key, expected)):
        raise HTTPException(status_code=401, detail="missing or invalid X-API-Key")


def records(df: pd.DataFrame) -> list[dict]:
    return df.astype(object).where(df.notna(), None).to_dict(orient="records")


# ------------------------------------------------------------------ response models (OpenAPI documentation)
class Health(BaseModel):
    status: str
    tables: list[str]


class Prediction(BaseModel):
    station_id: str
    hour_local: str
    pred_departures: float
    pred_arrivals: float
    pred_net_flow: float
    model_departures: str
    model_arrivals: str


# ------------------------------------------------------------------ endpoints
@app.get("/health", response_model=Health)
async def health() -> dict:
    """Liveness: cheap, no table read."""
    return {"status": "ok", "tables": common.available_tables()}


@app.get("/v1/status", dependencies=[Depends(require_key)])
def status() -> dict:
    """Readiness / freshness: which data and which models are being served."""
    p = common.table("gold_station_priority")
    out = {"snapshot_time_local": str(p.snapshot_time_local.iloc[0]), "horizon_hours": int(p.horizon_hours.iloc[0]),
           "risk_method": str(p.risk_method.iloc[0]) if "risk_method" in p else "point",
           "models": {t: common.model(t)[1] for t in ("departures", "arrivals")},
           "table_versions_utc": {}}
    for name in ("gold_station_priority", "gold_truck_routes", "gold_alert_backtest", "gold_daily_kpis"):
        try:
            out["table_versions_utc"][name] = pd.Timestamp(common.table_version(name), unit="s", tz="UTC").isoformat()
        except FileNotFoundError:
            out["table_versions_utc"][name] = None
    return out


@app.get("/v1/kpis", dependencies=[Depends(require_key)])
def kpis() -> dict:
    d = common.table("gold_daily_kpis")
    return {"days": int(len(d)), "total_trips": int(d.trips.sum()), "avg_trips_per_day": round(float(d.trips.mean())),
            "avg_duration_min": round(float((d.avg_duration_min * d.trips).sum() / d.trips.sum()), 2),
            "member_share_pct": round(float((d.member_share_pct * d.trips).sum() / d.trips.sum()), 1),
            "ebike_share_pct": round(float((d.ebike_share_pct * d.trips).sum() / d.trips.sum()), 1),
            "period": [str(d.date.min()), str(d.date.max())]}


@app.get("/v1/priority", dependencies=[Depends(require_key)])
def priority(limit: int = Query(20, ge=1, le=500), risk: RiskLabel | None = None) -> list[dict]:
    """Stations to rebalance first (latest run of notebook 04), with P(empty) / P(full) within the horizon."""
    p = common.table("gold_station_priority").sort_values("priority_rank")
    if risk:
        p = p[p.risk == risk]
    cols = ["priority_rank", "station_id", "station_name", "num_bikes_available", "num_docks_available", "capacity",
            "pred_departures", "pred_arrivals", "projected_bikes", "p_empty", "p_full", "risk", "action",
            "bikes_to_move", "snapshot_time_local"]
    return records(p[[c for c in cols if c in p.columns]].head(limit))


@functools.lru_cache(maxsize=16)
def _plan(version: float, n_trucks: int, capacity: int, shift: int, top_n: int) -> tuple[list[dict], dict]:
    from smartbike.routing import FleetConfig, candidates_from_priority, optimise_plan
    cfg = FleetConfig(n_trucks=n_trucks, truck_capacity=capacity, shift_minutes=shift, time_limit_s=5)
    plan, kpi = optimise_plan(candidates_from_priority(common.table("gold_station_priority"), top_n), cfg)
    return records(plan), kpi


@app.get("/v1/rebalancing-plan", dependencies=[Depends(require_key)])
def rebalancing_plan(n_trucks: int = Query(3, ge=1, le=10), truck_capacity: int = Query(30, ge=5, le=80),
                     shift_minutes: int = Query(240, ge=30, le=600), candidates: int = Query(80, ge=5, le=200)) -> dict:
    """Optimised truck tours (OR-Tools) for the latest priority table. Default fleet = the batch plan of notebook 04;
    other fleets are solved on demand (5 s time box, cached per table version)."""
    version = common.table_version("gold_station_priority")
    stops, kpi = _plan(version, n_trucks, truck_capacity, shift_minutes, candidates)
    return {"fleet": {"n_trucks": n_trucks, "truck_capacity": truck_capacity, "shift_minutes": shift_minutes},
            "kpis": kpi, "stops": stops}


@app.get("/v1/alerts/backtest", dependencies=[Depends(require_key)])
def alert_backtest() -> dict:
    """How reliable the empty/full alerts were on recorded history (scripts/backtest_alerts.py)."""
    return {"metrics": records(common.table("gold_alert_backtest")),
            "calibration": records(common.table("gold_alert_calibration"))}


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


@app.get("/v1/predict", dependencies=[Depends(require_key)], response_model=Prediction)
def predict(station_id: str, hour: datetime | None = None,
            temperature_c: float = Query(15.0, ge=-30, le=45), precipitation_mm: float = Query(0.0, ge=0, le=100),
            wind_kmh: float = Query(10.0, ge=0, le=150), humidity_pct: float = Query(60.0, ge=0, le=100)) -> dict:
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
    out = {"metrics": records(common.table("gold_ml_metrics").drop(columns=["model_uri"], errors="ignore")),
           "serving": {t: common.model(t)[1] for t in ("departures", "arrivals")}}
    try:
        out["promotion_log"] = records(common.table("gold_model_promotion_log"))
    except FileNotFoundError:
        pass
    return out
