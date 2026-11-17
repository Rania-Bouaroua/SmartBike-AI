"""Take one snapshot of the Citi Bike GBFS feeds (source 2 - live JSON API).

Usage:
    python scripts/03_gbfs_snapshot.py

Writes the raw responses to data/raw/gbfs/:
  - station_information_<ts>.json : static reference data (name, short_name, lat/lon, capacity)
  - station_status_<ts>.json      : dynamic availability (bikes / docks available)

station_information is the reference table that reconciles the GBFS UUID `station_id`
with the trip archives' `start_station_id` (= GBFS `short_name`, e.g. "6257.03").
"""
import datetime as dt
import json
import pathlib

import requests

FEEDS = {
    "station_information": "https://gbfs.citibikenyc.com/gbfs/en/station_information.json",
    "station_status": "https://gbfs.citibikenyc.com/gbfs/en/station_status.json",
}

if __name__ == "__main__":
    out = pathlib.Path("data/raw/gbfs")
    out.mkdir(parents=True, exist_ok=True)
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for name, url in FEEDS.items():
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        data = r.json()
        target = out / f"{name}_{ts}.json"
        target.write_text(json.dumps(data), encoding="utf-8")
        print(f"[ok] {name}: {len(data['data']['stations'])} stations -> {target}")
