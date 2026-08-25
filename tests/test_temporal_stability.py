"""Tests L - Audit 4: temporal stability (daily + ISO week buckets)."""

from __future__ import annotations

import pytest

from std0_quant.audit.temporal_stability import (
    DEFAULT_MIN_WEEKLY_N,
    iso_week_key,
    run_temporal_stability,
    utc_day_key,
)


def row(cid, start_ms, first_opp=True, y30=1, eligible=True, qty=100.0):
    return {
        "condition_id": cid,
        "market_start_ms": start_ms,
        "market_end_ms": start_ms + 300_000,
        "initial_first_timestamp_ms": start_ms + 10_000,
        "initial_qty": qty if first_opp else None,
        "first_opp_end_ms": start_ms + 60_000 if first_opp else None,
        "y30": y30 if first_opp else None,
        "y30_horizon_eligible": eligible if first_opp else False,
    }


# A fixed Monday so ISO week math is deterministic:
# 2026-01-05 is an ISO Monday (week 2 of 2026).
MONDAY = 1_767_571_200_000  # 2026-01-05T00:00:00Z
DAY = 86_400_000
WEEK = 7 * DAY


class TestKeys:
    def test_day_key(self) -> None:
        assert utc_day_key(MONDAY) == "2026-01-05"

    def test_iso_week_key_monday(self) -> None:
        assert iso_week_key(MONDAY) == "2026-W02"

    def test_iso_week_key_sunday_same_week(self) -> None:
        assert iso_week_key(MONDAY + 6 * DAY) == "2026-W02"

    def test_iso_week_key_next_monday(self) -> None:
        assert iso_week_key(MONDAY + WEEK) == "2026-W03"


class TestBucketing:
    def test_daily_buckets_sorted(self) -> None:
        rows = [
            row("a", MONDAY + DAY),
            row("b", MONDAY),
            row("c", MONDAY),
        ]
        result = run_temporal_stability(rows, min_weekly_n=1)
        assert [d.key for d in result.daily] == ["2026-01-05", "2026-01-06"]
        assert result.daily[0].clean_market_count == 2
        assert result.daily[1].clean_market_count == 1

    def test_weekly_low_n_flag(self) -> None:
        rows = [row("a", MONDAY)]
        result = run_temporal_stability(rows, min_weekly_n=100)
        assert len(result.weekly) == 1
        assert result.weekly[0].low_n is True
        assert result.n_low_n_weeks == 1
        assert result.status == "NOT_COMPUTABLE"

    def test_missing_start_skipped(self) -> None:
        r = row("a", MONDAY)
        r["market_start_ms"] = None
        result = run_temporal_stability([r], min_weekly_n=1)
        assert result.n_missing_start == 1
        assert result.daily == []
        assert result.weekly == []


class TestBucketStats:
    def test_counts_and_rates(self) -> None:
        rows = [
            row("a", MONDAY, first_opp=True, y30=1),
            row("b", MONDAY, first_opp=True, y30=0),
            row("c", MONDAY, first_opp=True, y30=1, eligible=False),
            row("d", MONDAY, first_opp=False),
        ]
        result = run_temporal_stability(rows, min_weekly_n=1)
        week = result.weekly[0]
        assert week.clean_market_count == 4
        assert week.first_opposite_count == 3
        assert week.first_opposite_rate == pytest.approx(0.75)
        assert week.y30_observable == 2
        assert week.y30_positive == 1
        assert week.y30_negative == 1
        assert week.y30_censored == 1
        assert week.positive_rate_observable == pytest.approx(0.5)
        assert week.median_initial_qty == pytest.approx(100.0)
        assert week.median_seconds_to_initial == pytest.approx(10.0)

    def test_median_over_multiple_values(self) -> None:
        rows = [
            row("a", MONDAY, qty=10.0),
            row("b", MONDAY, qty=20.0),
            row("c", MONDAY, qty=30.0),
            row("d", MONDAY, qty=40.0),
        ]
        result = run_temporal_stability(rows, min_weekly_n=1)
        assert result.weekly[0].median_initial_qty == pytest.approx(25.0)
        assert result.weekly[0].median_seconds_to_initial == pytest.approx(10.0)


