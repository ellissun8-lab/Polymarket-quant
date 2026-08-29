"""Select exactly one recorder session without cross-session stitching."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from std0_quant.audit.coverage import (
    SessionRecord,
    session_markets,
    session_time_ranges,
)


@dataclass(frozen=True)
class SessionSelection:
    status: str  # ELIGIBLE | MISSING | AMBIGUOUS
    kind: str
    session_id: str | None
    candidate_session_ids: tuple[str, ...]
    reason: str


def _session_overlaps_window(
    session: SessionRecord,
    start_ms: int,
    end_ms: int,
) -> bool:
    for lo, hi in session_time_ranges(session):
        if lo <= end_ms and (hi is None or hi >= start_ms):
            return True
    return False


def select_unique_session(
    sessions: Iterable[SessionRecord],
    *,
    kind: str,
    start_ms: int,
    end_ms: int,
    condition_id: str | None = None,
) -> SessionSelection:
    """Select one eligible source session for a market window.

    Book sessions must both overlap the window AND have subscribed to the
    requested condition_id. BTC sessions only need to overlap the window.

    Multiple distinct matching session_ids are rejected rather than stitched.
    """
    if end_ms < start_ms:
        raise ValueError("end_ms must be >= start_ms")

    if kind == "polymarket_book" and not condition_id:
        raise ValueError("condition_id is required for polymarket_book")

    candidates: dict[str, SessionRecord] = {}

    for session in sessions:
        if session.kind != kind:
            continue

        if not _session_overlaps_window(session, start_ms, end_ms):
            continue

        if (
            kind == "polymarket_book"
            and condition_id not in session_markets(session)
        ):
            continue

        candidates.setdefault(session.session_id, session)

    candidate_ids = tuple(sorted(candidates))

    if not candidate_ids:
        return SessionSelection(
            status="MISSING",
            kind=kind,
            session_id=None,
            candidate_session_ids=(),
            reason="no matching session overlaps the requested market window",
        )

    if len(candidate_ids) > 1:
        return SessionSelection(
            status="AMBIGUOUS",
            kind=kind,
            session_id=None,
            candidate_session_ids=candidate_ids,
            reason="multiple recorder sessions match; stitching is forbidden",
        )

    return SessionSelection(
        status="ELIGIBLE",
        kind=kind,
        session_id=candidate_ids[0],
        candidate_session_ids=candidate_ids,
        reason="exactly one matching recorder session",
    )
