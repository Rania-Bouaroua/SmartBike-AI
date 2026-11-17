"""Stock-out risk: will a station run out of bikes (empty) or docks (full) within the horizon?

Two methods, evaluated against each other by `scripts/backtest_alerts.py`:

1. **point** (original rule of notebook 04): project the stock with the *expected* demand,
       projected = bikes_now - E[departures over H] + E[arrivals over H]
   and alert when projected <= max(2, 10 % capacity). Simple, but blind to variance: a busy station with a
   balanced expected flow is never flagged, although its stock swings widely.

2. **probabilistic**: the demand models are trained with a Poisson loss, so they predict the *rate* of a Poisson
   process. We simulate the stock path in short steps (default 10 min) with Poisson departures/arrivals, bounded by
   0 and the capacity (a rider cannot take a bike from an empty station or return one to a full station), and
   estimate P(stock hits 0 within H) and P(stock hits capacity within H). This is a path probability - what
   actually matters to a rider - not an end-of-horizon estimate.

Both return the same columns so the app, the API and Power BI do not care which method produced them.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

RISK_LABELS = ("vide maintenant", "saturee maintenant", "risque vide", "risque saturee", "ok")


@dataclass(frozen=True)
class RiskConfig:
    horizon_h: int = 2
    low_frac: float = 0.10        # point rule: alert when projected stock <= max(min_units, low_frac * capacity)
    min_units: int = 2
    target_frac: float = 0.50     # rebalance to half full
    p_alert: float = 0.50         # probabilistic rule: alert when P(stock-out within H) >= p_alert
    steps_per_hour: int = 6       # Monte Carlo resolution (10-minute steps)
    n_sims: int = 1000
    seed: int = 42

    def threshold(self, capacity: np.ndarray) -> np.ndarray:
        return np.maximum(self.min_units, self.low_frac * np.asarray(capacity, dtype=float))


def usable_capacity(bikes: np.ndarray, docks: np.ndarray) -> np.ndarray:
    """Docks a rider can actually use right now = bikes available + free docks.

    Not the nominal `capacity`: broken bikes stay locked in docks and some docks are out of service. On the
    17/11/2026 snapshot 90 % of stations had fewer usable docks than nominal (median gap 3; 5,724 disabled bikes),
    and 92 % of full stations were in that case - simulating with the nominal capacity under-estimated P(full)
    (backtest calibration curve, docs/results.md section 10).
    """
    return np.asarray(bikes, dtype=float) + np.asarray(docks, dtype=float)


def point_projection(bikes: np.ndarray, capacity: np.ndarray, dep_total: np.ndarray,
                     arr_total: np.ndarray) -> np.ndarray:
    """Expected stock at the end of the horizon, bounded to [0, capacity]."""
    cap = np.asarray(capacity, dtype=float)
    return np.clip(np.asarray(bikes, float) - np.asarray(dep_total, float) + np.asarray(arr_total, float), 0, cap)


def stockout_probability(bikes: np.ndarray, capacity: np.ndarray, lam_dep: np.ndarray, lam_arr: np.ndarray,
                         cfg: RiskConfig = RiskConfig()) -> tuple[np.ndarray, np.ndarray]:
    """P(empty within H), P(full within H) per station, by Monte Carlo on bounded Poisson stock paths.

    bikes, capacity   shape (n,)
    lam_dep, lam_arr  shape (n, H): expected departures / arrivals for each of the next H hours
    Deterministic for a given cfg.seed. Cost: n * n_sims * H * steps_per_hour Poisson draws (~30 M for the whole
    network, < 1 s).
    """
    bikes = np.asarray(bikes, dtype=np.int64)
    cap = np.asarray(capacity, dtype=np.int64)
    lam_dep = np.atleast_2d(np.asarray(lam_dep, dtype=float))
    lam_arr = np.atleast_2d(np.asarray(lam_arr, dtype=float))
    if lam_dep.shape != lam_arr.shape or lam_dep.shape[0] != bikes.shape[0]:
        raise ValueError(f"shape mismatch: bikes {bikes.shape}, lam_dep {lam_dep.shape}, lam_arr {lam_arr.shape}")
    if (lam_dep < 0).any() or (lam_arr < 0).any():
        raise ValueError("Poisson rates must be >= 0")
    n, horizon = lam_dep.shape
    rng = np.random.default_rng(cfg.seed)
    stock = np.repeat(np.clip(bikes, 0, cap)[:, None], cfg.n_sims, axis=1)
    cap_b = cap[:, None]
    hit_empty = stock == 0
    hit_full = stock >= cap_b
    for h in range(horizon):
        d_rate = (lam_dep[:, h] / cfg.steps_per_hour)[:, None]
        a_rate = (lam_arr[:, h] / cfg.steps_per_hour)[:, None]
        for _ in range(cfg.steps_per_hour):
            stock = np.clip(stock + rng.poisson(a_rate, size=stock.shape) - rng.poisson(d_rate, size=stock.shape),
                            0, cap_b)
            hit_empty |= stock == 0
            hit_full |= stock >= cap_b
    return hit_empty.mean(axis=1), hit_full.mean(axis=1)


def assess(df: pd.DataFrame, cfg: RiskConfig = RiskConfig(), method: str = "probabilistic",
           lam_dep: np.ndarray | None = None, lam_arr: np.ndarray | None = None) -> pd.DataFrame:
    """Risk label, recommended action and priority for every station.

    df needs: num_bikes_available, num_docks_available, pred_departures, pred_arrivals (horizon totals); the stock is
    bounded by the usable capacity (bikes + free docks), see usable_capacity().
    method="probabilistic" also needs the hourly rates lam_dep / lam_arr (n, H); method="point" does not.
    Adds: projected_bikes, projected_docks, fill_ratio_now, p_empty, p_full, risk, bikes_to_move, action,
    priority_score, priority_rank.
    """
    if method not in ("point", "probabilistic"):
        raise ValueError(f"unknown method {method!r}")
    out = df.copy()
    bikes = out["num_bikes_available"].to_numpy()
    docks = out["num_docks_available"].to_numpy()
    cap = usable_capacity(bikes, docks)
    if (cap <= 0).any():
        raise ValueError("usable capacity must be > 0 (filter stations with no working dock first)")
    out["usable_capacity"] = cap
    thr = cfg.threshold(cap)
    out["projected_bikes"] = point_projection(bikes, cap, out.pred_departures, out.pred_arrivals)
    out["projected_docks"] = cap - out["projected_bikes"]
    out["fill_ratio_now"] = (bikes / cap).round(3)

    if method == "probabilistic":
        if lam_dep is None or lam_arr is None:
            raise ValueError("probabilistic method needs hourly rates lam_dep and lam_arr")
        p_empty, p_full = stockout_probability(bikes, cap, lam_dep, lam_arr, cfg)
        will_empty, will_fill = p_empty >= cfg.p_alert, p_full >= cfg.p_alert
    else:
        will_empty = out["projected_bikes"].to_numpy() <= thr
        will_fill = out["projected_docks"].to_numpy() <= thr
        p_empty, p_full = will_empty.astype(float), will_fill.astype(float)
    # when both are likely (tiny, very busy station) keep the more probable one
    empty_wins = p_empty >= p_full
    out["p_empty"], out["p_full"] = np.round(p_empty, 3), np.round(p_full, 3)
    out["risk"] = np.select(
        [bikes == 0, docks == 0, will_empty & empty_wins, will_fill],
        ["vide maintenant", "saturee maintenant", "risque vide", "risque saturee"], default="ok")

    target = np.round(cfg.target_frac * cap)
    proj = out["projected_bikes"].to_numpy()
    empty_side = out["risk"].isin(["vide maintenant", "risque vide"]).to_numpy()
    full_side = out["risk"].isin(["saturee maintenant", "risque saturee"]).to_numpy()
    move = np.where(empty_side, np.maximum(target - proj, 1), np.where(full_side, np.minimum(target - proj, -1), 0))
    out["bikes_to_move"] = np.round(move).astype(int)
    out["action"] = np.select([out.bikes_to_move > 0, out.bikes_to_move < 0],
                              ["ajouter des velos", "retirer des velos"], default="aucune")
    # urgency (already out = 1, else the stock-out probability) x volume x expected activity
    urgency = np.where((bikes == 0) | (docks == 0), 1.0, np.maximum(out.p_empty, out.p_full))
    activity = 1 + out.pred_departures.to_numpy() + out.pred_arrivals.to_numpy()
    out["priority_score"] = np.round(urgency * np.abs(out.bikes_to_move) * activity, 1)
    out["priority_rank"] = out.priority_score.rank(ascending=False, method="first").astype(int)
    return out
