"""Coverage and gap reporting for book / BTC data (spec section 15 Audit).

Coverage definition: within a market's ``[start, end]`` window, the fraction
of 1-second buckets containing at least one recorded observation. For the
order book, coverage is computed per outcome token and the weaker (minimum)
of the two sides is reported -- a market where only "Up" was recorded is not
"covered".

Nothing here fills or extrapolates: gaps remain gaps and are reported.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from std0_quant.events.event_ledger import MarketCoverage
from std0_quant.storage import read_ndjson

logger = logging.getLogger(__name__)


@dataclass
class Gap:
    start_ms: int
    end_ms: int

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


@dataclass
class StreamCoverage:
    """Coverage of one observation stream over one window."""

    coverage_pct: float | None
    n_observations: int
    first_observation_ms: int | None
    last_observation_ms: int | None
    gaps: list[Gap] = field(default_factory=list)
    max_gap_ms: int | None = None


def coverage_pct(
    observations_ms: list[int],
    start_ms: int,
    end_ms: int,
    bucket_seconds: float = 1.0,
) -> float | None:
    """Fraction of time buckets with at least one observation.

    Returns ``None`` for a non-positive window, and ``0.0`` when there are
    observations in general but none inside the window (a real, reportable
    gap -- not an unknown).
    """
    if end_ms <= start_ms:
        return None
    bucket_ms = int(bucket_seconds * 1000)
    n_buckets = max(1, (end_ms - start_ms + bucket_ms - 1) // bucket_ms)
    filled = set()
    for ts in observations_ms:
        if start_ms <= ts <= end_ms:
            # an observation exactly at `end` belongs to the last bucket
            filled.add(min((ts - start_ms) // bucket_ms, n_buckets - 1))
    return len(filled) / n_buckets


def bounded_state_coverage_pct(
    valid_observations_ms: list[int],
    start_ms: int,
    end_ms: int,
    stale_after_seconds: float = 5.0,
    bucket_seconds: float = 1.0,
) -> float | None:
    """Coverage of reconstructed state, bounded by its stale deadline.

    A valid snapshot/update keeps the state usable only until
    ``observation + stale_after``.  This deliberately does not forward-fill
    beyond that bound.  A bucket is covered when the state is valid at the
    bucket start (or an observation inside that bucket establishes it).
    """
    if end_ms <= start_ms:
        return None
    bucket_ms = int(bucket_seconds * 1000)
    stale_ms = int(stale_after_seconds * 1000)
    n_buckets = max(1, (end_ms - start_ms + bucket_ms - 1) // bucket_ms)
    intervals=[]
    for ts in sorted(valid_observations_ms):
        lo=max(ts,start_ms);hi=min(ts+stale_ms,end_ms)
        if hi<=lo:continue
        if intervals and lo<=intervals[-1][1]:intervals[-1]=(intervals[-1][0],max(intervals[-1][1],hi))
        else:intervals.append((lo,hi))
    filled=set()
    for index in range(n_buckets):
        bucket_lo=start_ms+index*bucket_ms;bucket_hi=min(bucket_lo+bucket_ms,end_ms)
        if any(lo<=bucket_lo and hi>=bucket_hi for lo,hi in intervals):filled.add(index)
    return len(filled) / n_buckets


def find_gaps(
    observations_ms: list[int],
    start_ms: int,
    end_ms: int,
    threshold_seconds: float = 5.0,
) -> list[Gap]:
    """Gaps longer than *threshold_seconds* inside ``[start_ms, end_ms]``,
    including leading (start -> first obs) and trailing (last obs -> end)."""
    threshold_ms = int(threshold_seconds * 1000)
    inside = sorted(ts for ts in observations_ms if start_ms <= ts <= end_ms)
    gaps: list[Gap] = []
    if not inside:
        if end_ms - start_ms > threshold_ms:
            gaps.append(Gap(start_ms, end_ms))
        return gaps
    if inside[0] - start_ms > threshold_ms:
        gaps.append(Gap(start_ms, inside[0]))
    for prev, curr in zip(inside, inside[1:]):
        if curr - prev > threshold_ms:
            gaps.append(Gap(prev, curr))
    if end_ms - inside[-1] > threshold_ms:
        gaps.append(Gap(inside[-1], end_ms))
    return gaps


def stream_coverage(
    observations_ms: list[int],
    start_ms: int,
    end_ms: int,
    bucket_seconds: float = 1.0,
    gap_threshold_seconds: float = 5.0,
) -> StreamCoverage:
    inside = sorted(ts for ts in observations_ms if start_ms <= ts <= end_ms)
    gaps = find_gaps(observations_ms, start_ms, end_ms, gap_threshold_seconds)
    return StreamCoverage(
        coverage_pct=coverage_pct(observations_ms, start_ms, end_ms, bucket_seconds),
        n_observations=len(inside),
        first_observation_ms=inside[0] if inside else None,
        last_observation_ms=inside[-1] if inside else None,
        gaps=gaps,
        max_gap_ms=max((g.duration_ms for g in gaps), default=None),
    )


# ---------------------------------------------------------------------------
# Session manifest handling
# ---------------------------------------------------------------------------

@dataclass
class SessionRecord:
    session_id: str
    kind: str  # "polymarket_book" | "btc_ticks"
    events: list[dict[str, Any]]


def load_sessions(sessions_dir: Path | str) -> list[SessionRecord]:
    """Load all session journals from ``data/sessions/*.ndjson``."""
    records: list[SessionRecord] = []
    directory = Path(sessions_dir)
    if not directory.is_dir():
        return records
    for path in sorted(directory.glob("*.ndjson")):
        try:
            entries = list(read_ndjson(path))
        except ValueError as exc:
            logger.warning("unreadable session journal", extra={"path": str(path),
                                                                "error": repr(exc)})
            continue
        if not entries:
            continue
        records.append(SessionRecord(
            session_id=entries[0].get("session_id", path.stem),
            kind=entries[0].get("kind", "unknown"),
            events=entries,
        ))
    return records


def session_time_ranges(session: SessionRecord) -> list[tuple[int, int | None]]:
    """Connected intervals, with ``None`` denoting an open connection.

    An unterminated connection is not a zero-length interval.  Keeping the
    open end explicit prevents live source files from disappearing when a
    later, already-closed market window is audited.
    """
    ranges: list[tuple[int, int | None]] = []
    connected_at: int | None = None
    for event in session.events:
        ts = event.get("timestamp_ms")
        if ts is None:
            continue
        if event.get("event") == "connected":
            connected_at = ts
        elif event.get("event") in ("disconnected", "session_end") and connected_at is not None:
            ranges.append((connected_at, ts))
            connected_at = None
    if connected_at is not None:
        ranges.append((connected_at, None))
    return ranges


def session_overlaps(
    sessions: list[SessionRecord], start_ms: int, end_ms: int, kind: str
) -> bool:
    for session in sessions:
        if session.kind != kind:
            continue
        for lo, hi in session_time_ranges(session):
            if lo <= end_ms and (hi is None or hi >= start_ms):
                return True
    return False


def session_markets(session: SessionRecord) -> set[str]:
    """condition_ids a book session subscribed to."""
    markets: set[str] = set()
    for event in session.events:
        if event.get("event") == "subscribed" and event.get("market"):
            markets.add(event["market"])
    return markets


# ---------------------------------------------------------------------------
# File-based coverage provider (used by build_event_ledger)
# ---------------------------------------------------------------------------

class FileCoverageProvider:
    """Computes per-market coverage from raw NDJSON files + session journals.

    * book coverage: rows in ``data/raw/polymarket_book/*.ndjson`` whose
      ``condition_id`` matches; the reported percentage is the minimum over
      the market's outcome tokens (weakest side).
    * btc coverage: tick rows in ``data/raw/btc_ticks/*.ndjson`` within the
      market window.
    * ``book_expected`` / ``btc_expected`` are True when a recorded session
      overlaps the market window (and, for books, subscribed to that market):
      only then may the ledger exclude for missing data.
    """

    def __init__(
        self,
        book_dir: Path | str,
        btc_dir: Path | str,
        sessions_dir: Path | str,
        bucket_seconds: float = 1.0,
        gap_threshold_seconds: float = 5.0,
        book_stale_seconds: float = 5.0,
        book_files: list[Path | str] | None = None,
        btc_files: list[Path | str] | None = None,
    ) -> None:
        self.book_dir = Path(book_dir)
        self.btc_dir = Path(btc_dir)
        self.sessions_dir = Path(sessions_dir)
        self.bucket_seconds = bucket_seconds
        self.gap_threshold_seconds = gap_threshold_seconds
        self.book_stale_seconds = book_stale_seconds
        self.book_files = [Path(p) for p in book_files] if book_files is not None else None
        self.btc_files = [Path(p) for p in btc_files] if btc_files is not None else None
        self._sessions: list[SessionRecord] | None = None
        self._book_index: dict[str, list[dict[str, Any]]] | None = None
        self._btc_ts: list[int] | None = None
        self.last_detail: dict[str, StreamCoverage] = {}

    # -- lazy loading -----------------------------------------------------------

    def sessions(self) -> list[SessionRecord]:
        if self._sessions is None:
            self._sessions = load_sessions(self.sessions_dir)
        return self._sessions

    def book_rows_by_condition(self) -> dict[str, list[dict[str, Any]]]:
        if self._book_index is not None:
            return self._book_index
        index: dict[str, list[dict[str, Any]]] = defaultdict(list)
        if self.book_dir.is_dir():
            paths=self.book_files if self.book_files is not None else sorted(self.book_dir.rglob("*.ndjson"))
            for path in paths:
                for row in read_ndjson(path):
                    condition_id = row.get("condition_id")
                    if condition_id:
                        # Coverage must stay bounded in memory during long runs;
                        # retain only the fields needed by this audit.
                        compact = {
                            "condition_id": condition_id,
                            "token_id": row.get("token_id"),
                            "outcome": row.get("outcome"),
                            "receive_timestamp_ms": row.get("receive_timestamp_ms"),
                        }
                        if "book_state_valid" in row:
                            compact["book_state_valid"] = row.get("book_state_valid")
                        index[condition_id].append(compact)
        self._book_index = index
        return index

    def btc_timestamps(self) -> list[int]:
        if self._btc_ts is not None:
            return self._btc_ts
        stamps: list[int] = []
        if self.btc_dir.is_dir():
            paths=self.btc_files if self.btc_files is not None else sorted(self.btc_dir.rglob("*.ndjson"))
            for path in paths:
                for row in read_ndjson(path):
                    ts = row.get("exchange_timestamp_ms")
                    if isinstance(ts, int):
                        stamps.append(ts)
        self._btc_ts = stamps
        return stamps

    # -- CoverageProvider protocol -------------------------------------------------

    def market_coverage(
        self, condition_id: str, market_start_ms: int | None, market_end_ms: int | None
    ) -> MarketCoverage:
        if market_start_ms is None or market_end_ms is None:
            return MarketCoverage()
        sessions = self.sessions()

        # -- book side
        rows = self.book_rows_by_condition().get(condition_id, [])
        by_token: dict[str, list[int]] = defaultdict(list)
        has_validity = any("book_state_valid" in row for row in rows)
        for row in rows:
            token = row.get("token_id")
            ts = row.get("receive_timestamp_ms")
            if token and isinstance(ts, int) and (
                not has_validity or row.get("book_state_valid") is True
            ):
                by_token[token].append(ts)
        book_pct: float | None = None
        if by_token:
            per_token = [
                (bounded_state_coverage_pct(
                    ts, market_start_ms, market_end_ms,
                    self.book_stale_seconds, self.bucket_seconds,
                ) if has_validity else coverage_pct(
                    ts, market_start_ms, market_end_ms, self.bucket_seconds
                ))
                for ts in by_token.values()
            ]
            known = [p for p in per_token if p is not None]
            book_pct = min(known) if known else None
        book_expected = any(
            condition_id in session_markets(s)
            for s in sessions if s.kind == "polymarket_book"
        ) and session_overlaps(
            sessions, market_start_ms, market_end_ms, "polymarket_book"
        )

        # -- btc side
        btc_pct = coverage_pct(
            self.btc_timestamps(), market_start_ms, market_end_ms, self.bucket_seconds
        )
        btc_expected = session_overlaps(
            sessions, market_start_ms, market_end_ms, "btc_ticks"
        )
        return MarketCoverage(
            poly_book_coverage_pct=book_pct,
            btc_coverage_pct=btc_pct,
            book_expected=book_expected,
            btc_expected=btc_expected,
        )

    # -- report generation -----------------------------------------------------------

    def market_report(
        self, condition_id: str, market_start_ms: int, market_end_ms: int
    ) -> dict[str, Any]:
        """Detailed coverage + gap report for one market."""
        rows = self.book_rows_by_condition().get(condition_id, [])
        by_token: dict[str, list[int]] = defaultdict(list)
        has_validity = any("book_state_valid" in row for row in rows)
        outcome_by_token: dict[str, str | None] = {}
        for row in rows:
            token = row.get("token_id")
            ts = row.get("receive_timestamp_ms")
            if token and isinstance(ts, int) and (
                not has_validity or row.get("book_state_valid") is True
            ):
                by_token[token].append(ts)
                outcome_by_token[token] = row.get("outcome")
        token_reports = {}
        for token in sorted(by_token):
            sc = stream_coverage(
                by_token[token], market_start_ms, market_end_ms,
                self.bucket_seconds, self.gap_threshold_seconds,
            )
            state_pct = bounded_state_coverage_pct(
                by_token[token], market_start_ms, market_end_ms,
                self.book_stale_seconds, self.bucket_seconds,
            ) if has_validity else sc.coverage_pct
            token_reports[token] = {
                "outcome": outcome_by_token.get(token),
                "n_observations": sc.n_observations,
                "coverage_pct": state_pct,
                "coverage_basis": "bounded_valid_state" if has_validity else "message_buckets_legacy",
                "max_gap_ms": sc.max_gap_ms,
                "n_gaps": len(sc.gaps),
                "gaps": [
                    {"start_ms": g.start_ms, "end_ms": g.end_ms,
                     "duration_ms": g.duration_ms}
                    for g in sc.gaps
                ],
            }
        btc = stream_coverage(
            self.btc_timestamps(), market_start_ms, market_end_ms,
            self.bucket_seconds, self.gap_threshold_seconds,
        )
        coverages = [t["coverage_pct"] for t in token_reports.values()
                     if t["coverage_pct"] is not None]
        return {
            "condition_id": condition_id,
            "market_start_ms": market_start_ms,
            "market_end_ms": market_end_ms,
            "book_coverage_pct": min(coverages) if coverages else None,
            "book_tokens": token_reports,
            "btc_coverage_pct": btc.coverage_pct,
            "btc_n_observations": btc.n_observations,
            "btc_max_gap_ms": btc.max_gap_ms,
            "btc_gaps": [
                {"start_ms": g.start_ms, "end_ms": g.end_ms,
                 "duration_ms": g.duration_ms} for g in btc.gaps
            ],
        }


def write_json_report(payload: dict[str, Any], path: Path | str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
