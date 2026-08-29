"""Safety regressions for strict prospective-only std0 trade synchronization."""
from __future__ import annotations

from conftest import make_trade

from std0_quant.collectors.prospective_std0_trades import (
    ProspectiveTradesSyncer,
)


def prospective_syncer(settings, harness):
    return ProspectiveTradesSyncer(
        settings,
        harness.state,
        harness.writer,
        harness.page_store,
        client=harness.client,
    )


def test_later_page_outside_time_window_is_not_silently_appended(
    settings, make_harness
) -> None:
    settings.polymarket.sync.page_limit = 2
    settings.polymarket.sync.max_offset = 10000
    settings.polymarket.sync.sleep_between_pages_seconds = 0.0

    # Page 0 is inside the requested interval.
    # Page 1 violates the lower boundary.
    records = [
        make_trade("0x400", 1_700_000_400),
        make_trade("0x300", 1_700_000_300),
        make_trade("0x000", 1_700_000_000),
        make_trade("0xm100", 1_699_999_900),
    ]
    harness = make_harness(records, honor_time_params=False)
    syncer = prospective_syncer(settings, harness)

    result = syncer.sync_range(
        start_ms=1_700_000_200_000,
        end_ms=1_700_000_500_000,
    )

    assert result.status == "time_params_not_honored"

    # The incomplete/invalid window must publish nothing, including the
    # apparently-valid first page staged before the violation was discovered.
    assert harness.raw_lines() == []


def test_backfill_offset_cap_does_not_drop_same_second_boundary_fills(
    settings, make_harness
) -> None:
    settings.polymarket.sync.page_limit = 2
    settings.polymarket.sync.max_offset = 10000
    settings.polymarket.sync.sleep_between_pages_seconds = 0.0

    # Offset 0 and 2 succeed; offset 4 hits the fake API cap.
    # The cap lands inside three distinct fills sharing timestamp 1700000000.
    records = [
        make_trade("0xnew3", 1_700_000_003),
        make_trade("0xnew2", 1_700_000_002),
        make_trade("0xsame1", 1_700_000_000),
        make_trade("0xsame2", 1_700_000_000),
        make_trade("0xsame3", 1_700_000_000),
        make_trade("0xold", 1_699_999_999),
    ]
    harness = make_harness(
        records,
        enforce_offset_cap=2,
        honor_time_params=True,
    )
    syncer = prospective_syncer(settings, harness)

    result = syncer.sync_range(
        start_ms=1_699_999_000_000,
        end_ms=1_700_001_000_000,
    )

    assert result.status == "complete"

    written = harness.raw_lines()
    txs = {
        (row.get("record") or {}).get("transactionHash")
        for row in written
    }

    assert len(written) == 6
    assert txs == {
        "0xnew3",
        "0xnew2",
        "0xsame1",
        "0xsame2",
        "0xsame3",
        "0xold",
    }


def test_single_second_over_offset_cap_fails_without_partial_publication(
    settings, make_harness
) -> None:
    settings.polymarket.sync.page_limit = 2
    settings.polymarket.sync.max_offset = 10000
    settings.polymarket.sync.sleep_between_pages_seconds = 0.0

    # Five distinct fills share one public timestamp second.
    # With offsets 0 and 2 allowed but offset 4 rejected, completeness inside
    # this single second cannot be proven by further time bisection.
    records = [
        make_trade(f"0xsame{i}", 1_700_000_000)
        for i in range(5)
    ]
    harness = make_harness(
        records,
        enforce_offset_cap=2,
        honor_time_params=True,
    )
    syncer = prospective_syncer(settings, harness)

    result = syncer.sync_range(
        start_ms=1_700_000_000_000,
        end_ms=1_700_000_000_999,
    )

    assert result.status == "unsplittable_second"
    assert result.new_trades == 0

    # A capped, incomplete second must never publish a partial truth set.
    assert harness.raw_lines() == []


def test_prospective_sync_rerun_is_idempotent(
    settings, make_harness
) -> None:
    settings.polymarket.sync.page_limit = 2
    settings.polymarket.sync.max_offset = 10000
    settings.polymarket.sync.sleep_between_pages_seconds = 0.0

    records = [
        make_trade("0x3", 1_700_000_003),
        make_trade("0x2", 1_700_000_002),
        make_trade("0x1", 1_700_000_001),
    ]
    harness = make_harness(
        records,
        honor_time_params=True,
    )
    syncer = prospective_syncer(settings, harness)

    first = syncer.sync_range(
        start_ms=1_700_000_000_000,
        end_ms=1_700_000_010_000,
    )
    second = syncer.sync_range(
        start_ms=1_700_000_000_000,
        end_ms=1_700_000_010_000,
    )

    assert first.status == "complete"
    assert first.new_trades == 3

    assert second.status == "complete"
    assert second.new_trades == 0
    assert second.duplicates_skipped == 3

    assert len(harness.raw_lines()) == 3
