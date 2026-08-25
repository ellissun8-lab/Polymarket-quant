"""Tests M - Audit 5: universe placebo summaries."""

from __future__ import annotations

import pytest

from std0_quant.audit.universe_placebo import (
    MIN_COMPARABLE_CLEAN_MARKETS,
    PLACEBO_UNIVERSES,
    UniverseSpec,
    run_universe_placebo,
    summarize_universe,
)
from std0_quant.events.event_ledger import (
    SlugWindowMetadataProvider,
    build_ledger_rows,
)
from test_event_ledger import fill  # noqa: F401  (reused helper)


def row(cid, clean=True, first_opp=True, y30=1, eligible=True,
        reason=None):
    return {
        "condition_id": cid,
        "clean_flag": clean,
        "exclude_reason": reason if not clean else None,
        "first_opp_end_ms": 1_787_500_000_000 if (clean and first_opp) else None,
        "y30": y30 if (clean and first_opp) else None,
        "y30_horizon_eligible": eligible if (clean and first_opp) else False,
    }


def summary(name="ETH-5m", n_clean=50, positives=25, first_opp=40,
            n_fills=1000, eligible=None):
    eligible = n_clean if eligible is None else eligible
    rows = []
    for i in range(n_clean):
        has_fo = i < first_opp
        rows.append(row(f"c{i}", clean=True, first_opp=has_fo,
                        y30=1 if has_fo and i < positives else 0,
                        eligible=has_fo and i < eligible))
    return summarize_universe(
        UniverseSpec(name, f"{name.lower().replace('-', '-')}-", 300),
        rows, n_fills=n_fills,
    )


class TestSpecs:
    def test_placebo_universe_specs(self) -> None:
        by_name = {u.name: u for u in PLACEBO_UNIVERSES}
        assert by_name["BTC-15m"].slug_prefix == "btc-updown-15m-"
        assert by_name["BTC-15m"].window_seconds == 900
        assert by_name["ETH-5m"].slug_prefix == "eth-updown-5m-"
        assert by_name["SOL-5m"].window_seconds == 300
        assert by_name["XRP-5m"].slug_prefix == "xrp-updown-5m-"


class TestSummarize:
    def test_counts_and_rates(self) -> None:
        rows = [
            row("a", first_opp=True, y30=1),
            row("b", first_opp=True, y30=0),
            row("c", first_opp=True, y30=1, eligible=False),
            row("d", first_opp=False),
            row("e", clean=False, reason="SAME_SECOND"),
            row("f", clean=False, reason="SAME_SECOND"),
        ]
        s = summarize_universe(
            UniverseSpec("ETH-5m", "eth-updown-5m-", 300), rows, n_fills=500,
            min_clean_markets=1,
        )
        assert s.n_markets == 6
        assert s.n_clean == 4
        assert s.n_excluded == 2
        assert s.exclusion_reasons == {"SAME_SECOND": 2}
        assert s.n_first_opp == 3
        assert s.first_opp_rate == pytest.approx(0.75)
        assert s.n_eligible == 2
        assert s.y30_positive == 1
        assert s.y30_censored == 1
        assert s.y30_positive_rate_observable == pytest.approx(0.5)
        assert s.comparable is True
        assert s.not_comparable_reason is None

    def test_no_fills_not_comparable(self) -> None:
        s = summarize_universe(
            UniverseSpec("XRP-5m", "xrp-updown-5m-", 300), [], n_fills=0
        )
        assert s.comparable is False
        assert s.not_comparable_reason == "no fills in universe"

    def test_too_few_clean_not_comparable(self) -> None:
        rows = [row(f"c{i}") for i in range(10)]
        s = summarize_universe(
            UniverseSpec("SOL-5m", "sol-updown-5m-", 300), rows, n_fills=99
        )
        assert s.comparable is False
        assert "only 10 clean markets" in s.not_comparable_reason

    def test_boundary_min_clean_comparable(self) -> None:
        rows = [row(f"c{i}", y30=1) for i in range(MIN_COMPARABLE_CLEAN_MARKETS)]
        s = summarize_universe(
            UniverseSpec("SOL-5m", "sol-updown-5m-", 300), rows, n_fills=99
        )
        assert s.comparable is True

    def test_no_eligible_not_comparable(self) -> None:
        rows = [row(f"c{i}", eligible=False) for i in range(50)]
        s = summarize_universe(
            UniverseSpec("SOL-5m", "sol-updown-5m-", 300), rows, n_fills=99
        )
        assert s.comparable is False
        assert s.not_comparable_reason == "no horizon-eligible markets"


