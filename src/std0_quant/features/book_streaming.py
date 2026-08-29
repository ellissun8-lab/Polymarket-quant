"""Bounded-memory equivalent of Phase 2A Polymarket book features.

The frozen feature definitions are unchanged.  This implementation consumes
an iterable once and retains only the finite state required by those
definitions, rather than materializing and sorting all book rows.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable

from .book_reconstruction import depth, obi, row_timestamp


_TARGET_SECONDS = (0, 1, 3, 5, 10)
_COUNT_SECONDS = (1, 5)
_COVERAGE_SECONDS = (5, 10, 30)


@dataclass
class _Accumulator:
    """Finite state for one eligibility interpretation."""

    latest: dict[int, dict[str, dict[str, Any]]] = field(
        default_factory=lambda: {seconds: {} for seconds in _TARGET_SECONDS}
    )
    update_counts: dict[int, int] = field(
        default_factory=lambda: {seconds: 0 for seconds in _COUNT_SECONDS}
    )
    fallback_buckets: dict[int, set[int]] = field(
        default_factory=lambda: {seconds: set() for seconds in _COVERAGE_SECONDS}
    )
    valid_buckets: dict[int, dict[str, set[int]]] = field(
        default_factory=lambda: {seconds: {} for seconds in _COVERAGE_SECONDS}
    )
    source_min_ms: int | None = None
    source_max_ms: int | None = None
    row_count: int = 0

    def observe(
        self,
        row: dict[str, Any],
        cutoff_ms: int,
        stale_after_ms: int,
    ) -> None:
        raw_ts = row.get("receive_timestamp_ms")
        if raw_ts is None:
            return

        ts = int(raw_ts)
        if ts > cutoff_ms:
            return

        self.row_count += 1
        self.source_min_ms = ts if self.source_min_ms is None else min(self.source_min_ms, ts)
        self.source_max_ms = ts if self.source_max_ms is None else max(self.source_max_ms, ts)

        token = row.get("token_id")
        if token:
            token = str(token)
            for seconds in _TARGET_SECONDS:
                target = cutoff_ms - seconds * 1000
                if ts <= target:
                    prior = self.latest[seconds].get(token)
                    # sorted(..., key=timestamp) is stable; for equal timestamps
                    # the later input row wins in the existing implementation.
                    if prior is None or ts >= row_timestamp(prior):
                        self.latest[seconds][token] = row

        for seconds in _COUNT_SECONDS:
            start = cutoff_ms - seconds * 1000
            if start < ts <= cutoff_ms:
                self.update_counts[seconds] += 1

        for seconds in _COVERAGE_SECONDS:
            start = cutoff_ms - seconds * 1000
            end = cutoff_ms
            n = math.ceil((end - start) / 1000)

            # Backwards-compatible bucket_coverage state for fixtures/raw
            # with no book_state_valid field anywhere.
            if start <= ts <= end:
                bucket = min((ts - start) // 1000, n - 1)
                self.fallback_buckets[seconds].add(int(bucket))

            # State for bounded_book_coverage when validity semantics apply.
            if token:
                lo = max(ts, start)
                hi = min(ts + stale_after_ms, end)
                if hi <= lo:
                    continue

                first_bucket = max(0, math.ceil((lo - start) / 1000))
                last_bucket = min(n - 1, math.floor((hi - start) / 1000) - 1)

                if first_bucket <= last_bucket:
                    occupied = self.valid_buckets[seconds].setdefault(token, set())
                    occupied.update(range(first_bucket, last_bucket + 1))

    def latest_by_outcome(
        self,
        seconds: int,
        cutoff_ms: int,
        stale_after_ms: int,
    ) -> dict[str, dict[str, Any]]:
        target = cutoff_ms - seconds * 1000
        rows = {
            token: row
            for token, row in self.latest[seconds].items()
            if target - row_timestamp(row) <= stale_after_ms
        }
        return {
            str(row.get("outcome")): row
            for row in rows.values()
            if row
        }

    def coverage(self, seconds: int, *, validity_mode: bool) -> float | None:
        if self.row_count == 0:
            return None

        n = seconds

        if not validity_mode:
            occupied = self.fallback_buckets[seconds]
            return len(occupied) / n if occupied else None

        by_token = self.valid_buckets[seconds]
        if not by_token:
            return None

        values = [len(occupied) / n for occupied in by_token.values()]
        return min(values) if values else None


def compute_book_features_streaming(
    rows: Iterable[dict[str, Any]],
    cutoff_ms: int,
    opp_outcome: str,
    initial_outcome: str,
    stale_after_ms: int = 5000,
) -> dict[str, Any]:
    """Compute the frozen book feature set in bounded memory.

    Semantics match ``compute_book_features``:

    * receive timestamp is the observable clock;
    * if ANY input row contains ``book_state_valid``, only rows where it is
      exactly True are feature-eligible;
    * otherwise legacy/raw fixtures use all timestamped rows;
    * cutoff boundaries, stale handling and coverage definitions are unchanged.
    """

    all_rows = _Accumulator()
    valid_rows = _Accumulator()
    has_validity = False

    for row in rows:
        if "book_state_valid" in row:
            has_validity = True

        all_rows.observe(row, cutoff_ms, stale_after_ms)

        if row.get("book_state_valid") is True:
            valid_rows.observe(row, cutoff_ms, stale_after_ms)

    acc = valid_rows if has_validity else all_rows

    current = acc.latest_by_outcome(0, cutoff_ms, stale_after_ms)

    out: dict[str, Any] = {}

    for frame, outcome in (("opp", opp_outcome), ("initial", initial_outcome)):
        row = current.get(outcome)

        for name in ("best_bid", "best_ask", "mid", "spread"):
            out[f"{frame}_{name}"] = row.get(name) if row else None

        for count in (1, 3):
            out[f"{frame}_bid_depth_{count}"] = (
                depth(row.get("bids"), count) if row else None
            )
            out[f"{frame}_ask_depth_{count}"] = (
                depth(row.get("asks"), count) if row else None
            )
            out[f"{frame}_obi_{count}"] = obi(row, count)

    for seconds in (1, 3, 5, 10):
        previous = acc.latest_by_outcome(
            seconds, cutoff_ms, stale_after_ms
        ).get(opp_outcome)

        previous_mid = previous.get("mid") if previous else None
        previous_obi = obi(previous, 1)

        out[f"pm_mid_change_{seconds}s"] = (
            out.get("opp_mid") - previous_mid
            if out.get("opp_mid") is not None and previous_mid is not None
            else None
        )

        if seconds in (1, 5):
            out[f"pm_obi_change_{seconds}s"] = (
                out.get("opp_obi_1") - previous_obi
                if out.get("opp_obi_1") is not None and previous_obi is not None
                else None
            )
            out[f"book_update_count_{seconds}s"] = (
                acc.update_counts[seconds] if acc.row_count else None
            )

    previous5 = acc.latest_by_outcome(
        5, cutoff_ms, stale_after_ms
    ).get(opp_outcome)

    out["pm_spread_change_5s"] = (
        out["opp_spread"] - previous5.get("spread")
        if (
            out.get("opp_spread") is not None
            and previous5
            and previous5.get("spread") is not None
        )
        else None
    )

    for seconds in _COVERAGE_SECONDS:
        out[f"book_pre{seconds}_coverage_pct"] = acc.coverage(
            seconds,
            validity_mode=has_validity,
        )

    out["_source_min_ms"] = acc.source_min_ms
    out["_source_max_ms"] = acc.source_max_ms
    return out
