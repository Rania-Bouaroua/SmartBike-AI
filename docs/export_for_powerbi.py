"""Exports each local Gold table (folder of Parquet parts) to ONE Parquet file in data/powerbi/ for Power BI Desktop.

Usage: python docs/export_for_powerbi.py
"""
import pathlib

import pyarrow.parquet as pq

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC, OUT = ROOT / "data" / "lakehouse" / "Tables", ROOT / "data" / "powerbi"
OUT.mkdir(parents=True, exist_ok=True)
for table in sorted(p for p in SRC.iterdir() if p.is_dir() and p.name.startswith("gold_")):
    t = pq.read_table(table)
    pq.write_table(t, OUT / f"{table.name}.parquet")
    print(f"{table.name}: {t.num_rows:,} rows")