class TestComparison:
    def test_deltas_vs_reference(self) -> None:
        ref = summary("BTC-5m", n_clean=100, positives=50, first_opp=80)
        p1 = summary("ETH-5m", n_clean=100, positives=60, first_opp=80)
        result = run_universe_placebo(ref, [p1])
        comp = result.comparison[0]
        assert comp.name == "ETH-5m"
        # denominators are horizon-eligible (= first-opp) markets: 80
        assert comp.delta_y30_pp == pytest.approx(12.5)
        assert comp.delta_first_opp_pp == pytest.approx(0.0)
        assert comp.comparable is True

    def test_not_comparable_rows_kept(self) -> None:
        ref = summary("BTC-5m", n_clean=100, positives=50)
        p = summarize_universe(
            UniverseSpec("XRP-5m", "xrp-updown-5m-", 300), [], n_fills=0
        )
        result = run_universe_placebo(ref, [p])
        assert result.comparison[0].comparable is False
        assert result.comparison[0].note == "no fills in universe"
        assert result.comparison[0].delta_y30_pp is None
        assert result.status == "NOT_COMPUTABLE"

    def test_status_reported_when_any_comparable(self) -> None:
        ref = summary("BTC-5m", n_clean=100, positives=50)
        ok = summary("ETH-5m", n_clean=100, positives=50)
        empty = summarize_universe(
            UniverseSpec("XRP-5m", "xrp-updown-5m-", 300), [], n_fills=0
        )
        result = run_universe_placebo(ref, [ok, empty])
        assert result.status == "REPORTED"
        assert [c.comparable for c in result.comparison] == [True, False]


class TestSameConstruction:
    """The placebo ledger must come from the SAME frozen pipeline."""

    def test_eth_placebo_ledger_via_frozen_pipeline(self) -> None:
        # One ETH-5m market, 5-minute aligned window from the slug
        base = 1_787_400_000  # divisible by 300
        cid = "0xeth1"
        slug = f"eth-updown-5m-{base}"
        fills = [
            fill(f"0x{cid}a", (base + 10) * 1000, outcome="Up",
                 condition_id=cid, slug=slug),
            fill(f"0x{cid}b", (base + 100) * 1000, outcome="Down",
                 condition_id=cid, slug=slug),
            fill(f"0x{cid}c", (base + 110) * 1000, outcome="Down",
                 condition_id=cid, slug=slug),
            # BUY Down inside (t0, t0+30s] -> y30=1
            fill(f"0x{cid}d", (base + 115) * 1000, outcome="Down",
                 condition_id=cid, slug=slug),
        ]
        provider = SlugWindowMetadataProvider.from_fills(
            fills, slug_prefix="eth-updown-5m-", window_seconds=300
        )
        rows = build_ledger_rows(
            fills, provider, scope_slug_prefix="eth-updown-5m-"
        )
        s = summarize_universe(
            UniverseSpec("ETH-5m", "eth-updown-5m-", 300), rows, n_fills=4
        )
        assert s.n_markets == 1
        assert s.n_clean == 1
        assert s.n_first_opp == 1
        assert s.y30_positive == 1
        assert s.y30_positive_rate_observable == 1.0

    def test_wrong_window_alignment_excluded_not_fixed(self) -> None:
        # A slug whose start is not 300-aligned -> MARKET_METADATA_MISSING,
        # never a guessed window
        base = 1_787_400_100  # NOT divisible by 300
        cid = "0xodd"
        slug = f"eth-updown-5m-{base}"
        fills = [
            fill(f"0x{cid}a", (base + 10) * 1000, outcome="Up",
                 condition_id=cid, slug=slug),
            fill(f"0x{cid}b", (base + 100) * 1000, outcome="Down",
                 condition_id=cid, slug=slug),
        ]
        provider = SlugWindowMetadataProvider.from_fills(
            fills, slug_prefix="eth-updown-5m-", window_seconds=300
        )
        rows = build_ledger_rows(
            fills, provider, scope_slug_prefix="eth-updown-5m-"
        )
        s = summarize_universe(
            UniverseSpec("ETH-5m", "eth-updown-5m-", 300), rows, n_fills=2
        )
        assert s.n_clean == 0
        assert s.exclusion_reasons.get("MARKET_METADATA_MISSING") == 1
        assert s.comparable is False
