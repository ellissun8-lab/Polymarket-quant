"""Bounded source selection for prospective market coverage.

This layer enforces temporal-integrity eligibility before FileCoverageProvider:
- exactly one Book recorder session for the market;
- exactly one BTC recorder session for the market;
- raw files selected only from those sessions;
- no interrupted-session stitching.

It selects candidate files only. Formal sidecar SHA re-verification remains a
separate audit step.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from std0_quant.audit.coverage import SessionRecord, load_sessions
from std0_quant.features.raw_selection import (
    ClosedRawFile,
    closed_raw_index,
    files_for_time_window,
)
from std0_quant.features.session_selection import select_unique_session


@dataclass(frozen=True)
class MarketCoverageSources:
    status: str  # ELIGIBLE | INELIGIBLE
    condition_id: str
    book_session_id: str | None
    btc_session_id: str | None
    book_files: tuple[Path, ...]
    btc_files: tuple[Path, ...]
    reasons: tuple[str, ...]


class ProspectiveCoverageSelector:
    """Reusable bounded selector with indexes loaded once per refresh."""

    def __init__(
        self,
        sessions: list[SessionRecord],
        book_index: list[ClosedRawFile],
        btc_index: list[ClosedRawFile],
    ) -> None:
        self.sessions = sessions
        self.book_index = book_index
        self.btc_index = btc_index

    @classmethod
    def from_paths(
        cls,
        *,
        sessions_dir: Path | str,
        book_dir: Path | str,
        btc_dir: Path | str,
    ) -> "ProspectiveCoverageSelector":
        return cls(
            sessions=load_sessions(sessions_dir),
            book_index=closed_raw_index(book_dir),
            btc_index=closed_raw_index(btc_dir),
        )

    def select(
        self,
        *,
        condition_id: str,
        market_start_ms: int,
        market_end_ms: int,
    ) -> MarketCoverageSources:
        book = select_unique_session(
            self.sessions,
            kind="polymarket_book",
            start_ms=market_start_ms,
            end_ms=market_end_ms,
            condition_id=condition_id,
        )
        btc = select_unique_session(
            self.sessions,
            kind="btc_ticks",
            start_ms=market_start_ms,
            end_ms=market_end_ms,
        )

        reasons: list[str] = []

        if book.status != "ELIGIBLE":
            reasons.append(
                f"book_session_{book.status.lower()}:"
                + ",".join(book.candidate_session_ids)
            )

        if btc.status != "ELIGIBLE":
            reasons.append(
                f"btc_session_{btc.status.lower()}:"
                + ",".join(btc.candidate_session_ids)
            )

        if reasons:
            return MarketCoverageSources(
                status="INELIGIBLE",
                condition_id=condition_id,
                book_session_id=book.session_id,
                btc_session_id=btc.session_id,
                book_files=(),
                btc_files=(),
                reasons=tuple(reasons),
            )

        assert book.session_id is not None
        assert btc.session_id is not None

        # Book state immediately before market_start may remain valid for the
        # bounded stale interval, so one same-session predecessor file is needed.
        book_files = files_for_time_window(
            self.book_index,
            market_start_ms,
            market_end_ms,
            session_id=book.session_id,
            include_predecessor=True,
        )

        # BTC ledger coverage counts observations inside the market window only.
        btc_files = files_for_time_window(
            self.btc_index,
            market_start_ms,
            market_end_ms,
            session_id=btc.session_id,
            include_predecessor=False,
        )

        if not book_files:
            reasons.append("book_files_missing")
        if not btc_files:
            reasons.append("btc_files_missing")

        if reasons:
            return MarketCoverageSources(
                status="INELIGIBLE",
                condition_id=condition_id,
                book_session_id=book.session_id,
                btc_session_id=btc.session_id,
                book_files=tuple(book_files),
                btc_files=tuple(btc_files),
                reasons=tuple(reasons),
            )

        return MarketCoverageSources(
            status="ELIGIBLE",
            condition_id=condition_id,
            book_session_id=book.session_id,
            btc_session_id=btc.session_id,
            book_files=tuple(book_files),
            btc_files=tuple(btc_files),
            reasons=(),
        )
