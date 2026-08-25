"""Pooled and conditional discrimination/calibration metrics."""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Sequence

import numpy as np
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score


def safe_auc(y: Sequence[int], p: Sequence[float]) -> float | None:
    yv = np.asarray(y, dtype=int)
    return float(roc_auc_score(yv, p)) if len(yv) and len(np.unique(yv)) == 2 else None


def conditional_auc(y: Sequence[int], p: Sequence[float], periods: Sequence[str]) -> dict:
    grouped: dict[str, list[int]] = defaultdict(list)
    for i, period in enumerate(periods): grouped[str(period)].append(i)
    details, valid = [], []
    for period, idx in sorted(grouped.items()):
        auc = safe_auc([y[i] for i in idx], [p[i] for i in idx])
        details.append({"period": period, "n": len(idx), "auc": auc, "status": "EVALUABLE" if auc is not None else "NOT_EVALUABLE"})
        if auc is not None: valid.append((auc, len(idx)))
    macro = float(np.mean([a for a, _ in valid])) if valid else None
    weighted = float(np.average([a for a, _ in valid], weights=[n for _, n in valid])) if valid else None
    return {"pooled_auc": safe_auc(y, p), "macro_auc": macro,
            "weighted_auc": weighted, "macro_weekly_auc": macro,
            "weighted_weekly_auc": weighted, "periods": details,
            "period_details": details}


def calibration_bins(y: Sequence[int], p: Sequence[float], n_bins: int = 10) -> tuple[float | None, list[dict]]:
    if not y: return None, []
    bins = []
    pv, yv = np.asarray(p, float), np.asarray(y, int)
    edges = np.linspace(0, 1, n_bins + 1)
    ece = 0.0
    for i in range(n_bins):
        mask = (pv >= edges[i]) & ((pv <= edges[i + 1]) if i == n_bins - 1 else (pv < edges[i + 1]))
        n = int(mask.sum())
        if not n: continue
        pred, actual = float(pv[mask].mean()), float(yv[mask].mean())
        ece += n / len(yv) * abs(pred - actual)
        bins.append({"bin_low": float(edges[i]), "bin_high": float(edges[i + 1]), "count": n, "mean_prediction": pred, "event_rate": actual})
    return float(ece), bins


def probability_metrics(y: Sequence[int], p: Sequence[float], periods: Sequence[str]) -> dict:
    clipped = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    ece, bins = calibration_bins(list(y), clipped.tolist())
    disc = conditional_auc(y, clipped, periods)
    return {
        "brier": float(brier_score_loss(y, clipped)), "logloss": float(log_loss(y, clipped, labels=[0, 1])),
        "ece": ece, "calibration_bins": bins, **disc,
    }
