"""Backtest the empty / full alerts against what really happened (steps 1 + 2 of the roadmap).

Question answered: "when the system says a station will run empty (or full) within H hours, is it right, and how
many real stock-outs does it miss?" - the claim the whole project rests on.

Method (walk-forward, no look-ahead):
  * ground truth = recorded GBFS history (scripts/gbfs_history.py, or the Eventhouse table in Fabric);
  * every `--every` minutes (an "origin" t0) we rebuild the alert exactly as production would: the station state
    at t0, the weather of the next hours (Open-Meteo), the champion demand models, then three alert methods:
      naive          stock already low: bikes <= max(2, 10 % capacity)              (no forecast at all)
      point          notebook 04 rule: expected stock at t0+H <= threshold          (forecast, no uncertainty)
      probabilistic  P(stock hits 0 within H) >= p*, Monte Carlo on Poisson demand   (forecast + uncertainty)
  * truth: the station was observed empty (bikes = 0 while renting) at least once in (t0, t0+H]; same for full.
  * stations already empty (resp. full) at t0 are excluded: those alerts are trivial, we score the *predictive* part.
  * the probability cut-off p* is tuned on the first half of the origins only and evaluated on the second half.

Outputs: Gold tables gold_alert_backtest (metrics), gold_alert_calibration, gold_alert_backtest_rows,
and docs/figures/fig10_alert_backtest.png. Re-run any time: more history -> tighter estimates.

Usage:  python scripts/backtest_alerts.py [--horizon 2] [--every 15] [--sims 1000]
"""
from __future__ import annotations

import argparse
import pathlib
import sys

import numpy as np
import pandas as pd
import requests

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import common  # noqa: E402
from smartbike import evaluation as ev  # noqa: E402
from smartbike.risk import RiskConfig, point_projection, stockout_probability, usable_capacity  # noqa: E402

HISTORY = ROOT / "data" / "raw" / "gbfs_history"
MAX_GAP = pd.Timedelta(minutes=15)       # truth needs a dense record of the window


def load_history() -> pd.DataFrame:
    files = sorted(HISTORY.glob("status_*.parquet"))
    if not files:
        raise SystemExit(f"no history in {HISTORY} - run scripts/gbfs_history.py first")
    h = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    st = common.table("silver_stations")[["station_id", "gbfs_station_id", "station_name", "capacity", "lat", "lon"]]
    h = h.rename(columns={"station_id": "gbfs_station_id"}).merge(st, on="gbfs_station_id", how="inner")
    return h[h.capacity > 0]


