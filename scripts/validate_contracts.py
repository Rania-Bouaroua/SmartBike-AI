"""Quality gate: validate every Gold/Silver table that has a data contract (smartbike/contracts.py).

    python scripts/validate_contracts.py        # exit 0 = all contracts hold, 1 = at least one violation

Run as the last activity of the pipeline (pipeline/pl_smartbike_batch.json) so a broken table never reaches the
API, the operator app or Power BI unnoticed.
"""
from __future__ import annotations

import pathlib
import sys

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import common  # noqa: E402
from smartbike.contracts import CONTRACTS, DataContractError, validate  # noqa: E402


def main() -> int:
    failed = 0
    for name in CONTRACTS:
        path = common.TABLES / name
        if not path.exists():
            print(f"[skip] {name}: table not built")
            continue
        try:
            df = pd.read_parquet(path)
            validate(name, df)
            print(f"[ok]   {name}: {len(df):,} rows")
        except DataContractError as exc:
            failed += 1
            print(f"[FAIL] {exc}")
    print("contracts:", "all hold" if not failed else f"{failed} violated")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
