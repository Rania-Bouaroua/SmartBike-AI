"""SmartBike AI - data-quality and operational alerts (bonus "monitoring / alerting").

Checks run after each pipeline execution (and every few minutes on the live feed):
  DQ-1  share of valid trips in the last batch          < 98 %          -> data-quality alert
  DQ-2  share of trip events matched to a GBFS station  < 99 %          -> data-quality alert (ID drift)
  DQ-3  live GBFS snapshot older than 15 min                             -> pipeline / feed alert
  OPS-1 stations empty or saturated right now           > 8 % of network -> operational alert
  OPS-2 a top-50 station (by usage) is empty right now                   -> operational alert
  ML-1  model MAE no better than the naive baseline                      -> model alert (drift)

Alerts are appended to logs/alerts.jsonl and, if SMARTBIKE_ALERT_WEBHOOK is set (Teams / Slack incoming
webhook; in Azure read from Key Vault), posted there. Exit code 2 when a critical alert fires, so a pipeline
step / GitHub Action / scheduled task fails visibly.
In Fabric the live checks are implemented with Activator on the Eventhouse (see kql/03_alerts.kql).

Usage: python scripts/alert_monitor.py [--live]
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
from datetime import UTC, datetime

import pandas as pd
import requests

ROOT = pathlib.Path(__file__).resolve().parents[1]
T = ROOT / "data" / "lakehouse" / "Tables"
LOG = ROOT / "logs" / "alerts.jsonl"

THRESHOLDS = {"valid_pct_min": 98.0, "station_match_pct_min": 99.0, "snapshot_max_age_min": 15,
              "empty_or_full_pct_max": 8.0, "top_n_stations": 50}


def alert(rule: str, severity: str, message: str, value=None) -> dict:
    return {"time_utc": datetime.now(UTC).isoformat(timespec="seconds"), "rule": rule, "severity": severity,
            "message": message, "value": value}


def check_quality() -> list[dict]:
    out = []
    dq = pd.read_parquet(T / "gold_data_quality")
    valid = dq[(dq.table == "trips") & (dq.check == "valid")].pct_of_raw.sum()
    if valid < THRESHOLDS["valid_pct_min"]:
        out.append(alert("DQ-1", "critical", f"only {valid:.2f}% valid trips in the last batch", valid))
    matched = dq[dq.check == "station_matched"].pct_of_raw.sum()
    if matched < THRESHOLDS["station_match_pct_min"]:
        out.append(alert("DQ-2", "warning", f"only {matched:.2f}% of trip events match a GBFS station", matched))
    return out


def check_model() -> list[dict]:
    m = pd.read_parquet(T / "gold_ml_metrics")
    return [alert("ML-1", "critical", f"{r.target}: model MAE {r.model_mae:.3f} >= baseline {r.baseline_mae:.3f}",
                  r.model_mae) for r in m.itertuples() if r.model_mae >= r.baseline_mae]


def check_live() -> list[dict]:
    out = []
    status = requests.get("https://gbfs.citibikenyc.com/gbfs/en/station_status.json", timeout=30).json()
    age = (time.time() - status["last_updated"]) / 60
    if age > THRESHOLDS["snapshot_max_age_min"]:
        out.append(alert("DQ-3", "critical", f"GBFS feed is {age:.0f} min old", round(age, 1)))
    s = pd.DataFrame(status["data"]["stations"])
    s = s[s.is_installed == 1]
    bad = s[(s.num_bikes_available == 0) | (s.num_docks_available == 0)]
    pct = len(bad) / len(s) * 100
    if pct > THRESHOLDS["empty_or_full_pct_max"]:
        out.append(alert("OPS-1", "warning", f"{len(bad)} stations empty or full ({pct:.1f}% of the network)",
                         round(pct, 2)))
    prof = pd.read_parquet(T / "gold_station_profile", columns=["station_id", "station_name", "departures"])
    st = pd.read_parquet(T / "silver_stations", columns=["station_id", "gbfs_station_id"])
    top = prof.nlargest(THRESHOLDS["top_n_stations"], "departures").merge(st, on="station_id")
    empty_top = top.merge(s[s.num_bikes_available == 0], left_on="gbfs_station_id", right_on="station_id")
    for r in empty_top.itertuples():
        out.append(alert("OPS-2", "critical", f"busy station '{r.station_name}' is EMPTY now", r.station_id_x))
    return out


def notify(alerts: list[dict]) -> None:
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        for a in alerts:
            fh.write(json.dumps(a, ensure_ascii=False) + "\n")
    hook = os.environ.get("SMARTBIKE_ALERT_WEBHOOK")
    if hook and alerts:
        text = "\n".join(f"[{a['severity'].upper()}] {a['rule']}: {a['message']}" for a in alerts)
        requests.post(hook, json={"text": f"SmartBike AI alerts\n{text}"}, timeout=15)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--live", action="store_true", help="also check the live GBFS feed")
    a = p.parse_args()
    found = check_quality() + check_model() + (check_live() if a.live else [])
    notify(found)
    for x in found:
        print(f"[{x['severity'].upper():8}] {x['rule']}: {x['message']}")
    if not found:
        print("[ok] no alert")
    sys.exit(2 if any(x["severity"] == "critical" for x in found) else 0)
