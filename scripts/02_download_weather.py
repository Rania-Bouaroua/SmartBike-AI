"""Download hourly historical weather for New York from Open-Meteo (source 3 - REST API / JSON).

Usage:
    python scripts/02_download_weather.py --start 2026-10-01 --end 2026-10-31

No API key is required. Output: data/raw/weather/weather_<start>_<end>.json (raw API response, Bronze).
Times are requested in the America/New_York timezone so they align with Citi Bike timestamps (local time).
"""
import argparse
import json
import pathlib

import requests

URL = "https://archive-api.open-meteo.com/v1/archive"
NYC = {"latitude": 40.7306, "longitude": -73.9866}  # Manhattan centroid
VARIABLES = "temperature_2m,precipitation,rain,wind_speed_10m,relative_humidity_2m,weather_code"

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2026-10-01")
    p.add_argument("--end", default="2026-10-31")
    p.add_argument("--out", default="data/raw/weather")
    a = p.parse_args()

    params = {**NYC, "start_date": a.start, "end_date": a.end,
              "hourly": VARIABLES, "timezone": "America/New_York"}
    r = requests.get(URL, params=params, timeout=60)
    r.raise_for_status()
    data = r.json()
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"weather_{a.start}_{a.end}.json"
    target.write_text(json.dumps(data), encoding="utf-8")
    print(f"[ok] {len(data['hourly']['time'])} hourly rows -> {target}")
