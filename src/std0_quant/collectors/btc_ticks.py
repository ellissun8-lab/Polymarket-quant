"""BTC tick recorder (public Binance trade stream).

Source (recorded in every row and in README):
``wss://stream.binance.com:9443/ws/btcusdt@trade`` -- public, no auth,
millisecond exchange timestamps, one message per trade. Raw tick data (not
candles) is stored so arbitrary 5s/15s/30s/60s return windows can be built
later without lossy pre-aggregation.

Recorded schema per row (NDJSON, append-only):

* ``exchange_timestamp_ms`` -- Binance trade time (``T``)
* ``event_timestamp_ms``    -- Binance event time (``E``)
* ``receive_timestamp_ms``  -- local UTC receive time
* ``price``, ``size``       -- trade price / quantity
* ``trade_id``              -- Binance trade id (t)
* ``buyer_is_maker``        -- Binance ``m`` flag
* ``source``                -- e.g. ``binance:BTCUSDT@trade``
* ``raw_message``           -- the untouched message

Reconnect/backoff/stale handling is shared with the Polymarket recorder via
:class:`std0_quant.collectors.ws_runner.ReconnectingWsSession`.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

from std0_quant.collectors.ws_runner import (
    ReconnectingWsSession,
    SessionJournal,
    StreamStats,
)
from std0_quant.collectors.live_storage import RotatingNDJSON
from std0_quant.collectors.live_audit import GapTracker, LatencyTracker, TradeSequenceAudit
from std0_quant.timeutil import utc_now_ms

logger = logging.getLogger(__name__)

BTC_SCHEMA_VERSION = "binance_trade_v1"


def parse_binance_trade(message: dict[str, Any], receive_ts_ms: int,
                        source: str) -> dict[str, Any] | None:
    """Map a Binance ``@trade`` message to the tick row schema.

    Returns ``None`` for non-trade payloads (e.g. subscription acks) --
    those are not ticks and are not fabricated.
    """
    if message.get("e") != "trade":
        return None
    try:
        return {
            "source": source,
            "schema_version": BTC_SCHEMA_VERSION,
            "exchange_timestamp_ms": int(message["T"]),
            "event_timestamp_ms": int(message.get("E") or message["T"]),
            "receive_timestamp_ms": receive_ts_ms,
            "price": float(message["p"]),
            "size": float(message["q"]),
            "trade_id": message.get("t"),
            "buyer_is_maker": message.get("m"),
            "symbol": message.get("s"),
            "raw_message": message,
        }
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("malformed binance trade message",
                       extra={"error": repr(exc)})
        return None


def handle_binance_text(text: str, receive_ts_ms: int, source: str) -> list[dict[str, Any]]:
    """Parse one WebSocket frame into tick rows (usually 0 or 1)."""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("non-JSON binance frame ignored",
                       extra={"preview": text[:120]})
        return []
    messages = parsed if isinstance(parsed, list) else [parsed]
    rows: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, dict):
            row = parse_binance_trade(message, receive_ts_ms, source)
            if row is not None:
                rows.append(row)
    return rows


class TickRecorder:
    """Records the configured BTC trade stream to an append-only NDJSON file."""

    def __init__(
        self,
        ws_url: str,
        source: str,
        raw_dir: Path | str,
        journal: SessionJournal,
        stale_after_seconds: float = 30.0,
        backoff_base_seconds: float = 1.0,
        backoff_max_seconds: float = 60.0,
        connect: Callable[..., Any] | None = None,
        clock: Callable[[], int] = utc_now_ms,
        rotation_seconds: int = 3600,
        rotation_max_bytes: int = 268435456,
        fsync_every_records: int = 100,
    ) -> None:
        self.ws_url = ws_url
        self.source = source
        self.journal = journal
        self._clock = clock
        self._raw_writer = RotatingNDJSON(
            raw_dir, "binance_btc", journal.session_id, "btc",
            rotation_seconds=rotation_seconds, max_bytes=rotation_max_bytes,
            fsync_every=fsync_every_records, journal=journal,
        )
        self.raw_path = self._raw_writer.files[0]
        self.session = ReconnectingWsSession(
            ws_url=ws_url,
            journal=journal,
            stale_after_seconds=stale_after_seconds,
            backoff_base_seconds=backoff_base_seconds,
            backoff_max_seconds=backoff_max_seconds,
            connect=connect,
            clock=clock,
        )
        self.stats = self.session.stats
        self.rows_written = 0
        self.sequence_audit = TradeSequenceAudit()
        self.latency = LatencyTracker()
        self.gap_tracker = GapTracker("BINANCE_BTC", int(stale_after_seconds*1000))

    async def run(self) -> StreamStats:
        try:
            return await self.session.run(
                on_connect=self._on_connect,
                on_text=self._on_text,
            )
        finally:
            self._raw_writer.close()

    def stop(self) -> None:
        self.session.stop()

    async def _on_connect(self, ws: Any) -> None:
        # /ws/<stream> URLs need no SUBSCRIBE frame; the URL is the subscription.
        self.journal.emit("subscribed", url=self.ws_url, source=self.source)

    async def _on_text(self, ws: Any, text: str) -> None:
        rows = handle_binance_text(text, self._clock(), self.source)
        for row in rows:
            row["session_id"] = self.journal.session_id
            row["connection_id"] = self.session.connection_id
            row["collector_version"] = "phase2a_prospective_v4"
            row["latency_ms"] = (
                row["receive_timestamp_ms"] - row["exchange_timestamp_ms"]
            )
            row["detected_trade_id_gap"] = self.sequence_audit.observe(
                int(row["trade_id"])
            )
            self.latency.add(row["receive_timestamp_ms"],
                             row["exchange_timestamp_ms"])
            gap_count = len(self.gap_tracker.gaps)
            self.gap_tracker.observe(row["receive_timestamp_ms"])
            if len(self.gap_tracker.gaps) > gap_count:
                gap = self.gap_tracker.gaps[-1]
                self.journal.emit("gap_detected", source="BINANCE_BTC",
                                  start_ms=gap.start_ms, end_ms=gap.end_ms,
                                  duration_ms=gap.duration_ms)
            self._raw_writer.append(row)
            self.rows_written += 1
        if self.session.stats.messages % 200 == 0:
            self._raw_writer.flush()