class TestStabilityRule:
    def _weeks(self, rates_by_week):
        """rates_by_week: list of (n_markets, positive_rate) per week."""
        rows = []
        for w, (n, rate) in enumerate(rates_by_week):
            positives = round(n * rate)
            for i in range(n):
                rows.append(
                    row(f"w{w}m{i}", MONDAY + w * WEEK,
                        y30=1 if i < positives else 0)
                )
        return rows

    def test_stable_passes(self) -> None:
        rows = self._weeks([
            (200, 0.50), (200, 0.52), (200, 0.51), (200, 0.49),
        ])
        result = run_temporal_stability(rows, min_weekly_n=100)
        assert result.status == "PASS"
        assert result.spread_pp == pytest.approx(3.0)
        assert result.max_abs_week_over_week_pp == pytest.approx(2.0)
        assert result.n_low_n_weeks == 0

    def test_spread_triggers_warn(self) -> None:
        rows = self._weeks([
            (200, 0.50), (200, 0.52), (200, 0.65), (200, 0.49),
        ])
        result = run_temporal_stability(rows, min_weekly_n=100)
        assert result.spread_pp == pytest.approx(16.0)
        assert result.status == "WARN"

    def test_jump_triggers_warn(self) -> None:
        # spread < 10pp but one consecutive jump >= 10pp
        rows = self._weeks([
            (200, 0.50), (200, 0.55), (200, 0.45), (200, 0.50),
        ])
        result = run_temporal_stability(rows, min_weekly_n=100)
        assert result.spread_pp == pytest.approx(10.0)
        assert result.max_abs_week_over_week_pp == pytest.approx(10.0)
        assert result.status == "WARN"

    def test_low_n_weeks_excluded_from_rule(self) -> None:
        # week 3 has 5 markets (low n) with a wild rate; rule must ignore it
        rows = self._weeks([
            (200, 0.50), (200, 0.52), (200, 0.51), (5, 0.95),
        ])
        result = run_temporal_stability(rows, min_weekly_n=100)
        assert result.n_low_n_weeks == 1
        assert result.spread_pp == pytest.approx(2.0)
        assert result.status == "PASS"

    def test_min_weekly_n_boundary_inclusive(self) -> None:
        # exactly min_weekly_n -> NOT low n
        rows = self._weeks([(100, 0.5), (100, 0.5)])
        result = run_temporal_stability(rows, min_weekly_n=100)
        assert result.n_low_n_weeks == 0
        assert DEFAULT_MIN_WEEKLY_N == 100

    def test_single_eligible_week_not_computable(self) -> None:
        rows = self._weeks([(200, 0.5)])
        result = run_temporal_stability(rows, min_weekly_n=100)
        assert result.status == "NOT_COMPUTABLE"

    def test_week_over_week_chain(self) -> None:
        rows = self._weeks([(200, 0.50), (200, 0.55), (200, 0.45)])
        result = run_temporal_stability(rows, min_weekly_n=100)
        assert [c.from_week for c in result.week_over_week] == [
            "2026-W02", "2026-W03"
        ]
        assert result.week_over_week[0].delta_pp == pytest.approx(5.0)
        assert result.week_over_week[1].delta_pp == pytest.approx(-10.0)

    def test_extreme_week_flagged(self) -> None:
        # 2-sigma outlier week
        rows = self._weeks([
            (200, 0.50), (200, 0.50), (200, 0.50), (200, 0.50),
            (200, 0.50), (200, 0.50), (200, 0.80),
        ])
        result = run_temporal_stability(rows, min_weekly_n=100)
        assert "2026-W08" in result.extreme_weeks
