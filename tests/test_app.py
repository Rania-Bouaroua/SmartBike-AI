"""Unit tests for the API layer and the alert rules (no Spark, no network, synthetic tables)."""
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import api, common


@pytest.fixture()
def tables(tmp_path, monkeypatch):
    t = tmp_path / "Tables"

    def put(name, df):
        (t / name).mkdir(parents=True)
        df.to_parquet(t / name / "part-0.parquet")

    put("silver_stations", pd.DataFrame({"station_id": ["6140.05"], "gbfs_station_id": ["uuid-1"],
                                         "station_name": ["W 21 St & 6 Ave"], "lat": [40.74], "lon": [-73.99],
                                         "capacity": [50]}))
    put("gold_station_hour_encoding", pd.DataFrame({"station_id": ["6140.05"], "hour": [8], "is_weekend": [0],
                                                    "enc_dep_station_hour": [7.5]}))
    put("gold_station_encoding", pd.DataFrame({"station_id": ["6140.05"], "enc_dep_station": [3.0]}))
    put("gold_daily_kpis", pd.DataFrame({"date": pd.to_datetime(["2026-10-01", "2026-10-02"]).date,
                                         "trips": [100, 300], "avg_duration_min": [10.0, 14.0],
                                         "member_share_pct": [80.0, 84.0], "ebike_share_pct": [70.0, 74.0]}))
    put("gold_data_quality", pd.DataFrame({"check": ["valid", "station_matched"], "rows": [97, 10],
                                           "pct_of_raw": [97.0, 99.9], "table": ["trips", "stations"]}))
    put("gold_ml_metrics", pd.DataFrame({"target": ["departures"], "model_mae": [1.2], "baseline_mae": [1.1]}))
    monkeypatch.setattr(common, "TABLES", t)
    common.table.cache_clear()
    yield t
    common.table.cache_clear()


def test_features_use_spark_day_of_week_and_train_encodings(tables):
    # 2026-10-05 is a Monday -> Spark dayofweek = 2 ; 8h weekday -> encoding found
    f = common.build_features("6140.05", pd.Timestamp("2026-10-05 08:40"), {})
    assert f.loc[0, "dow"] == 2 and f.loc[0, "is_weekend"] == 0 and f.loc[0, "hour"] == 8
    assert f.loc[0, "enc_dep_station_hour"] == 7.5
    sunday = common.build_features("6140.05", pd.Timestamp("2026-10-04 08:00"), {})
    assert sunday.loc[0, "dow"] == 1 and sunday.loc[0, "is_weekend"] == 1
    assert sunday.loc[0, "enc_dep_station_hour"] == 0.0          # unseen key -> 0, never NaN


def test_unknown_station_raises(tables):
    with pytest.raises(KeyError):
        common.build_features("nope", pd.Timestamp("2026-10-05"), {})


def test_kpis_are_trip_weighted(tables, monkeypatch):
    monkeypatch.delenv("SMARTBIKE_API_KEY", raising=False)
    r = TestClient(api.app).get("/v1/kpis").json()
    assert r["total_trips"] == 400
    assert r["avg_duration_min"] == 13.0            # (10*100 + 14*300) / 400, not the plain mean 12


def test_api_key_is_enforced_when_configured(tables, monkeypatch):
    monkeypatch.setenv("SMARTBIKE_API_KEY", "s3cret")
    client = TestClient(api.app)
    assert client.get("/v1/kpis").status_code == 401
    assert client.get("/v1/kpis", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/v1/kpis", headers={"X-API-Key": "s3cret"}).status_code == 200
    assert client.get("/health").status_code == 200       # health probe stays public


def test_alert_rules_fire_on_bad_quality_and_bad_model(tables, monkeypatch):
    import importlib.util
    import pathlib
    spec = importlib.util.spec_from_file_location(
        "alert_monitor", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "alert_monitor.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "T", tables)
    rules = {a["rule"] for a in mod.check_quality() + mod.check_model()}
    assert rules == {"DQ-1", "ML-1"}          # 97 % valid < 98 %, MAE 1.2 >= baseline 1.1 ; match 99.9 % is fine


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_live_status_flags_empty_and_full_and_ignores_unknown_stations(tables, monkeypatch):
    import requests

    payload = {"data": {"stations": [
        {"station_id": "uuid-1", "num_bikes_available": 0, "num_docks_available": 50, "is_renting": 1,
         "last_reported": 1_790_000_000},
        {"station_id": "uuid-unknown", "num_bikes_available": 3, "num_docks_available": 0, "is_renting": 1,
         "last_reported": 1_790_000_000},
    ]}}
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(payload))
    live = common.live_status()
    assert list(live.station_id) == ["6140.05"]          # unknown GBFS station dropped by the join
    assert bool(live.is_empty.iloc[0]) and not bool(live.is_full.iloc[0])
    assert str(live.last_reported_utc.iloc[0].tz) == "UTC"


def test_live_endpoint_returns_503_when_feed_is_down(tables, monkeypatch):
    import requests

    def boom(*a, **k):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(requests, "get", boom)
    r = TestClient(api.app).get("/v1/stations/6140.05/live")
    assert r.status_code == 503


# ------------------------------------------------------------------ API hardening (v1.1)
def test_api_validates_inputs_and_tags_every_response(tables):
    client = TestClient(api.app)
    r = client.get("/health", headers={"X-Request-ID": "abc123"})
    assert r.headers["X-Request-ID"] == "abc123" and r.headers["Server-Timing"].startswith("app;dur=")
    assert client.get("/v1/priority", params={"risk": "not-a-label"}).status_code == 422
    assert client.get("/v1/predict", params={"station_id": "6140.05", "temperature_c": 80}).status_code == 422
    assert client.get("/v1/predict", params={"station_id": "6140.05", "precipitation_mm": -1}).status_code == 422


def test_missing_table_and_broken_contract_answer_503(tables, monkeypatch):
    client = TestClient(api.app)
    r = client.get("/v1/alerts/backtest")                       # table not built in the fixture
    assert r.status_code == 503 and r.json()["detail"] == "table not available"
    bad = pd.DataFrame({"date": pd.to_datetime(["2026-10-01", "2026-10-01"]).date, "trips": [100, -5],
                        "avg_duration_min": [10.0, 14.0], "member_share_pct": [80.0, 140.0],
                        "ebike_share_pct": [70.0, 74.0]})
    path = tables / "gold_daily_kpis" / "part-0.parquet"
    bad.to_parquet(path)
    import os
    os.utime(path, (os.path.getmtime(path) + 10,) * 2)          # new table version -> re-validated
    r = client.get("/v1/kpis")
    assert r.status_code == 503 and r.json()["detail"] == "data contract violated"
    assert "trips" in r.json()["reason"] and "member_share_pct" in r.json()["reason"]
