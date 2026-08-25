"""Phase 1.6 within-period negative controls."""
from __future__ import annotations

from typing import Sequence
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .conditional_metrics import conditional_auc


def shuffle_within_period(y: Sequence[int], periods: Sequence[str], rng: np.random.Generator) -> np.ndarray:
    out = np.asarray(y, int).copy()
    for period in sorted(set(periods)):
        idx = np.asarray([i for i, v in enumerate(periods) if v == period])
        out[idx] = rng.permutation(out[idx])
    return out


def _oof_predictions(X: np.ndarray, y: np.ndarray, periods: Sequence[str]) -> np.ndarray:
    """Leave-one-period-out predictions; training is always other periods."""
    pred = np.full(len(y), float(y.mean()))
    for period in sorted(set(periods)):
        test = np.asarray([v == period for v in periods]); train = ~test
        if train.sum() and len(np.unique(y[train])) == 2:
            model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000))
            model.fit(X[train], y[train]); pred[test] = model.predict_proba(X[test])[:, 1]
    return pred


def run_conditional_shuffle(X: np.ndarray, y: Sequence[int], periods: Sequence[str], n_shuffles: int = 1000, seed: int = 20260824) -> dict:
    """Randomization test against one fixed, period-OOF score vector.

    Fitting the score once is intentional: the null permutes labels while
    holding the audited prediction rule fixed. It preserves period base
    rates exactly and avoids tuning a new model to every randomized target.
    """
    if n_shuffles < 1: raise ValueError("n_shuffles must be positive")
    X, yv = np.asarray(X, float), np.asarray(y, int)
    if len(yv) != len(periods) or X.shape[0] != len(yv): raise ValueError("length mismatch")
    rng, records = np.random.default_rng(seed), []
    fixed_predictions = _oof_predictions(X, yv, periods)
    for _ in range(n_shuffles):
        ys = shuffle_within_period(yv, periods, rng)
        m = conditional_auc(ys, fixed_predictions, periods)
        records.append({"pooled_auc": m["pooled_auc"], "macro_weekly_auc": m["macro_auc"], "weighted_weekly_auc": m["weighted_auc"]})
    def summary(name):
        a = np.asarray([r[name] for r in records if r[name] is not None])
        return {"mean": float(a.mean()), "std": float(a.std()), "p95": float(np.percentile(a, 95)), "max": float(a.max())} if len(a) else {"mean": None, "std": None, "p95": None, "max": None}
    stats = {k: summary(k) for k in records[0]}
    cp = stats["macro_weekly_auc"]["p95"] is not None and stats["macro_weekly_auc"]["p95"] < .55 and stats["weighted_weekly_auc"]["p95"] < .55
    pooled = stats["pooled_auc"]["mean"]
    return {"seed": seed, "n_shuffles": n_shuffles, "records": records, "summary": stats,
            "regime_confounding_detected": pooled is not None and pooled > .55 and cp,
            "conditional_control_status": "PASS" if cp else "FAIL"}
