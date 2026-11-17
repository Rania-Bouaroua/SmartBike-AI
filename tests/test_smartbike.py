"""Unit tests for the domain package (pure functions, no I/O)."""
import math

import numpy as np
import pandas as pd
import pytest

from smartbike import evaluation as ev
from smartbike.features import build_feature_grid
from smartbike.risk import RiskConfig, assess, point_projection, stockout_probability
from smartbike.routing import FleetConfig, candidates_from_priority, distance_matrix_km, greedy_plan, optimise_plan


# ------------------------------------------------------------------ risk
def poisson_sf(k: int, lam: float) -> float:
    """P(X >= k) for X ~ Poisson(lam)."""
    return 1 - sum(math.exp(-lam) * lam ** i / math.factorial(i) for i in range(k))


def test_monte_carlo_matches_exact_probability_when_only_departures():
    # with departures only the stock path is monotone: P(hit 0 within H) = P(Poisson(lambda*H) >= bikes)
    bikes, lam_h, horizon = np.array([3, 5, 8]), 2.0, 2
    cfg = RiskConfig(horizon_h=horizon, n_sims=40_000, seed=1)
    p_empty, p_full = stockout_probability(bikes, np.array([20, 20, 20]), np.full((3, horizon), lam_h),
                                           np.zeros((3, horizon)), cfg)
    exact = [poisson_sf(int(b), lam_h * horizon) for b in bikes]
    assert np.allclose(p_empty, exact, atol=0.01)
    assert (p_full == 0).all()


def test_risk_is_deterministic_monotone_and_bounded():
    cfg = RiskConfig(n_sims=2000)
    cap, bikes = np.array([20, 20]), np.array([6, 6])
    low = stockout_probability(bikes, cap, np.full((2, 2), [[1.0], [4.0]]), np.ones((2, 2)), cfg)[0]
    again = stockout_probability(bikes, cap, np.full((2, 2), [[1.0], [4.0]]), np.ones((2, 2)), cfg)[0]
    assert np.array_equal(low, again)                       # seeded
    assert low[1] > low[0]                                  # more departures -> more likely empty
    assert ((low >= 0) & (low <= 1)).all()
    p_e, p_f = stockout_probability(np.array([0, 20]), cap, np.zeros((2, 2)), np.zeros((2, 2)), cfg)
    assert p_e[0] == 1 and p_f[1] == 1                      # already out counts as out


def test_stockout_probability_rejects_bad_input():
    with pytest.raises(ValueError):
        stockout_probability(np.array([1]), np.array([5]), np.ones((2, 2)), np.ones((2, 2)))
    with pytest.raises(ValueError):
        stockout_probability(np.array([1]), np.array([5]), -np.ones((1, 2)), np.ones((1, 2)))


def _stations():
    return pd.DataFrame({"station_id": list("abcd"), "station_name": list("ABCD"), "capacity": [20, 20, 20, 20],
                         "num_bikes_available": [0, 3, 18, 10], "num_docks_available": [20, 17, 2, 10],
                         "pred_departures": [1.0, 6.0, 0.5, 1.0], "pred_arrivals": [1.0, 0.5, 4.0, 1.0]})


def test_point_rule_reproduces_notebook_04():
    out = assess(_stations(), RiskConfig(), method="point")
    assert list(out.risk) == ["vide maintenant", "risque vide", "risque saturee", "ok"]
    assert list(np.sign(out.bikes_to_move)) == [1, 1, -1, 0]
    assert out.priority_rank.min() == 1 and out.loc[out.risk == "ok", "priority_score"].eq(0).all()
    assert point_projection([3], [20], [6.0], [0.5])[0] == 0      # bounded at 0


def test_probabilistic_assessment_flags_the_draining_station():
    df = _stations()
    lam_dep = np.repeat(df[["pred_departures"]].to_numpy() / 2, 2, axis=1)
    lam_arr = np.repeat(df[["pred_arrivals"]].to_numpy() / 2, 2, axis=1)
    out = assess(df, RiskConfig(n_sims=3000), "probabilistic", lam_dep, lam_arr)
    assert out.loc[1, "p_empty"] > 0.5 and out.loc[1, "risk"] == "risque vide"
    assert out.loc[3, "risk"] == "ok" and out.loc[3, "p_empty"] < 0.1
    with pytest.raises(ValueError):
        assess(df, method="probabilistic")                  # rates are mandatory
    with pytest.raises(ValueError):                         # no working dock at all
        assess(df.assign(num_bikes_available=0, num_docks_available=0), method="point")


def test_usable_capacity_bounds_the_stock_not_the_nominal_capacity():
    # nominal 20 docks, but 5 hold broken bikes: 3 bikes + 12 free docks = 15 usable -> fills up much sooner
    df = pd.DataFrame({"station_id": ["x"], "station_name": ["X"], "capacity": [20], "num_bikes_available": [3],
                       "num_docks_available": [12], "pred_departures": [0.0], "pred_arrivals": [14.0]})
    lam_arr = np.full((1, 2), 7.0)
    out = assess(df, RiskConfig(n_sims=4000), "probabilistic", np.zeros((1, 2)), lam_arr)
    assert out.usable_capacity.iloc[0] == 15
    nominal_p_full = stockout_probability(np.array([3]), np.array([20]), np.zeros((1, 2)), lam_arr,
                                          RiskConfig(n_sims=4000))[1][0]
    assert out.p_full.iloc[0] > nominal_p_full + 0.2      # nominal capacity would hide most of the risk


