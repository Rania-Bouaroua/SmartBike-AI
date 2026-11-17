"""Metrics for the alert backtest: did the stations we flagged actually run empty / full within the horizon?

Alerts are a rare-event classification problem (a few % of stations stock out in a 2-hour window), so accuracy is
meaningless. We report:
  precision  share of alerts that were right             (operator trust: "when it rings, go")
  recall     share of real stock-outs that were flagged  (service: "how many outages did we miss")
  F1, PR-AUC (threshold-free ranking quality; random = prevalence), Brier score (probability accuracy)
and a reliability table (predicted probability vs observed frequency) to check calibration.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def confusion(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    y_true, y_pred = np.asarray(y_true, bool), np.asarray(y_pred, bool)
    tp = int((y_true & y_pred).sum())
    fp = int((~y_true & y_pred).sum())
    fn = int((y_true & ~y_pred).sum())
    tn = int((~y_true & ~y_pred).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "alerts": tp + fp, "events": tp + fn,
            "precision": precision, "recall": recall, "f1": f1}


def pr_auc(y_true: np.ndarray, score: np.ndarray) -> float:
    """Average precision (area under the precision-recall curve, step-wise, ties handled by score groups)."""
    y_true = np.asarray(y_true, bool)
    score = np.asarray(score, float)
    n_pos = int(y_true.sum())
    if n_pos == 0:
        return float("nan")
    order = np.argsort(-score, kind="mergesort")
    s, y = score[order], y_true[order]
    # evaluate precision/recall only at the last index of each group of tied scores
    last_of_group = np.r_[s[1:] != s[:-1], True]
    tp = np.cumsum(y)[last_of_group]
    k = np.arange(1, len(y) + 1)[last_of_group]
    precision, recall = tp / k, tp / n_pos
    return float(np.sum(np.diff(np.r_[0.0, recall]) * precision))


def brier(y_true: np.ndarray, prob: np.ndarray) -> float:
    return float(np.mean((np.asarray(prob, float) - np.asarray(y_true, float)) ** 2))


def reliability(y_true: np.ndarray, prob: np.ndarray, bins: int = 10) -> pd.DataFrame:
    """Observed event frequency per predicted-probability bin (a calibrated model sits on the diagonal)."""
    df = pd.DataFrame({"p": np.asarray(prob, float), "y": np.asarray(y_true, float)})
    df["bin"] = pd.cut(df.p, np.linspace(0, 1, bins + 1), include_lowest=True)
    out = df.groupby("bin", observed=True).agg(n=("y", "size"), predicted=("p", "mean"), observed=("y", "mean"))
    return out.reset_index().assign(bin=lambda d: d.bin.astype(str))


def best_threshold(y_true: np.ndarray, prob: np.ndarray, grid: np.ndarray | None = None) -> float:
    """Probability cut-off that maximises F1 (to be chosen on a calibration period, never on the test period)."""
    grid = np.round(np.arange(0.05, 0.96, 0.05), 2) if grid is None else grid
    f1s = [confusion(y_true, np.asarray(prob) >= t)["f1"] for t in grid]
    return float(grid[int(np.argmax(f1s))])
