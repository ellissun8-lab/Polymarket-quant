"""Bounded-memory wrapper for the frozen BTC feature implementation.

Rows are streamed once.  Only the finite source window required by
``compute_btc_features`` plus one predecessor tick is retained, then the
existing frozen implementation performs the actual feature calculation.
"""
from __future__ import annotations

from typing import Any, Iterable

from .btc_features import compute_btc_features


def compute_btc_features_streaming(
    rows: Iterable[dict[str, Any]],
    market_start_ms: int,
    cutoff_ms: int,
) -> dict[str, Any]:
    """Compute frozen BTC features without materializing the full raw history.

    The retained interval matches the existing implementation's provenance
    floor:

        min(market_start_ms - 1000, cutoff_ms - 30000) .. cutoff_ms

    One eligible tick immediately before that floor is also retained because
    the frozen ``_last_at`` semantics may need a predecessor for a target
    timestamp.  No future rows are retained.
    """
    source_floor = min(
        int(market_start_ms) - 1000,
        int(cutoff_ms) - 30_000,
    )

    retained: list[dict[str, Any]] = []
    predecessor: dict[str, Any] | None = None
    predecessor_ts: int | None = None

    for row in rows:
        raw_ts = row.get("exchange_timestamp_ms")
        price = row.get("price")

        # Match compute_btc_features eligibility exactly.
        if raw_ts is None or price is None:
            continue

        ts = int(raw_ts)

        if ts > cutoff_ms:
            continue

        if ts >= source_floor:
            retained.append(row)
            continue

        # Keep the first encountered row at the greatest timestamp.
        # This matches Python's stable sort + max(key=timestamp) behavior
        # for equal-timestamp records in the frozen implementation.
        if predecessor_ts is None or ts > predecessor_ts:
            predecessor = row
            predecessor_ts = ts

    rows_for_compute = (
        [predecessor] + retained
        if predecessor is not None
        else retained
    )

    return compute_btc_features(
        rows_for_compute,
        market_start_ms,
        cutoff_ms,
    )
