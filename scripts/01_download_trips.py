"""Download Citi Bike NYC monthly trip archives (source 1 - historical CSV).

Usage:
    python scripts/01_download_trips.py --months 202610 [202609 ...] [--out data/raw/trips]

The public bucket is https://s3.amazonaws.com/tripdata (listed on citibikenyc.com/system-data).
Each monthly ZIP contains several CSV parts with the schema documented in docs/data_dictionary.md.
"""
import argparse
import pathlib
import zipfile

import requests

BASE_URL = "https://s3.amazonaws.com/tripdata/{month}-citibike-tripdata.zip"


def download(month: str, out_dir: pathlib.Path) -> pathlib.Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{month}-citibike-tripdata.zip"
    if target.exists():
        print(f"[skip] {target} already exists")
        return target
    url = BASE_URL.format(month=month)
    print(f"[get ] {url}")
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        tmp = target.with_suffix(".part")
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
        tmp.rename(target)
    return target


def extract(archive: pathlib.Path, out_dir: pathlib.Path) -> list[pathlib.Path]:
    with zipfile.ZipFile(archive) as z:
        bad = z.testzip()
        if bad:
            raise RuntimeError(f"Corrupted member in {archive}: {bad}")
        names = [n for n in z.namelist() if n.endswith(".csv") and not n.startswith("__MACOSX")]
        for n in names:
            dest = out_dir / pathlib.Path(n).name
            if not dest.exists():
                with z.open(n) as src, open(dest, "wb") as dst:
                    while chunk := src.read(1 << 20):
                        dst.write(chunk)
        return [out_dir / pathlib.Path(n).name for n in names]


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--months", nargs="+", default=["202610"])
    p.add_argument("--out", default="data/raw/trips")
    a = p.parse_args()
    out = pathlib.Path(a.out)
    for m in a.months:
        files = extract(download(m, out), out)
        print(f"[ok  ] {m}: {len(files)} CSV file(s)")