# ------------------------------------------------------------------ features
def test_feature_grid_uses_spark_day_of_week_and_zero_fills_unknown_encodings():
    st = pd.DataFrame({"station_id": ["a", "new"], "capacity": [10, None], "lat": [40.7, 40.8], "lon": [-74, -74]})
    hours = pd.DataFrame({"hour_ts": ["2026-10-04 08:20", "2026-10-05 08:00"], "temperature_c": [20.0, None]})
    enc = pd.DataFrame({"station_id": ["a"], "hour": [8], "is_weekend": [1], "enc_dep_station_hour": [7.0]})
    enc_st = pd.DataFrame({"station_id": ["a"], "enc_dep_station": [3.0]})
    g = build_feature_grid(st, hours, enc, enc_st).set_index(["station_id", "hour_ts"])
    sunday, monday = pd.Timestamp("2026-10-04 08:00"), pd.Timestamp("2026-10-05 08:00")
    assert g.loc[("a", sunday), "dow"] == 1 and g.loc[("a", sunday), "is_weekend"] == 1
    assert g.loc[("a", monday), "dow"] == 2 and g.loc[("a", monday), "enc_dep_station_hour"] == 0
    assert g.loc[("a", sunday), "enc_dep_station_hour"] == 7.0
    assert g.loc[("new", sunday), "enc_dep_station"] == 0 and g.loc[("new", sunday), "capacity"] == 0
    assert g.loc[("a", monday), "temperature_c"] == 15.0          # missing weather -> training default
    with pytest.raises(ValueError):
        build_feature_grid(st.iloc[:0], hours, enc, enc_st)


# ------------------------------------------------------------------ routing
def _candidates(n=24, seed=3):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"station_id": [f"s{i}" for i in range(n)], "station_name": [f"S{i}" for i in range(n)],
                         "lat": 40.72 + rng.random(n) * 0.06, "lon": -74.0 + rng.random(n) * 0.05,
                         "delta": rng.choice([-15, -8, 6, 12, 25], n), "value": rng.random(n) * 100})


def _check_plan(plan: pd.DataFrame, cfg: FleetConfig):
    for _, r in plan.groupby("truck"):
        load = -r.delta.cumsum()
        assert (load >= 0).all() and (load <= cfg.truck_capacity).all()
        assert list(r.load_after) == list(load)
        assert r.minutes_cum.max() <= cfg.shift_minutes + 1e-6
    assert plan.station_id.is_unique


def test_greedy_and_optimised_plans_respect_load_and_shift():
    cfg = FleetConfig(n_trucks=2, truck_capacity=20, shift_minutes=120, time_limit_s=3)
    cand = _candidates()
    g, kg = greedy_plan(cand, cfg)
    o, ko = optimise_plan(cand, cfg)
    _check_plan(g, cfg)
    _check_plan(o, cfg)
    assert o.delta.abs().max() <= cfg.truck_capacity                 # needs above a truck load are capped
    assert ko["value_served"] >= kg["value_served"] * 0.98            # never meaningfully worse than the baseline


def test_routing_edge_cases():
    assert np.allclose(np.diag(distance_matrix_km([40.7, 40.8], [-74, -74])), 0)
    d = distance_matrix_km([40.70, 40.71], [-74.0, -74.0], detour=1.0)[0, 1]
    assert 1.0 < d < 1.2                                              # 0.01 deg of latitude ~ 1.11 km
    empty = _candidates().assign(delta=0)
    plan, kpi = optimise_plan(empty)
    assert plan.empty and kpi["stops"] == 0
    with pytest.raises(ValueError):
        greedy_plan(pd.DataFrame({"station_id": ["x"]}))
    prio = pd.DataFrame({"station_id": ["a", "b", "c"], "station_name": list("ABC"), "lat": [40.7] * 3,
                         "lon": [-74.0] * 3, "bikes_to_move": [5, 0, -3], "priority_score": [10.0, 0, 4.0],
                         "priority_rank": [1, 3, 2], "risk": ["risque vide", "ok", "risque saturee"]})
    assert list(candidates_from_priority(prio).station_id) == ["a", "c"]


# ------------------------------------------------------------------ evaluation
def test_classification_metrics():
    y = np.array([1, 1, 0, 0, 0, 1], bool)
    m = ev.confusion(y, np.array([1, 0, 1, 0, 0, 1], bool))
    assert (m["tp"], m["fp"], m["fn"], m["tn"]) == (2, 1, 1, 2)
    assert m["precision"] == pytest.approx(2 / 3) and m["recall"] == pytest.approx(2 / 3)
    assert ev.pr_auc(y, y.astype(float)) == pytest.approx(1.0)
    assert ev.pr_auc(y, np.zeros(6)) == pytest.approx(y.mean())       # no ranking information = prevalence
    assert np.isnan(ev.pr_auc(np.zeros(3, bool), np.ones(3)))
    assert ev.brier(y, y.astype(float)) == 0
    rel = ev.reliability(y, np.array([.9, .8, .1, .2, .1, .7]), bins=2)
    assert rel.n.sum() == 6 and rel.observed.iloc[-1] == 1.0
    assert ev.best_threshold(y, np.array([.9, .8, .1, .2, .1, .7])) <= 0.7
