from __future__ import annotations

from std0_quant.audit.coverage import SessionRecord
from std0_quant.features.session_selection import select_unique_session


def book_session(session_id: str, market: str, start: int, end: int) -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        kind="polymarket_book",
        events=[
            {"event": "connected", "timestamp_ms": start},
            {"event": "subscribed", "timestamp_ms": start + 1, "market": market},
            {"event": "session_end", "timestamp_ms": end},
        ],
    )


def btc_session(session_id: str, start: int, end: int) -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        kind="btc_ticks",
        events=[
            {"event": "connected", "timestamp_ms": start},
            {"event": "session_end", "timestamp_ms": end},
        ],
    )


def test_unique_book_session_is_eligible():
    sessions = [
        book_session("book-1", "cid-1", 1000, 9000),
    ]

    result = select_unique_session(
        sessions,
        kind="polymarket_book",
        start_ms=2000,
        end_ms=8000,
        condition_id="cid-1",
    )

    assert result.status == "ELIGIBLE"
    assert result.session_id == "book-1"
    assert result.candidate_session_ids == ("book-1",)


def test_book_subscription_and_overlap_must_belong_to_same_session():
    sessions = [
        # Subscribed to cid-1, but does not overlap requested window.
        book_session("book-a", "cid-1", 1000, 1500),

        # Overlaps requested window, but subscribed to another market.
        book_session("book-b", "cid-2", 2000, 8000),
    ]

    result = select_unique_session(
        sessions,
        kind="polymarket_book",
        start_ms=3000,
        end_ms=7000,
        condition_id="cid-1",
    )

    assert result.status == "MISSING"
    assert result.session_id is None
    assert result.candidate_session_ids == ()


def test_multiple_matching_book_sessions_are_ambiguous_not_stitched():
    sessions = [
        book_session("book-a", "cid-1", 1000, 9000),
        book_session("book-b", "cid-1", 2000, 8000),
    ]

    result = select_unique_session(
        sessions,
        kind="polymarket_book",
        start_ms=3000,
        end_ms=7000,
        condition_id="cid-1",
    )

    assert result.status == "AMBIGUOUS"
    assert result.session_id is None
    assert result.candidate_session_ids == ("book-a", "book-b")


def test_multiple_matching_btc_sessions_are_ambiguous():
    sessions = [
        btc_session("btc-a", 1000, 9000),
        btc_session("btc-b", 2000, 8000),
    ]

    result = select_unique_session(
        sessions,
        kind="btc_ticks",
        start_ms=3000,
        end_ms=7000,
    )

    assert result.status == "AMBIGUOUS"
    assert result.session_id is None
    assert result.candidate_session_ids == ("btc-a", "btc-b")
