"""Daily/weekly base-rate surfaces for the Phase 1.6 audit."""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone
from statistics import median
from typing import Any, Mapping, Sequence


def _key(ms: int, period: str) -> str:
    dt = datetime.fromtimestamp(ms / 1000, timezone.utc)
    if period == "daily":
        return dt.strftime("%Y-%m-%d")
    iso = dt.isocalendar()
    return f"{iso.year:04d}-W{iso.week:02d}"


def wilson_interval(positive: int, total: int, z: float = 1.959963984540054) -> tuple[float | None, float | None]:
    if total <= 0:
        return None, None
    p = positive / total
    den = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / den
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / den
    return max(0.0, centre - half), min(1.0, centre + half)


def _med(rows: Sequence[Mapping[str, Any]], name: str) -> float | None:
    values = [float(r[name]) for r in rows if isinstance(r.get(name), (int, float)) and math.isfinite(float(r[name]))]
    return median(values) if values else None


def build_regime_surface(rows: Sequence[Mapping[str, Any]], period: str, min_n: int = 100) -> list[dict[str, Any]]:
    if period not in {"daily", "weekly"}:
        raise ValueError("period must be daily or weekly")
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("market_start_ms") is not None:
            groups[_key(int(row["market_start_ms"]), period)].append(row)
    out = []
    for key, group in sorted(groups.items()):
        clean = [r for r in group if r.get("clean_flag")]
        fo = [r for r in clean if r.get("first_opp_end_ms") is not None]
        obs = [r for r in fo if r.get("y30_horizon_eligible")]
        pos = sum(r.get("y30") == 1 for r in obs)
        neg = len(obs) - pos
        censored = len(fo) - len(obs)
        lo, hi = wilson_interval(pos, len(obs))
        intervals = [(int(r["first_opp_end_ms"]) - int(r["initial_first_timestamp_ms"])) / 1000 for r in fo if r.get("initial_first_timestamp_ms") is not None]
        buy = sum(int(r.get("n_buy_fills") or 0) for r in clean)
        sell = sum(int(r.get("n_sell_fills") or 0) for r in clean)
        out.append({
            "period": period, "period_key": key, "market_count": len(group),
            "clean_count": len(clean), "first_opposite_count": len(fo),
            "first_opposite_rate": len(fo) / len(clean) if clean else None,
            "y30_observable_count": len(obs), "y30_positive": pos,
            "y30_negative": neg, "y30_censored": censored,
            "y30_rate": pos / len(obs) if obs else None,
            "y30_ci_low": lo, "y30_ci_high": hi, "low_n": len(obs) < min_n,
            "median_initial_qty": _med(fo, "initial_qty"),
            "median_first_opp_qty": _med(fo, "first_opp_qty"),
            "median_initial_to_opp_seconds": median(intervals) if intervals else None,
            "median_n_buy_fills": _med(clean, "n_buy_fills"),
            "median_n_sell_fills": _med(clean, "n_sell_fills"),
            "buy_sell_ratio": buy / sell if sell else None,
        })
    return out

