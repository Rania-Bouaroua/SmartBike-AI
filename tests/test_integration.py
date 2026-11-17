"""End-to-end API tests on the real Gold tables and models (skipped when the pipeline has not been run, e.g. in CI)."""
import pytest
from fastapi.testclient import TestClient

from app import api, common

REQUIRED = ["gold_station_priority", "gold_daily_kpis", "silver_stations", "gold_station_hour_encoding"]
pytestmark = pytest.mark.skipif(not all((common.TABLES / t).exists() for t in REQUIRED),
                                reason="real Gold tables not built (run the notebooks)")


@pytest.fixture(scope="module")
def client():
    common.table.cache_clear()
    return TestClient(api.app)


def test_status_reports_snapshot_models_and_versions(client):
    s = client.get("/v1/status").json()
    assert s["horizon_hours"] >= 1 and set(s["models"]) == {"departures", "arrivals"}
    assert s["table_versions_utc"]["gold_station_priority"]


def test_priority_is_ranked_and_carries_probabilities(client):
    rows = client.get("/v1/priority", params={"limit": 50}).json()
    assert [r["priority_rank"] for r in rows] == sorted(r["priority_rank"] for r in rows)
    assert all(0 <= r["p_empty"] <= 1 for r in rows if r.get("p_empty") is not None)
    urgent = client.get("/v1/priority", params={"risk": "risque vide", "limit": 5}).json()
    assert all(r["risk"] == "risque vide" and r["bikes_to_move"] > 0 for r in urgent)


def test_rain_lowers_predicted_demand(client):
    busiest = client.get("/v1/priority", params={"limit": 1}).json()[0]["station_id"]
    q = {"station_id": busiest, "hour": "2026-10-06T08:00:00", "temperature_c": 20}
    dry = client.get("/v1/predict", params=q).json()
    wet = client.get("/v1/predict", params={**q, "precipitation_mm": 8}).json()
    assert wet["pred_departures"] < dry["pred_departures"]


def test_rebalancing_plan_respects_the_fleet(client):
    r = client.get("/v1/rebalancing-plan", params={"n_trucks": 2, "truck_capacity": 20, "shift_minutes": 120,
                                                   "candidates": 25})
    assert r.status_code == 200
    body = r.json()
    assert body["kpis"]["stops"] == len(body["stops"]) > 0
    assert all(0 <= s["load_after"] <= 20 and s["truck"] <= 2 for s in body["stops"])
