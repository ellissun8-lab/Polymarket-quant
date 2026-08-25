"""Deterministic descriptive change-point detection (CUSUM + binary split)."""
from __future__ import annotations
import math
from typing import Mapping, Sequence
import numpy as np

METRICS = ("first_opposite_rate", "y30_rate", "median_initial_qty", "median_first_opp_qty", "median_initial_to_opp_seconds", "buy_sell_ratio")


def _cusum(x: np.ndarray, min_segment: int) -> list[tuple[int, float]]:
    if len(x) < 2 * min_segment or np.std(x) == 0: return []
    z = (x - x.mean()) / x.std()
    score = np.abs(np.cumsum(z))[:-1]
    valid = np.arange(min_segment - 1, len(x) - min_segment)
    if not len(valid): return []
    i = int(valid[np.argmax(score[valid])]) + 1
    threshold = max(1.5, math.sqrt(len(x)))
    return [(i, float(score[i - 1]))] if score[i - 1] >= threshold else []


def _binary(x: np.ndarray, min_segment: int) -> list[tuple[int, float]]:
    if len(x) < 2 * min_segment: return []
    best = (0.0, None)
    scale = float(np.std(x))
    if scale == 0: return []
    for i in range(min_segment, len(x) - min_segment + 1):
        score = abs(float(x[:i].mean() - x[i:].mean())) / scale
        if score > best[0]: best = (score, i)
    return [(int(best[1]), best[0])] if best[1] is not None and best[0] >= 1.0 else []


def detect_change_points(surface: Sequence[Mapping], min_segment: int = 3, tolerance: int = 1) -> list[dict]:
    out = []
    for metric in METRICS:
        valid = [(r["period_key"], float(r[metric]), int(r.get("y30_observable_count", 0))) for r in surface if r.get(metric) is not None]
        if len(valid) < 2 * min_segment: continue
        x = np.asarray([v[1] for v in valid])
        found = {"CUSUM": _cusum(x, min_segment), "BINARY_SEGMENTATION": _binary(x, min_segment)}
        candidates = [(method, idx, score) for method, vals in found.items() for idx, score in vals]
        for method, idx, score in candidates:
            support = sum(any(abs(idx - other_idx) <= tolerance for other_idx, _ in vals) for m, vals in found.items() if m != method)
            out.append({"metric": metric, "break_timestamp": valid[idx][0], "method": method, "score": score,
                        "support_count": support + 1, "status": "SUPPORTED_BREAK" if support else "CANDIDATE_BREAK"})
    return out

