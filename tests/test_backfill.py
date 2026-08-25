"""Tests for the deep-history backfill: the live API's hard offset cap
(``HTTP 400 "max historical trades offset of 10000 exceeded"``) must be
handled as a paging boundary, and the window-slicing backfill must retrieve
every record past that cap."""

from __future__ import annotations

import pytest
from conftest import make_trade

from std0_quant.collectors.std0_trades import (
    APIBadRequestError,
    Std0TradesSyncer,
)


def _records(n: int, first_ts_s: int = 1_787_400_000) -> list[dict]:
    """n trades at one-second spacing, newest first (API ordering)."""
    return [
        make_trade(f"0xtx{i}", first_ts_s + i, condition_id="0xcA",
                   slug="btc-updown-5m-1787400000")
        for i in range(n - 1, -1, -1)
    ]


END_MS = 2_000_000_000_000  # comfortably after all fixture trades
FIRST_TS_S = 1_787_400_000


@pytest.fixture()
def tuned_settings(settings):
    settings.polymarket.sync.page_limit = 10
    settings.polymarket.sync.max_offset = 10000  # local guard never trips
    settings.polymarket.sync.sleep_between_pages_seconds = 0.0
    return settings


class TestOffsetCapHandling:
    def test_offset_cap_400_is_a_paging_boundary_not_a_failure(
        self, tuned_settings, make_harness
    ) -> None:
        harness = make_harness(
            _records(35), enforce_offset_cap=20, honor_time_params=True
        )
        result = harness.syncer.sync(
            start_ms=0, end_ms=END_MS, use_time_params=True, full=True
        )
        # pages at offsets 0, 10, 20 succeed; offset 30 hits the API cap.
        assert result.status == "offset_limit_reached"
        assert result.pages_fetched == 3
        assert result.records_fetched == 30
        assert result.new_trades == 30
        assert any("max historical trades offset" in m for m in result.messages)

    def test_other_400_errors_still_raise(
        self, tuned_settings, make_harness
    ) -> None:
        harness = make_harness(_records(5))
        harness.api.scripted_failures.append((400, '{"error":"bad request"}'))
        with pytest.raises(APIBadRequestError):
            harness.syncer.sync()


class TestSyncBackfill:
    def test_backfill_slices_windows_past_the_offset_cap(
        self, tuned_settings, make_harness
    ) -> None:
        # 35 trades at ts 1000..1034s; cap lets only 3 pages of 10 through.
        harness = make_harness(
            _records(35), enforce_offset_cap=20, honor_time_params=True
        )
        result = harness.syncer.sync_backfill(
            start_ms=0, end_ms=END_MS, sleep_between_windows_seconds=0.0
        )
        assert result.status == "complete"
        assert result.windows == 2
        # window 1: 30 records (ts 1005..1034); window 2: the 5 older ones.
        assert result.records_fetched == 35
        assert result.new_trades == 35
        assert result.duplicates_skipped == 0
        assert result.earliest_ts_ms == FIRST_TS_S * 1000
        assert result.latest_ts_ms == (FIRST_TS_S + 34) * 1000
        assert len(harness.raw_lines()) == 35
        # the second window's end was shrunk to earliest-seen minus 1 second
        end_params = [
            p["end"] for _, p in harness.api.calls if "end" in p
        ]
        assert end_params[0] == str(END_MS // 1000)
        assert end_params[-1] == str(FIRST_TS_S + 4)  # earliest seen 1787400005 -> end 1787400004

    def test_backfill_is_idempotent(self, tuned_settings, make_harness) -> None:
        harness = make_harness(
            _records(35), enforce_offset_cap=20, honor_time_params=True
        )
        first = harness.syncer.sync_backfill(
            start_ms=0, end_ms=END_MS, sleep_between_windows_seconds=0.0
        )
        second = harness.syncer.sync_backfill(
            start_ms=0, end_ms=END_MS, sleep_between_windows_seconds=0.0
        )
        assert first.new_trades == 35
        assert second.new_trades == 0
        assert second.duplicates_skipped == 35
        assert len(harness.raw_lines()) == 35  # no duplicates appended

    def test_backfill_stops_on_empty_window(
        self, tuned_settings, make_harness
    ) -> None:
        harness = make_harness([], honor_time_params=True)
        result = harness.syncer.sync_backfill(
            start_ms=0, end_ms=END_MS, sleep_between_windows_seconds=0.0
        )
        assert result.status == "complete"
        assert result.windows == 1
        assert result.new_trades == 0
        assert any("empty" in m for m in result.messages)

    def test_backfill_completes_when_window_exhausts_before_cap(
        self, tuned_settings, make_harness
    ) -> None:
        # Fewer records than one page: single window, no offset cap hit.
        harness = make_harness(
            _records(7), enforce_offset_cap=20, honor_time_params=True
        )
        result = harness.syncer.sync_backfill(
            start_ms=0, end_ms=END_MS, sleep_between_windows_seconds=0.0
        )
        assert result.status == "complete"
        assert result.windows == 1
        assert result.new_trades == 7

    def test_backfill_rejects_bad_range(self, tuned_settings, make_harness) -> None:
        harness = make_harness(_records(3), honor_time_params=True)
        with pytest.raises(ValueError):
            harness.syncer.sync_backfill(start_ms=2_000_000, end_ms=0)

    def test_each_window_gets_its_own_run_id(
        self, tuned_settings, make_harness
    ) -> None:
        # page files are keyed (run_id, page_index): sharing a run_id across
        # windows would collide; each window must be a separate audited run.
        harness = make_harness(
            _records(35), enforce_offset_cap=20, honor_time_params=True
        )
        harness.syncer._explicit_run_id = "injected-run"
        result = harness.syncer.sync_backfill(
            start_ms=0, end_ms=END_MS, sleep_between_windows_seconds=0.0
        )
        run_ids = [r.run_id for r in result.runs]
        assert len(run_ids) >= 2
        assert len(set(run_ids)) == len(run_ids)  # all distinct
        assert "injected-run" not in run_ids


class TestIdentityCollisionAudit:
    def test_within_page_identical_records_are_counted(
        self, tuned_settings, make_harness
    ) -> None:
        # two byte-identical records on the same page: public data cannot
        # tell a replay from two same-second fills -> counted, not guessed
        records = _records(3)
        records.append(dict(records[0]))
        harness = make_harness(records)
        result = harness.syncer.sync()
        assert result.within_page_identity_collisions == 1
        assert result.new_trades == 3
        assert len(harness.raw_lines()) == 3