def weather(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Hourly NYC weather covering [start, end] (local time). Falls back to model defaults when offline."""
    try:
        r = requests.get("https://api.open-meteo.com/v1/forecast", timeout=30, params={
            "latitude": 40.7306, "longitude": -73.9866, "timezone": common.NY_TZ, "past_days": 7, "forecast_days": 2,
            "hourly": "temperature_2m,precipitation,wind_speed_10m,relative_humidity_2m"})
        r.raise_for_status()
        f = r.json()["hourly"]
        wx = pd.DataFrame({"hour_ts": pd.to_datetime(f["time"]), "temperature_c": f["temperature_2m"],
                           "precipitation_mm": f["precipitation"], "wind_kmh": f["wind_speed_10m"],
                           "humidity_pct": f["relative_humidity_2m"]})
    except requests.RequestException as exc:
        print(f"[warn] weather unavailable ({exc}); using defaults")
        wx = pd.DataFrame({"hour_ts": pd.date_range(start.floor("h"), end.ceil("h"), freq="h")})
    return wx[(wx.hour_ts >= start.floor("h")) & (wx.hour_ts <= end.ceil("h"))]


def origins(snap_times: pd.Series, horizon: pd.Timedelta, every: pd.Timedelta) -> list[pd.Timestamp]:
    """Snapshot times with a dense record of the following horizon, spaced at least `every` apart."""
    times = pd.Series(sorted(snap_times.unique()))
    out, last = [], None
    for t0 in times:
        if last is not None and t0 - last < every:
            continue
        window = times[(times > t0) & (times <= t0 + horizon)]
        if window.empty or window.iloc[-1] < t0 + horizon - MAX_GAP:
            continue
        # compare Timedeltas, not raw integers: pandas 3 stores datetimes in us by default, Timestamp.value is ns
        gaps = pd.concat([pd.Series([window.iloc[0] - t0]), window.diff().dropna()])
        if gaps.max() > MAX_GAP:
            continue
        out.append(t0)
        last = t0
    return out


def score_origin(hist: pd.DataFrame, t0: pd.Timestamp, wx: pd.DataFrame, cfg: RiskConfig) -> pd.DataFrame:
    H = pd.Timedelta(hours=cfg.horizon_h)
    cur = hist[(hist.snapshot_utc == t0) & (hist.is_renting == 1)].drop_duplicates("station_id")
    cur = cur[cur.num_bikes_available + cur.num_docks_available > 0].reset_index(drop=True)
    local0 = t0.tz_convert(common.NY_TZ).tz_localize(None)
    hours = wx[(wx.hour_ts >= local0.floor("h"))].head(cfg.horizon_h).reset_index(drop=True)
    if len(hours) < cfg.horizon_h:
        hours = pd.DataFrame({"hour_ts": pd.date_range(local0.floor("h"), periods=cfg.horizon_h, freq="h")})
    lam_dep, lam_arr = common.hourly_rates(cur, hours)
    bikes = cur.num_bikes_available.to_numpy()
    docks = cur.num_docks_available.to_numpy()
    cap = usable_capacity(bikes, docks)            # same bound as production (smartbike.risk.assess)
    thr = cfg.threshold(cap)
    proj = point_projection(bikes, cap, lam_dep.sum(1), lam_arr.sum(1))
    p_empty, p_full = stockout_probability(bikes, cap, lam_dep, lam_arr, cfg)

    win = hist[(hist.snapshot_utc > t0) & (hist.snapshot_utc <= t0 + H)]
    truth = win.assign(e=(win.num_bikes_available == 0) & (win.is_renting == 1),
                       f=(win.num_docks_available == 0) & (win.is_returning == 1)) \
               .groupby("station_id").agg(empty_within=("e", "any"), full_within=("f", "any"))
    out = cur[["station_id", "station_name", "capacity", "num_bikes_available", "num_docks_available"]].assign(
        origin_utc=t0, pred_departures=lam_dep.sum(1), pred_arrivals=lam_arr.sum(1), projected_bikes=proj,
        threshold=thr, p_empty=p_empty, p_full=p_full,
        naive_empty=bikes <= thr, naive_full=docks <= thr,
        point_empty=proj <= thr, point_full=(cap - proj) <= thr)
    return out.merge(truth, on="station_id", how="inner")


def evaluate(rows: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    times = sorted(rows.origin_utc.unique())
    split = times[len(times) // 2] if len(times) > 1 else times[0]
    calib, test = rows[rows.origin_utc < split], rows[rows.origin_utc >= split]
    if calib.empty:                      # too little history yet: tune and test on the same origins (flagged)
        calib = test
    metrics, cal_tables = [], []
    for event, already in (("empty", "num_bikes_available"), ("full", "num_docks_available")):
        y_col, p_col = f"{event}_within", f"p_{event}"
        c, t = calib[calib[already] > 0], test[test[already] > 0]
        p_star = ev.best_threshold(c[y_col], c[p_col])
        usable = t.num_bikes_available + t.num_docks_available
        naive_score = -t.num_bikes_available / usable if event == "empty" else -t.num_docks_available / usable
        point_score = -t.projected_bikes if event == "empty" else t.projected_bikes
        for method, pred, score in (("naive", t[f"naive_{event}"], naive_score),
                                    ("point", t[f"point_{event}"], point_score),
                                    ("probabilistic", t[p_col] >= p_star, t[p_col])):
            m = ev.confusion(t[y_col], pred)
            m.update(event=event, method=method, pr_auc=ev.pr_auc(t[y_col], score),
                     brier=ev.brier(t[y_col], t[p_col]) if method == "probabilistic" else np.nan,
                     p_threshold=p_star if method == "probabilistic" else np.nan,
                     prevalence=float(t[y_col].mean()), n=len(t), origins_test=t.origin_utc.nunique(),
                     origins_calib=c.origin_utc.nunique(), same_period=bool(calib is test))
            metrics.append(m)
        cal_tables.append(ev.reliability(t[y_col], t[p_col]).assign(event=event))
    return pd.DataFrame(metrics), pd.concat(cal_tables, ignore_index=True)


def save(df: pd.DataFrame, name: str) -> None:
    out = common.TABLES / name
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.parquet"):
        old.unlink()
    df.to_parquet(out / "part-0.parquet", index=False)


def figure(metrics: pd.DataFrame, calib: pd.DataFrame, path: pathlib.Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.4))
    colors = {"naive": "#94A3B8", "point": "#8DDCFF", "probabilistic": "#2563EB"}
    for ax, event in zip(axes[:2], ("empty", "full")):
        m = metrics[metrics.event == event].set_index("method")
        x = np.arange(3)
        for k, (method, row) in enumerate(m.iterrows()):
            ax.bar(x + (k - 1) * 0.26, [row.precision, row.recall, row.pr_auc], 0.26, label=method,
                   color=colors[method])
        ax.set_xticks(x, ["précision", "rappel", "PR-AUC"])
        ax.set_ylim(0, 1)
        ax.set_title(f"Alerte « {'vide' if event == 'empty' else 'pleine'} » à {int(HORIZON_H)} h "
                     f"(prévalence {m.prevalence.iloc[0]:.1%})")
        ax.legend(frameon=False, fontsize=8)
    ax = axes[2]
    for event, col in (("empty", "#2563EB"), ("full", "#0B1020")):
        c = calib[calib.event == event]
        ax.plot(c.predicted, c.observed, "o-", color=col, label=f"P({'vide' if event == 'empty' else 'pleine'})")
    ax.plot([0, 1], [0, 1], "--", color="#94A3B8", lw=1)
    ax.set_xlabel("probabilité prévue")
    ax.set_ylabel("fréquence observée")
    ax.set_title("Calibration")
    ax.legend(frameon=False)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=140)


HORIZON_H = 2


def main() -> int:
    global HORIZON_H
    ap = argparse.ArgumentParser(description="Backtest stock-out alerts against recorded GBFS history")
    ap.add_argument("--horizon", type=int, default=2)
    ap.add_argument("--every", type=int, default=15, help="minutes between origins")
    ap.add_argument("--sims", type=int, default=1000)
    args = ap.parse_args()
    HORIZON_H = args.horizon
    cfg = RiskConfig(horizon_h=args.horizon, n_sims=args.sims)
    hist = load_history()
    times = hist.snapshot_utc.drop_duplicates().sort_values()
    print(f"history: {times.size} snapshots, {times.min()} -> {times.max()}")
    orig = origins(times, pd.Timedelta(hours=args.horizon), pd.Timedelta(minutes=args.every))
    if not orig:
        print(f"not enough history yet: need >= {args.horizon} h of snapshots at <= {MAX_GAP} spacing")
        return 1
    local = [t.tz_convert(common.NY_TZ).tz_localize(None) for t in (orig[0], orig[-1])]
    wx = weather(local[0] - pd.Timedelta(hours=1), local[1] + pd.Timedelta(hours=args.horizon + 1))
    rows = pd.concat([score_origin(hist, t0, wx, cfg) for t0 in orig], ignore_index=True)
    metrics, calib = evaluate(rows)
    metrics.insert(0, "horizon_h", args.horizon)
    save(metrics, "gold_alert_backtest")
    save(calib, "gold_alert_calibration")
    save(rows, "gold_alert_backtest_rows")
    figure(metrics, calib, ROOT / "docs" / "figures" / "fig10_alert_backtest.png")
    pd.set_option("display.width", 200)
    print(f"origins: {len(orig)} ({orig[0]} -> {orig[-1]}), station-windows scored: {len(rows):,}")
    print(metrics[["event", "method", "alerts", "events", "precision", "recall", "f1", "pr_auc", "brier",
                   "p_threshold", "prevalence", "n", "same_period"]].round(3).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
