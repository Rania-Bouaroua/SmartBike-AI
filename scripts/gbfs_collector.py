"""Continuous GBFS collector -> Microsoft Fabric Eventstream (real-time path).

Polls Citi Bike `station_status` every N seconds, enriches each station with the static
`station_information` (name, short_name, lat/lon, capacity), and sends one JSON event per
station to an Eventstream *Custom endpoint* source (Event Hub-compatible protocol).

Usage:
    # Local test, no cloud: writes events to data/raw/stream/*.jsonl
    python scripts/gbfs_collector.py --dry-run --interval 60 --iterations 3

    # Real mode: the connection string is NEVER in the code (see security/README_security.md)
    #   Option A: environment variable EVENTSTREAM_CONNECTION_STRING (+ EVENTSTREAM_NAME)
    #   Option B: Azure Key Vault secret, read with the signed-in identity (az login / managed identity)
    python scripts/gbfs_collector.py --keyvault kv-smartbike-dev --secret eventstream-conn

Cost control: --only-changes sends a station only when its availability changed since the
previous poll (≈ 8x fewer events than sending all ~2,500 stations every minute). A full snapshot is
still sent every --full-every polls (default 15) so that every KQL 15-minute window sees every station.
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import time

import requests

INFO_URL = "https://gbfs.citibikenyc.com/gbfs/en/station_information.json"
STATUS_URL = "https://gbfs.citibikenyc.com/gbfs/en/station_status.json"


def get_secret(vault: str, name: str) -> str:
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient

    client = SecretClient(vault_url=f"https://{vault}.vault.azure.net", credential=DefaultAzureCredential())
    return client.get_secret(name).value


def load_station_info() -> dict:
    stations = requests.get(INFO_URL, timeout=30).json()["data"]["stations"]
    return {s["station_id"]: s for s in stations}


def build_events(info: dict, previous: dict, only_changes: bool, poll_type: str = "delta") -> tuple[list[dict], dict]:
    payload = requests.get(STATUS_URL, timeout=30).json()
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    events, current = [], {}
    for s in payload["data"]["stations"]:
        sid = s["station_id"]
        ref = info.get(sid, {})
        capacity = ref.get("capacity") or 0
        bikes = s.get("num_bikes_available", 0)
        docks = s.get("num_docks_available", 0)
        key = (bikes, docks, s.get("is_renting"), s.get("is_returning"))
        current[sid] = key
        if only_changes and previous.get(sid) == key:
            continue
        events.append({
            "event_time": now,
            "station_id": sid,
            "short_name": ref.get("short_name"),
            "station_name": ref.get("name"),
            "lat": ref.get("lat"),
            "lon": ref.get("lon"),
            "capacity": capacity,
            "bikes_available": bikes,
            "ebikes_available": s.get("num_ebikes_available", 0),
            "docks_available": docks,
            "bikes_disabled": s.get("num_bikes_disabled", 0),
            "docks_disabled": s.get("num_docks_disabled", 0),
            "is_installed": s.get("is_installed", 0),
            "is_renting": s.get("is_renting", 0),
            "is_returning": s.get("is_returning", 0),
            "last_reported": dt.datetime.fromtimestamp(s.get("last_reported", 0), dt.timezone.utc).isoformat(),
            "fill_ratio": round(bikes / capacity, 3) if capacity else None,
            "poll_type": poll_type,
        })
    return events, current


class JsonlSink:
    def __init__(self, folder="data/raw/stream"):
        self.folder = pathlib.Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)

    def send(self, events):
        f = self.folder / f"station_status_{dt.date.today():%Y%m%d}.jsonl"
        with open(f, "a", encoding="utf-8") as fh:
            for e in events:
                fh.write(json.dumps(e) + "\n")

    def close(self):
        pass


class EventstreamSink:
    """Eventstream 'Custom endpoint' exposes an Event Hub-compatible connection string."""

    def __init__(self, conn_str: str, eventhub_name: str | None):
        from azure.eventhub import EventHubProducerClient

        self.client = EventHubProducerClient.from_connection_string(conn_str, eventhub_name=eventhub_name)

    def send(self, events):
        from azure.eventhub import EventData

        batch = self.client.create_batch()
        for e in events:
            data = EventData(json.dumps(e))
            try:
                batch.add(data)
            except ValueError:  # batch full -> flush and start a new one
                self.client.send_batch(batch)
                batch = self.client.create_batch()
                batch.add(data)
        if len(batch):
            self.client.send_batch(batch)

    def close(self):
        self.client.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--interval", type=int, default=60, help="seconds between polls (GBFS ttl is ~60s)")
    p.add_argument("--iterations", type=int, default=0, help="0 = run forever")
    p.add_argument("--dry-run", action="store_true", help="write JSONL locally instead of Eventstream")
    p.add_argument("--only-changes", action="store_true")
    p.add_argument("--full-every", type=int, default=15, help="send a full snapshot every N polls")
    p.add_argument("--keyvault", help="Key Vault name holding the connection string")
    p.add_argument("--secret", default="eventstream-conn")
    a = p.parse_args()

    if a.dry_run:
        sink = JsonlSink()
    else:
        conn = get_secret(a.keyvault, a.secret) if a.keyvault else os.environ["EVENTSTREAM_CONNECTION_STRING"]
        sink = EventstreamSink(conn, os.environ.get("EVENTSTREAM_NAME"))

    info, previous, i = load_station_info(), {}, 0
    try:
        while True:
            i += 1
            if i % 60 == 0:  # refresh reference data every ~hour (new stations, capacity changes)
                info = load_station_info()
            try:
                full = (i - 1) % a.full_every == 0
                events, previous = build_events(info, previous, a.only_changes and not full,
                                                "full" if full or not a.only_changes else "delta")
                sink.send(events)
                print(f"[{dt.datetime.now():%H:%M:%S}] poll {i}: sent {len(events)} events")
            except requests.RequestException as exc:  # transient network error: log and keep going
                print(f"[warn] poll {i} failed: {exc}")
            if a.iterations and i >= a.iterations:
                break
            time.sleep(a.interval)
    finally:
        sink.close()
