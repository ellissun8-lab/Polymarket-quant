"""Coverage / gap computation tests."""

from __future__ import annotations

import json

from std0_quant.audit.coverage import (
    FileCoverageProvider,
    bounded_state_coverage_pct,
    coverage_pct,
    find_gaps,
    load_sessions,
    session_overlaps,
    session_time_ranges,
    stream_coverage,
)
from std0_quant.storage import AppendOnlyNDJSON

START = 1_000_000
END = 1_300_000  # 300 second window = 300 buckets


class TestCoveragePct:
    def test_full_coverage(self) -> None:
        obs = list(range(START, END, 500))  # 2 obs per bucket
        assert coverage_pct(obs, START, END) == 1.0

    def test_half_coverage(self) -> None:
        obs = list(range(START, START + 150_000, 500))  # first half only
        assert coverage_pct(obs, START, END) == 0.5

    def test_no_observations_inside_window(self) -> None:
        assert coverage_pct([START - 5_000], START, END) == 0.0

    def test_invalid_window_returns_none(self) -> None:
        assert coverage_pct([START], END, START) is None

    def test_partial_last_bucket_counts(self) -> None:
        # 1 observation in the middle of a 300s window -> 1/300
        assert coverage_pct([START + 150_000], START, END) == 1 / 300

    def test_book_state_is_only_covered_until_stale_deadline(self) -> None:
        assert bounded_state_coverage_pct(
            [START], START, START + 10_000, stale_after_seconds=5
        ) == 0.5

    def test_repeated_valid_updates_keep_state_covered(self) -> None:
        assert bounded_state_coverage_pct(
            [START, START + 4_000, START + 8_000],
            START, START + 10_000, stale_after_seconds=5,
        ) == 1.0


class TestGaps:
    def test_leading_and_trailing_and_interior_gaps(self) -> None:
        obs = [
            START + 30_000,   # leading gap of 30s (start -> first obs)
            START + 31_000,
            START + 60_000,   # interior gap of 29s
            END - 40_000,     # interior gap of 40s + trailing 40s
        ]
        gaps = find_gaps(obs, START, END, threshold_seconds=5)
        durations = [g.duration_ms for g in gaps]
        assert 30_000 in durations   # leading
        assert 40_000 in durations   # interior (60_000 -> END-40_000)
        assert 40_000 in durations   # trailing (END-40_000 -> END)
        assert 29_000 in durations   # interior 31s -> 60s

    def test_no_gaps_when_dense(self) -> None:
        obs = list(range(START, END, 1000))
        assert find_gaps(obs, START, END, threshold_seconds=5) == []

    def test_empty_stream_is_one_big_gap(self) -> None:
        gaps = find_gaps([], START, END, threshold_seconds=5)
        assert len(gaps) == 1
        assert gaps[0].duration_ms == END - START

    def test_stream_coverage_fields(self) -> None:
        sc = stream_coverage([START, START + 10_000, END - 1], START, END,
                             gap_threshold_seconds=5)
        assert sc.n_observations == 3
        assert sc.first_observation_ms == START
        assert sc.last_observation_ms == END - 1
        # largest gap: between obs 2 (START+10s) and obs 3 (END-1)
        assert sc.max_gap_ms == (END - 1) - (START + 10_000)


class TestSessions:
    def _write_session(self, tmp_path, session_id, kind, connected, disconnected):
        path = tmp_path / "sessions" / f"{session_id}.ndjson"
        entries = [
            {"session_id": session_id, "kind": kind, "event": "session_start",
             "timestamp_ms": connected - 1},
            {"session_id": session_id, "kind": kind, "event": "connected",
             "timestamp_ms": connected},
            {"session_id": session_id, "kind": kind, "event": "subscribed",
             "market": "0xcA", "timestamp_ms": connected + 1},
            {"session_id": session_id, "kind": kind, "event": "disconnected",
             "timestamp_ms": disconnected},
            {"session_id": session_id, "kind": kind, "event": "session_end",
             "timestamp_ms": disconnected + 1},
        ]
        with AppendOnlyNDJSON(path) as writer:
            writer.append_many(entries)
        return path

    def test_load_sessions_and_ranges(self, tmp_path) -> None:
        self._write_session(tmp_path, "s1", "polymarket_book",
                            START, START + 100_000)
        sessions = load_sessions(tmp_path / "sessions")
        assert len(sessions) == 1
        assert sessions[0].kind == "polymarket_book"
        ranges = session_time_ranges(sessions[0])
        assert ranges[0] == (START, START + 100_000)
        assert session_overlaps(sessions, START + 50_000, START + 60_000,
                                "polymarket_book") is True
        assert session_overlaps(sessions, START + 200_000, START + 300_000,
                                "polymarket_book") is False
        assert session_overlaps(sessions, START, END, "btc_ticks") is False

    def test_file_provider_book_and_btc_coverage(self, tmp_path) -> None:
        self._write_session(tmp_path, "s1", "polymarket_book",
                            START, START + 100_000)
        self._write_session(tmp_path, "b1", "btc_ticks", START, END)

        # book rows: Up dense, Down sparse -> weakest side reported
        with AppendOnlyNDJSON(tmp_path / "raw" / "book.ndjson") as w:
            w.append_many([
                {"condition_id": "0xcA", "token_id": "up", "outcome": "Up",
                 "receive_timestamp_ms": ts}
                for ts in range(START, START + 100_000, 500)
            ] + [
                {"condition_id": "0xcA", "token_id": "dn", "outcome": "Down",
                 "receive_timestamp_ms": ts}
                for ts in range(START, START + 100_000, 50_000)
            ] + [
                {"condition_id": "0xOTHER", "token_id": "up", "outcome": "Up",
                 "receive_timestamp_ms": START},
            ])
        with AppendOnlyNDJSON(tmp_path / "btc" / "ticks.ndjson") as w:
            w.append_many([
                {"exchange_timestamp_ms": ts} for ts in range(START, END, 1000)
            ])

        provider = FileCoverageProvider(
            book_dir=tmp_path / "raw", btc_dir=tmp_path / "btc",
            sessions_dir=tmp_path / "sessions",
        )
        coverage = provider.market_coverage("0xcA", START, START + 100_000)
        # Down side: 3 observations in 100 buckets (2 of them at bucket edges)
        assert coverage.poly_book_coverage_pct is not None
        assert coverage.poly_book_coverage_pct <= 0.03
        assert coverage.btc_coverage_pct == 1.0
        assert coverage.book_expected is True
        assert coverage.btc_expected is True

        # A market with no session subscription is not "expected"
        other = provider.market_coverage("0xOTHER", START, START + 100_000)
        assert other.book_expected is False

        # Detailed report with gaps
        report = provider.market_report("0xcA", START, START + 100_000)
        assert set(report["book_tokens"]) == {"up", "dn"}
        assert report["book_tokens"]["dn"]["n_gaps"] >= 1
        assert report["btc_coverage_pct"] == 1.0
