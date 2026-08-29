from __future__ import annotations

from pathlib import Path

from std0_quant.audit.coverage import SessionRecord
from std0_quant.features.prospective_coverage import (
    ProspectiveCoverageSelector,
)
from std0_quant.features.raw_selection import ClosedRawFile


def book_session(
    session_id: str,
    market: str,
    start: int,
    end: int,
) -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        kind="polymarket_book",
        events=[
            {"event": "connected", "timestamp_ms": start},
            {
                "event": "subscribed",
                "timestamp_ms": start + 1,
                "market": market,
            },
            {"event": "session_end", "timestamp_ms": end},
        ],
    )


def btc_session(
    session_id: str,
    start: int,
    end: int,
) -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        kind="btc_ticks",
        events=[
            {"event": "connected", "timestamp_ms": start},
            {"event": "session_end", "timestamp_ms": end},
        ],
    )


def raw(
    path: Path,
    session_id: str,
    first: int,
    last: int,
) -> ClosedRawFile:
    return ClosedRawFile(
        path=path,
        source=None,
        session_id=session_id,
        first_timestamp_ms=first,
        last_timestamp_ms=last,
        record_count=1,
        sha256="abc",
    )


def test_selects_only_unique_session_files_and_book_predecessor(tmp_path):
    sessions = [
        book_session("book-1", "cid-1", 1000, 9000),
        btc_session("btc-1", 1000, 9000),
    ]

    book_prev = tmp_path / "book_prev.ndjson"
    book_live = tmp_path / "book_live.ndjson"
    book_other = tmp_path / "book_other.ndjson"

    btc_prev = tmp_path / "btc_prev.ndjson"
    btc_live = tmp_path / "btc_live.ndjson"
    btc_other = tmp_path / "btc_other.ndjson"

    selector = ProspectiveCoverageSelector(
        sessions=sessions,
        book_index=[
            raw(book_prev, "book-1", 1000, 2500),
            raw(book_live, "book-1", 3000, 6000),
            raw(book_other, "book-other", 3000, 6000),
        ],
        btc_index=[
            raw(btc_prev, "btc-1", 1000, 2500),
            raw(btc_live, "btc-1", 3500, 6500),
            raw(btc_other, "btc-other", 3500, 6500),
        ],
    )

    result = selector.select(
        condition_id="cid-1",
        market_start_ms=3000,
        market_end_ms=7000,
    )

    assert result.status == "ELIGIBLE"
    assert result.book_session_id == "book-1"
    assert result.btc_session_id == "btc-1"

    # Book includes one same-session predecessor.
    assert result.book_files == (book_prev, book_live)

    # BTC coverage counts only observations inside the window.
    assert result.btc_files == (btc_live,)

    assert book_other not in result.book_files
    assert btc_other not in result.btc_files


def test_ambiguous_book_sessions_fail_closed(tmp_path):
    sessions = [
        book_session("book-a", "cid-1", 1000, 9000),
        book_session("book-b", "cid-1", 1000, 9000),
        btc_session("btc-1", 1000, 9000),
    ]

    selector = ProspectiveCoverageSelector(
        sessions=sessions,
        book_index=[
            raw(tmp_path / "a.ndjson", "book-a", 3000, 6000),
            raw(tmp_path / "b.ndjson", "book-b", 3000, 6000),
        ],
        btc_index=[
            raw(tmp_path / "btc.ndjson", "btc-1", 3000, 6000),
        ],
    )

    result = selector.select(
        condition_id="cid-1",
        market_start_ms=3000,
        market_end_ms=7000,
    )

    assert result.status == "INELIGIBLE"
    assert result.book_files == ()
    assert result.btc_files == ()
    assert any(
        reason.startswith("book_session_ambiguous")
        for reason in result.reasons
    )


def test_ambiguous_btc_sessions_fail_closed(tmp_path):
    sessions = [
        book_session("book-1", "cid-1", 1000, 9000),
        btc_session("btc-a", 1000, 9000),
        btc_session("btc-b", 1000, 9000),
    ]

    selector = ProspectiveCoverageSelector(
        sessions=sessions,
        book_index=[
            raw(tmp_path / "book.ndjson", "book-1", 3000, 6000),
        ],
        btc_index=[
            raw(tmp_path / "btc-a.ndjson", "btc-a", 3000, 6000),
            raw(tmp_path / "btc-b.ndjson", "btc-b", 3000, 6000),
        ],
    )

    result = selector.select(
        condition_id="cid-1",
        market_start_ms=3000,
        market_end_ms=7000,
    )

    assert result.status == "INELIGIBLE"
    assert result.book_files == ()
    assert result.btc_files == ()
    assert any(
        reason.startswith("btc_session_ambiguous")
        for reason in result.reasons
    )


def test_missing_selected_files_is_ineligible(tmp_path):
    sessions = [
        book_session("book-1", "cid-1", 1000, 9000),
        btc_session("btc-1", 1000, 9000),
    ]

    selector = ProspectiveCoverageSelector(
        sessions=sessions,
        book_index=[],
        btc_index=[
            raw(tmp_path / "btc.ndjson", "btc-1", 3000, 6000),
        ],
    )

    result = selector.select(
        condition_id="cid-1",
        market_start_ms=3000,
        market_end_ms=7000,
    )

    assert result.status == "INELIGIBLE"
    assert "book_files_missing" in result.reasons
