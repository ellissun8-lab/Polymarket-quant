"""Test A: trade deduplication, plus collector behavior tests.

Required by the spec: re-syncing the same transactionHash / unique trade id
must never produce a second copy in the database.
"""

from __future__ import annotations

from conftest import SyncHarness, make_trade


def _sample_records() -> list[dict]:
    # newest-first, as the data API returns
    return [
        make_trade("0xtx5", 1700000500, size=120, price=0.55),
        make_trade("0xtx4", 1700000400, size=80, price=0.52),
        make_trade("0xtx3", 1700000300, size=60, price=0.51),
        make_trade("0xtx2", 1700000200, size=40, price=0.50),
        make_trade("0xtx1", 1700000100, size=20, price=0.49),
    ]


def test_repeated_sync_writes_no_duplicates(make_harness) -> None:
    harness: SyncHarness = make_harness(_sample_records())

    first = harness.syncer.sync(full=True)
    assert first.status == "complete"
    assert first.new_trades == 5
    assert len(harness.raw_lines()) == 5

    # A brand-new syncer (same state + same raw file) re-fetching the same API
    # payload must append nothing.
    second = SyncHarness(harness.settings, _sample_records())
    try:
        result = second.syncer.sync(full=True)
        assert result.new_trades == 0
        assert result.duplicates_skipped == 5
        assert len(second.raw_lines()) == 5  # raw file unchanged
        assert len(second.state.known_keys("std0_trades")) == 5
    finally:
        second.close()


def test_duplicate_within_single_api_response_is_collapsed(make_harness) -> None:
    # The same record appearing twice inside one response is an API replay.
    trade = make_trade("0xtx1", 1700000100)
    harness: SyncHarness = make_harness([trade, dict(trade)])
    result = harness.syncer.sync(full=True)
    assert result.new_trades == 1
    assert len(harness.raw_lines()) == 1


def test_same_timestamp_same_outcome_multiple_fills_coexist(make_harness) -> None:
    """Spec Test B (storage level): identical timestamps must not overwrite."""
    records = [
        make_trade("0xtx1", 1700000100, size=10, price=0.40),
        make_trade("0xtx2", 1700000100, size=20, price=0.50),
        make_trade("0xtx3", 1700000100, size=30, price=0.60),
    ]
    harness: SyncHarness = make_harness(records)
    result = harness.syncer.sync(full=True)
    assert result.new_trades == 3
    lines = harness.raw_lines()
    assert len(lines) == 3
    fill_ids = [line["record"] for line in lines]
    ids = {f"{r['transactionHash']}|{r['size']}" for r in fill_ids}
    assert len(ids) == 3  # all three fills coexist


def test_same_tx_hash_different_sizes_are_distinct(make_harness) -> None:
    records = [
        make_trade("0xsame", 1700000100, size=10),
        make_trade("0xsame", 1700000100, size=25),
    ]
    harness: SyncHarness = make_harness(records)
    result = harness.syncer.sync(full=True)
    assert result.new_trades == 2


def test_explicit_id_field_preferred_for_identity(make_harness) -> None:
    records = [
        make_trade("0xtx1", 1700000100, extra={"id": "trade-abc"}),
        make_trade("0xtx1", 1700000100, extra={"id": "trade-def"}),
    ]
    harness: SyncHarness = make_harness(records)
    result = harness.syncer.sync(full=True)
    assert result.new_trades == 2


def test_taker_only_is_sent_explicitly(make_harness) -> None:
    harness: SyncHarness = make_harness(_sample_records())
    harness.syncer.sync(full=True)
    params = harness.api.last_params()
    assert "takerOnly" in params
    assert params["takerOnly"] == "false"  # matches configured settings.yaml
    assert params["user"] == harness.settings.trader.wallet


def test_incremental_sync_fetches_only_new_records(make_harness) -> None:
    initial = _sample_records()
    harness: SyncHarness = make_harness(initial)
    harness.syncer.sync(full=True)
    first_pages = harness.api.calls

    # One new trade arrives on top of the same history.
    harness.api.records = [make_trade("0xtx6", 1700000600, size=90), *initial]
    result = harness.syncer.sync()  # incremental (not full)
    assert result.new_trades == 1
    assert result.pages_fetched < 10
    assert len(harness.raw_lines()) == 6
    assert harness.api.calls is first_pages  # same transport, more calls


def test_offset_limit_guard(make_harness, settings) -> None:
    settings.polymarket.sync.page_limit = 2
    settings.polymarket.sync.max_offset = 4  # stops after offset 4 (> 4)
    records = [make_trade(f"0xtx{i}", 1700000000 + i) for i in range(20)]
    harness: SyncHarness = make_harness(records)
    result = harness.syncer.sync(full=True)
    assert result.status == "offset_limit_reached"
    assert any("max_offset" in m for m in result.messages)


def test_time_params_not_honored_aborts_instead_of_silent_full_fetch(make_harness) -> None:
    # API returns records outside the requested window -> must abort loudly.
    records = [
        make_trade("0xtx1", 1700000100),
        make_trade("0xtx2", 1700000200),
    ]
    harness: SyncHarness = make_harness(records)
    result = harness.syncer.sync(
        start_ms=1_800_000_000_000, end_ms=1_800_000_300_000,
        use_time_params=True, full=True,
    )
    assert result.status == "time_params_not_honored"
    assert result.messages


def test_http_retry_then_success(make_harness) -> None:
    records = _sample_records()
    harness: SyncHarness = make_harness(records)
    from std0_quant.collectors.std0_trades import RetryingClient

    client = RetryingClient(
        base_url="http://fake", fetch_fn=harness.api,
        max_retries=3, backoff_base_seconds=0.0, sleeper=lambda _s: None,
    )
    harness.api.scripted_failures = [(503, "boom"), (429, "slow down")]
    harness.client = client
    harness.syncer.client = client
    harness.api.records = records
    result = harness.syncer.sync(full=True)
    assert result.status == "complete"
    assert result.new_trades == 5
