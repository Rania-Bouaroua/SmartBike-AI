"""Unit tests for the real-time collector (no network: GBFS responses are mocked)."""
import importlib.util
import pathlib

spec = importlib.util.spec_from_file_location(
    "gbfs_collector", pathlib.Path(__file__).parents[1] / "scripts" / "gbfs_collector.py")
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)

INFO = {"A": {"short_name": "6257.03", "name": "W 24 St & 7 Ave", "lat": 40.7, "lon": -73.9, "capacity": 20},
        "B": {"short_name": "6224.06", "name": "8 Ave & W 24 St", "lat": 40.7, "lon": -73.9, "capacity": 0}}


def status(bikes_a, bikes_b=3):
    return {"data": {"stations": [
        {"station_id": "A", "num_bikes_available": bikes_a, "num_docks_available": 20 - bikes_a,
         "is_renting": 1, "is_returning": 1, "is_installed": 1, "last_reported": 1794848960},
        {"station_id": "B", "num_bikes_available": bikes_b, "num_docks_available": 0,
         "is_renting": 1, "is_returning": 1, "is_installed": 1, "last_reported": 1794848960},
    ]}}


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def test_events_are_enriched_with_station_information(monkeypatch):
    monkeypatch.setattr(collector.requests, "get", lambda *a, **k: FakeResponse(status(5)))
    events, _ = collector.build_events(INFO, {}, only_changes=False)
    a = next(e for e in events if e["station_id"] == "A")
    assert a["short_name"] == "6257.03"          # join key with the trip archives
    assert a["fill_ratio"] == 0.25
    assert a["poll_type"] == "delta"
    assert a["last_reported"].startswith("2026-11-16")


def test_zero_capacity_gives_null_fill_ratio(monkeypatch):
    monkeypatch.setattr(collector.requests, "get", lambda *a, **k: FakeResponse(status(5)))
    events, _ = collector.build_events(INFO, {}, only_changes=False)
    assert next(e for e in events if e["station_id"] == "B")["fill_ratio"] is None


def test_only_changes_sends_changed_stations(monkeypatch):
    monkeypatch.setattr(collector.requests, "get", lambda *a, **k: FakeResponse(status(5)))
    _, previous = collector.build_events(INFO, {}, only_changes=True)
    monkeypatch.setattr(collector.requests, "get", lambda *a, **k: FakeResponse(status(4)))
    events, _ = collector.build_events(INFO, previous, only_changes=True)
    assert [e["station_id"] for e in events] == ["A"]   # B unchanged -> not resent


def test_jsonl_sink_writes_one_line_per_event(tmp_path):
    sink = collector.JsonlSink(tmp_path)
    sink.send([{"x": 1}, {"x": 2}])
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1 and len(files[0].read_text().splitlines()) == 2
