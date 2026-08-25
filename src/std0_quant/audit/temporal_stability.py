"""Audit 4 - temporal stability (Phase 1.5, spec section 7).

Research question: are the Phase 1 headline rates (FirstOpposite rate,
observable Y30 positive rate) stable over the sample period, or are they
driven by a few unusual days/weeks? Rates computed on a short window can
reflect a regime that does not generalize; the audit quantifies week-to-
week movement instead of assuming stability.

Buckets (UTC, by ``market_start_ms``):

* daily buckets - descriptive granularity;
* ISO-week buckets (``YYYY-Www``) - the stability unit.

Per bucket: clean market count, FirstOpposite count/rate, Y30 observable /
positive / negative / censored counts, observable positive rate, medians.

Low-N weeks (< ``min_weekly_n`` clean markets, default 100) are KEPT and
reported but flagged ``low_n`` and excluded from the stability rule - a
small week is noisy, not evidence of instability.

Rule (descriptive, not a significance test): WARN when the spread of
weekly observable positive rates (max-min, over non-low-N weeks) is at
least ``TEMPORAL_WARN_SPREAD_PP`` (10pp) or a consecutive-week change is
at least ``TEMPORAL_WARN_JUMP_PP`` (10pp). Otherwise PASS.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

DEFAULT_MIN_WEEKLY_N = 100
TEMPORAL_WARN_SPREAD_PP = 10.0
TEMPORAL_WARN_JUMP_PP = 10.0


def utc_day_key(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime(
        "%Y-%m-%d"
    )


def iso_week_key(ms: int) -> str:
    iso = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isocalendar()
    return f"{iso[0]:04d}-W{iso[1]:02d}"


@dataclass
class BucketStats:
    key: str
    clean_market_count: int = 0
    first_opposite_count: int = 0
    y30_observable: int = 0
    y30_positive: int = 0
    y30_negative: int = 0
    y30_censored: int = 0
    median_initial_qty: float | None = None
    median_seconds_to_initial: float | None = None
    low_n: bool = False

    @property
    def first_opposite_rate(self) -> float | None:
        if not self.clean_market_count:
            return None
        return self.first_opposite_count / self.clean_market_count

    @property
    def positive_rate_observable(self) -> float | None:
        if not self.y30_observable:
            return None
        return self.y30_positive / self.y30_observable


@dataclass
class WeekOverWeekChange:
    from_week: str
    to_week: str
    from_rate: float
    to_rate: float
    delta_pp: float


@dataclass
class TemporalStabilityResult:
    daily: list[BucketStats] = field(default_factory=list)
    weekly: list[BucketStats] = field(default_factory=list)
    n_missing_start: int = 0
    min_weekly_n: int = DEFAULT_MIN_WEEKLY_N
    # weekly-rate stats over non-low-N weeks only
    rate_min: float | None = None
    rate_max: float | None = None
    rate_mean: float | None = None
    rate_std: float | None = None
    spread_pp: float | None = None
    week_over_week: list[WeekOverWeekChange] = field(default_factory=list)
    max_abs_week_over_week_pp: float | None = None
    extreme_weeks: list[str] = field(default_factory=list)
    n_low_n_weeks: int = 0

    @property
    def status(self) -> str:
        eligible = [w for w in self.weekly if not w.low_n]
        if len(eligible) < 2 or self.spread_pp is None:
            return "NOT_COMPUTABLE"
        if (
            self.spread_pp >= TEMPORAL_WARN_SPREAD_PP
            or (
                self.max_abs_week_over_week_pp is not None
                and self.max_abs_week_over_week_pp >= TEMPORAL_WARN_JUMP_PP
            )
        ):
            return "WARN"
        return "PASS"


def _median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _bucket_rows(rows: Sequence[Mapping[str, Any]], key_fn: Callable[[int], str]):
    buckets: dict[str, list[Mapping[str, Any]]] = {}
    missing = 0
    for row in rows:
        start = row.get("market_start_ms")
        if start is None:
            missing += 1
            continue
        buckets.setdefault(key_fn(int(start)), []).append(row)
    return buckets, missing


def _bucket_stats(key: str, rows: Sequence[Mapping[str, Any]]) -> BucketStats:
    stats = BucketStats(key=key)
    qty_values: list[float] = []
    sec_values: list[float] = []
    for row in rows:
        stats.clean_market_count += 1
        if row.get("first_opp_end_ms") is not None:
            stats.first_opposite_count += 1
            if row.get("y30_horizon_eligible"):
                stats.y30_observable += 1
                if row.get("y30") == 1:
                    stats.y30_positive += 1
                else:
                    stats.y30_negative += 1
            else:
                stats.y30_censored += 1
        qty = row.get("initial_qty")
        if isinstance(qty, (int, float)):
            qty_values.append(float(qty))
        start = row.get("market_start_ms")
        first_ts = row.get("initial_first_timestamp_ms")
        if start is not None and first_ts is not None:
            sec_values.append((first_ts - start) / 1000.0)
    stats.median_initial_qty = _median(qty_values)
    stats.median_seconds_to_initial = _median(sec_values)
    return stats


def run_temporal_stability(
    clean_rows: Sequence[Mapping[str, Any]],
    min_weekly_n: int = DEFAULT_MIN_WEEKLY_N,
) -> TemporalStabilityResult:
    """Daily + ISO-week buckets over the clean set (read-only)."""
    result = TemporalStabilityResult(min_weekly_n=min_weekly_n)

    day_buckets, missing = _bucket_rows(clean_rows, utc_day_key)
    result.daily = [
        _bucket_stats(key, day_buckets[key]) for key in sorted(day_buckets)
    ]
    week_buckets, _ = _bucket_rows(clean_rows, iso_week_key)
    result.n_missing_start = missing

    for key in sorted(week_buckets):
        stats = _bucket_stats(key, week_buckets[key])
        stats.low_n = stats.clean_market_count < min_weekly_n
        if stats.low_n:
            result.n_low_n_weeks += 1
        result.weekly.append(stats)

    eligible_rates = [
        w.positive_rate_observable
        for w in result.weekly
        if not w.low_n and w.positive_rate_observable is not None
    ]
    if len(eligible_rates) >= 2:
        result.rate_min = min(eligible_rates)
        result.rate_max = max(eligible_rates)
        result.rate_mean = sum(eligible_rates) / len(eligible_rates)
        var = sum((r - result.rate_mean) ** 2 for r in eligible_rates) / len(
            eligible_rates
        )
        result.rate_std = var ** 0.5
        result.spread_pp = (result.rate_max - result.rate_min) * 100

    eligible_weeks = [
        w for w in result.weekly
        if not w.low_n and w.positive_rate_observable is not None
    ]
    for prev, cur in zip(eligible_weeks, eligible_weeks[1:]):
        delta = (cur.positive_rate_observable - prev.positive_rate_observable)
        result.week_over_week.append(
            WeekOverWeekChange(
                from_week=prev.key,
                to_week=cur.key,
                from_rate=prev.positive_rate_observable,
                to_rate=cur.positive_rate_observable,
                delta_pp=delta * 100,
            )
        )
    if result.week_over_week:
        result.max_abs_week_over_week_pp = max(
            abs(c.delta_pp) for c in result.week_over_week
        )

    if result.rate_mean is not None and result.rate_std is not None:
        for w in eligible_weeks:
            if (
                abs(w.positive_rate_observable - result.rate_mean)
                > 2 * result.rate_std
            ):
                result.extreme_weeks.append(w.key)
    return result
