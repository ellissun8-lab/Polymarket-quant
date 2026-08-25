"""Fill model and raw-envelope loading for the events pipeline.

A :class:`Fill` is one normalized raw fill record (see spec section 1.1).
Raw data is append-only; this module only *reads* it.

``Fill`` uses ``slots=True`` and callers can load millions of fills without
their ``raw_json`` payload (``keep_raw_json=False``): every analytical field
is a first-class dataclass field, and the raw NDJSON stays the single source
of truth for the untouched record.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from std0_quant.collectors.std0_trades import normalize_fill
from std0_quant.storage import read_ndjson

logger = logging.getLogger(__name__)

BUY = "BUY"
SELL = "SELL"


@dataclass(frozen=True, slots=True)
class Fill:
    fill_id: str
    proxy_wallet: str | None
    side: str | None
    asset: str | None
    condition_id: str | None
    size: float | None
    price: float | None
    timestamp_ms: int | None
    timestamp_raw: object
    title: str | None
    slug: str | None
    outcome: str | None
    outcome_index: int | None
    transaction_hash: str | None
    source: str
    fetched_at_ms: int
    raw_json: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_buy(self) -> bool:
        return self.side == BUY

    @property
    def direction(self) -> str | None:
        """Direction = outcome label (Up / Down for BTC 5m markets)."""
        return self.outcome


def fill_to_row(fill: Fill) -> dict[str, Any]:
    """Fill -> plain dict (same schema as ``normalize_fill`` minus raw_json)."""
    return {
        f.name: getattr(fill, f.name) for f in fields(fill) if f.name != "raw_json"
    }


def fill_from_envelope(envelope: dict[str, Any], *, keep_raw_json: bool = True) -> Fill:
    """Build a Fill from one raw-store NDJSON line (envelope + untouched record)."""
    normalized = normalize_fill(
        envelope["record"], envelope.get("source", "unknown"),
        envelope.get("fetched_at_ms", 0),
    )
    if not keep_raw_json:
        normalized["raw_json"] = {}
    return Fill(**normalized)


def load_fills(
    paths: Path | str | Iterable[Path | str], *, keep_raw_json: bool = True
) -> Iterator[Fill]:
    """Stream fills from raw NDJSON file(s), preserving file order."""
    if isinstance(paths, (str, Path)):
        paths = [paths]
    seen: set[str] = set()
    duplicates = 0
    for path in paths:
        for envelope in read_ndjson(path):
            fill = fill_from_envelope(envelope, keep_raw_json=keep_raw_json)
            if fill.fill_id in seen:
                # Defensive: the syncer's dedupe makes this impossible by
                # construction. If it ever happens, count and warn loudly --
                # never silently double-count sizes.
                duplicates += 1
                continue
            seen.add(fill.fill_id)
            yield fill
    if duplicates:
        logger.warning(
            "duplicate fill identities found in raw store (skipped on replay)",
            extra={"duplicates": duplicates},
        )
