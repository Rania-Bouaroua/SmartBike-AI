"""Data contracts for the tables the serving layer (API, operator app, Power BI) depends on.

Each contract states what consumers assume about a table: keys, types, ranges, allowed labels and cross-column rules.
They are enforced (1) by `scripts/validate_contracts.py`, the quality gate at the end of the pipeline, and (2) when the
API / app load a new version of a table (app.common), so a broken table is refused loudly instead of being displayed.
"""
from __future__ import annotations

import pandera.pandas as pa
from pandera.pandas import Check, Column, DataFrameSchema

from smartbike.risk import RISK_LABELS

NYC_LAT, NYC_LON = (40.40, 41.05), (-74.35, -73.60)
PROB = [Check.ge(0), Check.le(1)]
NONNEG = [Check.ge(0)]


def _action_matches_move(df):
    sign = df.bikes_to_move.apply(lambda v: (v > 0) - (v < 0))
    expected = sign.map({1: "ajouter des velos", -1: "retirer des velos", 0: "aucune"})
    return expected == df.action


CONTRACTS: dict[str, DataFrameSchema] = {
    "silver_stations": DataFrameSchema({
        "station_id": Column(str, unique=True),
        "station_name": Column(str),
        "lat": Column(float, Check.in_range(*NYC_LAT)),
        "lon": Column(float, Check.in_range(*NYC_LON)),
        "capacity": Column(float, NONNEG, nullable=True, coerce=True),
    }),
    "gold_daily_kpis": DataFrameSchema({
        "date": Column(object, unique=True),
        "trips": Column(int, Check.gt(0), coerce=True),
        "avg_duration_min": Column(float, Check.in_range(1, 120)),
        "member_share_pct": Column(float, Check.in_range(0, 100)),
        "ebike_share_pct": Column(float, Check.in_range(0, 100)),
    }),
    "gold_station_priority": DataFrameSchema(
        {
            "station_id": Column(str, unique=True),
            "capacity": Column(float, Check.gt(0), coerce=True),
            "num_bikes_available": Column(int, NONNEG, coerce=True),
            "num_docks_available": Column(int, NONNEG, coerce=True),
            "pred_departures": Column(float, NONNEG),
            "pred_arrivals": Column(float, NONNEG),
            "p_empty": Column(float, PROB, required=False),
            "p_full": Column(float, PROB, required=False),
            "risk": Column(str, Check.isin(RISK_LABELS)),
            "bikes_to_move": Column(int, coerce=True),
            "action": Column(str, Check.isin(["ajouter des velos", "retirer des velos", "aucune"])),
            "priority_rank": Column(int, [Check.ge(1)], unique=True, coerce=True),
            "lat": Column(float, Check.in_range(*NYC_LAT)),
            "lon": Column(float, Check.in_range(*NYC_LON)),
        },
        checks=[Check(_action_matches_move, error="action must follow the sign of bikes_to_move"),
                Check(lambda df: (df.risk == "ok") == (df.bikes_to_move == 0),
                      error="a station needs a move if and only if it is at risk")],
    ),
    "gold_truck_routes": DataFrameSchema(
        {
            "method": Column(str),
            "truck": Column(int, Check.ge(1)),
            "stop": Column(int, Check.ge(1)),
            "station_id": Column(str),
            "delta": Column(int, Check.ne(0)),
            "load_after": Column(int, NONNEG),
            "minutes_cum": Column(float, NONNEG),
            "leg_km": Column(float, NONNEG),
        },
        checks=[Check(lambda df: ~df.duplicated(["method", "station_id"]), error="a station is visited once per plan")],
    ),
    "gold_ml_metrics": DataFrameSchema({
        "target": Column(str, Check.isin(["departures", "arrivals"]), unique=True),
        "model_mae": Column(float, Check.gt(0)),
        "baseline_mae": Column(float, Check.gt(0)),
    }),
    "gold_model_comparison": DataFrameSchema(
        {
            "target": Column(str, Check.isin(["departures", "arrivals"])),
            "model": Column(str),
            "val_poisson_dev": Column(float, Check.gt(0)),
            "val_mae": Column(float, Check.gt(0)),
            "selected": Column(bool),
        },
        checks=[Check(lambda df: df.groupby("target").selected.sum().eq(1).all(),
                      error="exactly one selected model per target"),
                Check(lambda df: ~df.duplicated(["target", "model"]), error="one row per target and model"),
                Check(lambda df: ~(df.selected & df.model.eq("baseline")), error="the baseline is not a model")],
    ),
    "gold_alert_backtest": DataFrameSchema({
        "event": Column(str, Check.isin(["empty", "full"])),
        "method": Column(str, Check.isin(["naive", "point", "probabilistic"])),
        "precision": Column(float, PROB),
        "recall": Column(float, PROB),
        "pr_auc": Column(float, PROB, nullable=True),
        "n": Column(int, Check.gt(0)),
    }),
}


class DataContractError(RuntimeError):
    """A table violates its contract; message lists every failing check."""


def validate(name: str, df):
    """Validate `df` against the contract of table `name` (no-op when the table has no contract)."""
    schema = CONTRACTS.get(name)
    if schema is None:
        return df
    try:
        return schema.validate(df, lazy=True)
    except pa.errors.SchemaErrors as exc:
        cases = exc.failure_cases[["column", "check", "failure_case"]].drop_duplicates().head(10)
        raise DataContractError(f"table {name} violates its contract:\n{cases.to_string(index=False)}") from exc
