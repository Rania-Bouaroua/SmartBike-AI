"""Backtest plumbing: origin selection (no look-ahead, dense truth windows) and the walk-forward evaluation."""
import importlib.util
import pathlib

import numpy as np
import pandas as pd

spec = importlib.util.spec_from_file_location(
    "backtest_alerts", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "backtest_alerts.py")
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)


def test_origins_need_a_dense_window_and_respect_spacing():
    t = pd.date_range("2026-11-17 12:00", periods=40, freq="5min", tz="UTC")       # 3 h 15 of history
    gap = t.delete(slice(20, 24))                                                   # 20-min hole at 13:40-13:55
    o = bt.origins(pd.Series(t), pd.Timedelta(hours=2), pd.Timedelta(minutes=15))
    assert o[0] == t[0] and all(b - a >= pd.Timedelta(minutes=15) for a, b in zip(o, o[1:]))
    assert o[-1] + pd.Timedelta(hours=2) <= t[-1] + bt.MAX_GAP                      # full window recorded
    with_hole = bt.origins(pd.Series(gap), pd.Timedelta(hours=2), pd.Timedelta(minutes=15))
    assert len(with_hole) < len(o)                                                  # windows over the hole dropped
    assert bt.origins(pd.Series(t[:10]), pd.Timedelta(hours=2), pd.Timedelta(minutes=15)) == []


def test_evaluate_tunes_on_first_half_and_scores_three_methods():
    rng = np.random.default_rng(0)
    rows = []
    for k, t0 in enumerate(pd.date_range("2026-11-17", periods=6, freq="15min", tz="UTC")):
        n = 400
        p = rng.random(n)
        y = rng.random(n) < p * 0.3                                                 # informative probability
        bikes = rng.integers(1, 20, n)
        rows.append(pd.DataFrame({
            "origin_utc": t0, "station_id": [f"s{i}" for i in range(n)], "capacity": 20.0,
            "num_bikes_available": bikes, "num_docks_available": 20 - bikes + 1, "projected_bikes": bikes - 2.0,
            "p_empty": p, "p_full": p[::-1], "empty_within": y, "full_within": y[::-1],
            "naive_empty": bikes <= 2, "naive_full": (20 - bikes) <= 2,
            "point_empty": bikes - 2 <= 2, "point_full": (22 - bikes) <= 2}))
    metrics, calib = bt.evaluate(pd.concat(rows, ignore_index=True))
    assert set(metrics.method) == {"naive", "point", "probabilistic"} and set(metrics.event) == {"empty", "full"}
    m = metrics.set_index(["event", "method"])
    assert m.loc[("empty", "probabilistic"), "origins_calib"] == 3 and m.loc[("empty", "probabilistic"), "origins_test"] == 3
    assert not m.same_period.any()
    assert m.loc[("empty", "probabilistic"), "pr_auc"] > m.loc[("empty", "probabilistic"), "prevalence"]
    assert {"predicted", "observed", "event"} <= set(calib.columns)
