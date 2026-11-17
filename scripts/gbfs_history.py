"""Record the Citi Bike GBFS station_status feed at a fixed interval, as compact Parquet snapshots.

Why: the empty/full alerts are a *forecast* ("this station will be empty within 2 h"). To measure whether they are
right we need the true station state over time, which the trip archives do not contain (they hold movements, not
stock levels). This recorder builds that ground truth; `scripts/backtest_alerts.py` consumes it.

Usage:
    python scripts/gbfs_history.py --interval 300 --iterations 96      # 8 h, one snapshot every 5 min
    python scripts/gbfs_history.py --once                               # single snapshot (e.g. from a scheduler)

Output: data/raw/gbfs_history/status_<UTC timestamp>.parquet, ~60 KB per snapshot (vs ~1 MB of raw JSON).
In Fabric, the same history is the Eventhouse table fed by the Eventstream (kql/), so this script is the local stand-in.
"""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys
import time

import pandas as pd
import requests

STATUS_URL = "https://gbfs.citibikenyc.com/gbfs/en/station_status.json"
COLUMNS = ["station_id", "num_bikes_available", "num_docks_available", "is_renting", "is_returning", "last_reported"]
OUT = pathlib.Path(__file__).resolve().parents[1] / "data" / "raw" / "gbfs_history"


def snapshot(session: requests.Session) -> pathlib.Path:
    resp = session.get(STATUS_URL, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    df = pd.DataFrame(payload["data"]["stations"])[COLUMNS]
    taken = dt.datetime.fromtimestamp(payload["last_updated"], tz=dt.UTC)
    df["snapshot_utc"] = pd.Timestamp(taken)
    df["last_reported"] = pd.to_datetime(df["last_reported"], unit="s", utc=True)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"status_{taken:%Y%m%dT%H%M%SZ}.parquet"
    df.to_parquet(path, index=False)
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--interval", type=int, default=300, help="seconds between snapshots (default 300)")
    ap.add_argument("--iterations", type=int, default=96, help="number of snapshots (default 96 = 8 h at 5 min)")
    ap.add_argument("--once", action="store_true", help="take a single snapshot and exit")
    args = ap.parse_args()
    n = 1 if args.once else args.iterations
    with requests.Session() as session:
        for i in range(n):
            started = time.monotonic()
            try:
                path = snapshot(session)
                print(f"[{i + 1}/{n}] {path.name}", flush=True)
            except (requests.RequestException, KeyError, ValueError) as exc:   # keep recording through outages
                print(f"[{i + 1}/{n}] skipped: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            if i < n - 1:
                time.sleep(max(0.0, args.interval - (time.monotonic() - started)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
