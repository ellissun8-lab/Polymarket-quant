"""Polymarket realtime order book recorder (CLOB market channel).

Records every WebSocket message for both outcome tokens of a BTC Up/Down
market into an append-only NDJSON file, together with a derived
best-bid/best-ask snapshot per event.

Guarantees (spec section 5):

* automatic reconnect with exponential backoff, automatic resubscribe;
* raw messages append-only; nothing is forward- or back-filled;
* each row stores both ``receive_timestamp_ms`` (local UTC) and
  ``exchange_timestamp_ms`` (when the exchange stamps it);
* connection drops / stale feed are recorded in a per-session journal
  (``data/sessions/``) -- no silent data loss;
* after a market closes, a coverage report can be generated from the
  session + raw file (see :mod:`std0_quant.audit.coverage`).

Actual observed message schema is documented in README (the parser
tolerates both ``buy_levels``/``sell_levels`` and ``bids``/``asks`` level
arrays, and both unix-ms and ISO exchange timestamps).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import queue
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from std0_quant.collectors.ws_runner import (
    ReconnectingWsSession,
    SessionJournal,
    StreamStats,
)
from std0_quant.storage import AppendOnlyNDJSON
from std0_quant.collectors.live_storage import (
    RotatingNDJSON,
    SidecarFinalizationError,
)
from std0_quant.collectors.live_audit import BookValidity, GapTracker, LatencyTracker
from std0_quant.timeutil import parse_ts_to_ms, utc_now_ms

logger = logging.getLogger(__name__)

BOOK_SCHEMA_VERSION = "polymarket_clob_market_v4_compact_async_writer"


# ---------------------------------------------------------------------------
# Pure message handling (fully unit-tested; no I/O)
# ---------------------------------------------------------------------------

@dataclass
class TokenBook:
    """Best-known state of one outcome token's book."""

    token_id: str
    outcome: str
    bids: dict[float, float] = field(default_factory=dict)  # price -> size
    asks: dict[float, float] = field(default_factory=dict)
    last_trade_price: float | None = None
    last_trade_size: float | None = None
    last_trade_side: str | None = None

    def best_bid(self) -> float | None:
        sizes = [p for p, s in self.bids.items() if s > 0]
        return max(sizes) if sizes else None

    def best_ask(self) -> float | None:
        sizes = [p for p, s in self.asks.items() if s > 0]
        return min(sizes) if sizes else None


def parse_levels(raw_levels: Any) -> list[tuple[float, float]]:
    """Parse a levels array (``[{"price": "0.4", "size": "100"}, ...]``)."""
    if not isinstance(raw_levels, list):
        return []
    levels: list[tuple[float, float]] = []
    for entry in raw_levels:
        if not isinstance(entry, dict):
            continue
        try:
            price = float(entry.get("price"))
            size = float(entry.get("size"))
        except (TypeError, ValueError):
            continue
        levels.append((price, size))
    return levels


class BookState:
    """Tracks book state for the subscribed tokens and turns raw messages
    into derived rows. Pure logic: injectable, deterministic, testable."""

    def __init__(self, condition_id: str, tokens: list[tuple[str, str]]) -> None:
        self.condition_id = condition_id
        self.books: dict[str, TokenBook] = {
            token_id: TokenBook(token_id, outcome) for token_id, outcome in tokens
        }

    # -- message application --------------------------------------------------

    def apply(self, message: dict[str, Any], receive_ts_ms: int) -> dict[str, Any] | None:
        """Apply one parsed event; return the derived row (or None if the
        message carries no recognizable event_type)."""
        event_type = message.get("event_type")
        token_id = message.get("asset_id") or message.get("token_id")
        book = self.books.get(token_id)
        if book is None:
            # Message for a token we do not subscribe to: keep raw only.
            return self._row(book=None, message=message, receive_ts_ms=receive_ts_ms)

        if event_type == "book":
            bids = parse_levels(message.get("buy_levels") or message.get("bids"))
            asks = parse_levels(message.get("sell_levels") or message.get("asks"))
            book.bids = dict(bids)
            book.asks = dict(asks)
        elif event_type == "price_change":
            changes = message.get("changes", [])
            if isinstance(changes, list):
                for change in changes:
                    if not isinstance(change, dict):
                        continue
                    try:
                        price = float(change.get("price"))
                        size = float(change.get("size"))
                    except (TypeError, ValueError):
                        continue
                    side = str(change.get("side", "")).upper()
                    if side == "BUY":
                        book.bids[price] = size
                    elif side == "SELL":
                        book.asks[price] = size
        elif event_type == "last_trade_price":
            try:
                book.last_trade_price = (
                    float(message["price"]) if message.get("price") is not None else None
                )
                book.last_trade_size = (
                    float(message["size"]) if message.get("size") is not None else None
                )
            except (TypeError, ValueError):
                pass
            book.last_trade_side = message.get("side")
        # tick_size_change / unknown events: raw-only rows.

        return self._row(book=book, message=message, receive_ts_ms=receive_ts_ms)

    # -- row derivation ---------------------------------------------------------

    def _row(
        self, book: TokenBook | None, message: dict[str, Any], receive_ts_ms: int
    ) -> dict[str, Any]:
        best_bid = book.best_bid() if book else None
        best_ask = book.best_ask() if book else None
        mid = (
            (best_bid + best_ask) / 2.0
            if best_bid is not None and best_ask is not None
            else None
        )
        spread = (
            best_ask - best_bid
            if best_bid is not None and best_ask is not None
            else None
        )
        return {
            "source": "polymarket:clob-ws:market",
            "schema_version": BOOK_SCHEMA_VERSION,
            "receive_timestamp_ms": receive_ts_ms,
            "exchange_timestamp_ms": parse_ts_to_ms(message.get("timestamp")),
            "exchange_timestamp_raw": message.get("timestamp"),
            "condition_id": message.get("market") or self.condition_id,
            "token_id": message.get("asset_id") or message.get("token_id"),
            "outcome": book.outcome if book else None,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "mid": mid,
            "spread": spread,
            # Full snapshots remain in raw_message. Persisting top-3 state is
            # sufficient for the frozen Phase 2A depth/OBI features and keeps
            # high-frequency incrementals from duplicating the entire book.
            "bids": _sorted_levels(book.bids, 3) if book else None,
            "asks": _sorted_levels(book.asks, 3, reverse=False) if book else None,
            "last_trade_price": book.last_trade_price if book else None,
            "last_trade_size": book.last_trade_size if book else None,
            "last_trade_side": book.last_trade_side if book else None,
            "event_type": message.get("event_type"),
            "raw_message": message.get("_raw_parent", message) if message.get("_store_raw", True) else None,
            "raw_message_ref": message.get("_raw_frame_id"),
            "applied_change": message.get("_applied_change"),
        }


def _sorted_levels(levels: dict[float, float], limit: int | None = None,
                   reverse: bool = True) -> list[dict[str, float]]:
    return [
        {"price": price, "size": size}
        for price, size in sorted(levels.items(), key=lambda kv: kv[0], reverse=reverse)[:limit]
        if size > 0
    ]


def handle_raw_text(
    state: BookState, text: str, receive_ts_ms: int
) -> list[dict[str, Any]]:
    """Parse one WebSocket text frame into derived rows.

    The CLOB market channel sends JSON arrays of events (single dicts are
    tolerated). The application-level keepalive text ``"PING"`` is NOT book
    data and returns no rows (the connection layer answers ``PONG``).
    """
    if text.strip() in ("PING", "PONG", "ping", "pong"):
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("non-JSON websocket frame dropped from derivation "
                       "(raw is still journaled by the caller)",
                       extra={"preview": text[:120]})
        return []
    events = parsed if isinstance(parsed, list) else [parsed]
    rows: list[dict[str, Any]] = []
    for event in events:
        if isinstance(event, dict):
            # Current CLOB schema batches token-specific changes under
            # ``price_changes``. Expand them deterministically so both token
            # books advance and every derived row has a token identity.
            changes = event.get("price_changes")
            if event.get("event_type") == "price_change" and isinstance(changes, list):
                frame_id = hashlib.sha256(
                    f"{receive_ts_ms}|{event.get('market')}|{event.get('timestamp')}".encode()
                ).hexdigest()
                for index, change in enumerate(changes):
                    if not isinstance(change, dict):
                        continue
                    expanded = {**event, "asset_id": change.get("asset_id"),
                                "changes": [change], "_raw_parent": event,
                                "_raw_frame_id": frame_id,
                                "_applied_change": change,
                                "_store_raw": index == 0}
                    row = state.apply(expanded, receive_ts_ms)
                    if row is not None:
                        rows.append(row)
            else:
                row = state.apply(event, receive_ts_ms)
                if row is not None:
                    rows.append(row)
    return rows


def build_subscribe_message(token_ids: list[str]) -> str:
    return json.dumps({"assets_ids": token_ids, "type": "market"})


# ---------------------------------------------------------------------------
# Market discovery (gamma API)
# ---------------------------------------------------------------------------

@dataclass
class MarketInfo:
    condition_id: str
    slug: str
    market_start_ms: int
    market_end_ms: int
    tokens: list[tuple[str, str]]  # (token_id, outcome)


def _tokens_from_gamma_market(market: dict[str, Any]) -> list[tuple[str, str]]:
    """Outcome -> token_id mapping from a gamma market object.

    Prefers the explicit ``tokens`` array; falls back to ``clobTokenIds``
    (a JSON-encoded string array) zipped with ``outcomes``.
    """
    tokens = market.get("tokens")
    if isinstance(tokens, list) and tokens:
        result = []
        for entry in tokens:
            if isinstance(entry, dict) and entry.get("token_id"):
                result.append((str(entry["token_id"]), str(entry.get("outcome"))))
        if result:
            return result
    outcomes = market.get("outcomes")
    clob_ids = market.get("clobTokenIds")
    if isinstance(outcomes, str):
        try:
            outcomes = json.loads(outcomes)
        except json.JSONDecodeError:
            outcomes = None
    if isinstance(outcomes, list) and isinstance(clob_ids, str):
        try:
            ids = json.loads(clob_ids)
        except json.JSONDecodeError:
            return []
        if isinstance(ids, list) and len(ids) == len(outcomes):
            return [(str(i), str(o)) for i, o in zip(ids, outcomes)]
    return []


def _fetch_market_by_slug(
    fetch_fn: Callable[[str, dict[str, Any]], tuple[int, str]],
    gamma_base_url: str,
    slug: str,
) -> dict[str, Any] | None:
    params = {"slug": slug}
    status, body = fetch_fn(f"{gamma_base_url.rstrip('/')}/markets", params)
    if status != 200:
        logger.warning("gamma slug lookup failed",
                       extra={"slug": slug, "status": status})
        return None
    try:
        markets = json.loads(body)
    except json.JSONDecodeError:
        return None
    if isinstance(markets, dict):
        markets = markets.get("data", [])
    if not isinstance(markets, list):
        return None
    for market in markets:
        if isinstance(market, dict) and market.get("slug") == slug:
            return market
    return None


def find_active_market(
    fetch_fn: Callable[[str, dict[str, Any]], tuple[int, str]],
    gamma_base_url: str,
    now_ms: int,
    slug_prefix: str,
    window_seconds: int = 300,
) -> MarketInfo | None:
    """Find the currently-running market by computing its slug from the clock.

    Real observed slug format (verified live): ``<prefix><unix_start_seconds>``
    where the trading window is ``[ts, ts + window_seconds)`` and ts is
    window-aligned (e.g. 5-minute). Candidates around the current boundary are
    looked up on gamma by slug; the window is taken from the slug itself
    (gamma ``startDate`` is the market CREATION time, not the window start).
    """
    now_s = now_ms // 1000
    current_start = now_s - (now_s % window_seconds)
    # current window first; then the previous (boundary race) and next.
    candidates = [current_start, current_start - window_seconds,
                  current_start + window_seconds]
    for start_s in candidates:
        slug = f"{slug_prefix}{start_s}"
        market = _fetch_market_by_slug(fetch_fn, gamma_base_url, slug)
        if market is None:
            continue
        market_start_ms = start_s * 1000
        market_end_ms = market_start_ms + window_seconds * 1000
        gamma_end_ms = parse_ts_to_ms(market.get("endDate"))
        if gamma_end_ms is not None and abs(gamma_end_ms - market_end_ms) > 60_000:
            logger.warning(
                "gamma endDate disagrees with slug-derived window; trusting slug",
                extra={"slug": slug, "gamma_end_ms": gamma_end_ms,
                       "slug_end_ms": market_end_ms},
            )
        if market_end_ms <= now_ms:
            continue  # window already over
        tokens = _tokens_from_gamma_market(market)
        if len(tokens) != 2:
            logger.warning("market lacks two outcome tokens; skipping",
                           extra={"slug": slug})
            continue
        condition_id = market.get("conditionId")
        if not condition_id:
            continue
        return MarketInfo(
            condition_id=condition_id, slug=slug,
            market_start_ms=market_start_ms, market_end_ms=market_end_ms,
            tokens=tokens,
        )
    return None


# ---------------------------------------------------------------------------
# Async WebSocket recorder
# ---------------------------------------------------------------------------

class BookRecorder:
    """Records the CLOB market channel for a fixed set of tokens.

    Reconnect behavior: exponential backoff (base * 2^n capped at max);
    after every (re)connect the subscription is re-sent and the exchange
    re-sends full book snapshots, so state is rebuilt without any local
    forward/back-filling.
    """

    def __init__(
        self,
        ws_url: str,
        market: MarketInfo,
        raw_dir: Path | str,
        journal: SessionJournal,
        stale_after_seconds: float = 20.0,
        backoff_base_seconds: float = 1.0,
        backoff_max_seconds: float = 60.0,
        connect: Callable[..., Any] | None = None,
        clock: Callable[[], int] = utc_now_ms,
        rotation_seconds: int = 3600,
        rotation_max_bytes: int = 268435456,
        fsync_every_records: int = 100,
        writer_queue_batches: int = 1000,
    ) -> None:
        self.ws_url = ws_url
        self.market = market
        self.journal = journal
        self.stale_after_seconds = stale_after_seconds
        self.backoff_base_seconds = backoff_base_seconds
        self.backoff_max_seconds = backoff_max_seconds
        self._connect = connect
        self._clock = clock
        self.state = BookState(
            market.condition_id, [(tid, out) for tid, out in market.tokens]
        )
        stamp = utc_now_ms()
        self.raw_path = (
            Path(raw_dir) / f"book_{stamp}_{journal.session_id}.ndjson"
        )
        self._raw_writer = RotatingNDJSON(
            raw_dir, "polymarket_book", journal.session_id, "book",
            rotation_seconds=rotation_seconds, max_bytes=rotation_max_bytes,
            fsync_every=fsync_every_records, journal=journal,
        )
        self.raw_path = self._raw_writer.files[0]
        self._writer_error: BaseException | None = None
        self._writer_error_kind: str | None = None
        self._write_queue: queue.Queue[list[dict[str, Any]] | None] = queue.Queue(maxsize=writer_queue_batches)
        self._writer_thread = threading.Thread(target=self._writer_loop,
                                               name=f"book-writer-{journal.session_id}",daemon=True)
        self._writer_thread.start()
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
        self.validity = BookValidity(int(stale_after_seconds*1000))
        self.latency = LatencyTracker()
        self.gap_tracker = GapTracker("POLYMARKET_BOOK", int(stale_after_seconds*1000))
        self.subscription_id = f"{journal.session_id}-sub-1"
        self._snapshot_tokens: set[str] = set()

    # -- public API -------------------------------------------------------------

    async def run(self) -> StreamStats:
        """Run until :meth:`stop` is called; reconnect forever in between."""
        try:
            return await self.session.run(
                on_connect=self._on_connect,
                on_text=self._on_text,
            )
        finally:
            await asyncio.to_thread(self._write_queue.put, None)
            await asyncio.to_thread(self._writer_thread.join)
            if self._writer_error is not None:
                # classify before raising: RAW_WRITE_FAILURE (rows lost),
                # SIDECAR_FINALIZATION_FAILURE (raw durable, meta missing) and
                # MEMORY_ERROR have different recovery paths; the journal
                # event is emitted on the loop thread (journal is not
                # thread-safe for concurrent appends).
                self.journal.emit("writer_failed",
                                  failure_kind=self._writer_error_kind,
                                  error=repr(self._writer_error))
                raise RuntimeError(
                    f"{self._writer_error_kind}: book writer failed"
                ) from self._writer_error

    def _writer_loop(self) -> None:
        try:
            while True:
                batch = self._write_queue.get()
                if batch is None: break
                for row in batch:self._raw_writer.append(row)
                self._write_queue.task_done()
            self._raw_writer.close()
        except SidecarFinalizationError as exc:
            self._writer_error = exc
            self._writer_error_kind = "SIDECAR_FINALIZATION_FAILURE"
        except MemoryError as exc:
            self._writer_error = exc
            self._writer_error_kind = "MEMORY_ERROR"
        except BaseException as exc:
            self._writer_error = exc
            self._writer_error_kind = "RAW_WRITE_FAILURE"

    def stop(self) -> None:
        self.session.stop()

    # -- hooks --------------------------------------------------------------------

    async def _on_connect(self, ws: Any) -> None:
        self.state = BookState(
            self.market.condition_id,
            [(tid, out) for tid, out in self.market.tokens],
        )
        self.validity.connect(self.session.connection_id)
        self._snapshot_tokens.clear()
        token_ids = [t for t, _ in self.market.tokens]
        await ws.send(build_subscribe_message(token_ids))
        self.journal.emit(
            "subscribed", market=self.market.condition_id, slug=self.market.slug,
            tokens=token_ids,
            connection_id=self.session.connection_id,
            subscription_id=self.subscription_id,
        )

    async def _on_text(self, ws: Any, text: str) -> None:
        if text.strip() == "PING":
            await ws.send("PONG")  # CLOB application-level keepalive
            return
        receive_ts = self._clock()
        rows = handle_raw_text(self.state, text, receive_ts)
        persisted=[]
        for row in rows:
            token_id = row.get("token_id")
            if row.get("event_type") == "book" and token_id:
                self._snapshot_tokens.add(str(token_id))
            valid = self.validity.apply(
                row.get("event_type"), receive_ts, sane=_row_sane(row)
            )
            snapshot_ready = {str(t) for t, _ in self.market.tokens}.issubset(
                self._snapshot_tokens
            )
            row["book_state_status"] = (
                self.validity.state if snapshot_ready else "UNINITIALIZED"
            )
            row["book_state_valid"] = valid and snapshot_ready
            row["session_id"] = self.journal.session_id
            row["connection_id"] = self.session.connection_id
            row["subscription_id"] = self.subscription_id
            row["collector_version"] = "phase2a_prospective_v4"
            row["latency_ms"] = (
                receive_ts - row["exchange_timestamp_ms"]
                if row.get("exchange_timestamp_ms") is not None else None
            )
            self.latency.add(receive_ts, row.get("exchange_timestamp_ms"))
            gap_count = len(self.gap_tracker.gaps)
            self.gap_tracker.observe(receive_ts)
            if len(self.gap_tracker.gaps) > gap_count:
                gap = self.gap_tracker.gaps[-1]
                self.journal.emit("gap_detected", source="POLYMARKET_BOOK",
                                  start_ms=gap.start_ms, end_ms=gap.end_ms,
                                  duration_ms=gap.duration_ms)
            persisted.append(row)
            self.rows_written += 1
        if persisted:
            if self._write_queue.full():
                self.journal.emit("queue_backpressure",source="POLYMARKET_BOOK",
                                  queue_batches=self._write_queue.qsize())
            await asyncio.to_thread(self._write_queue.put,persisted)
        if self._writer_error is not None:
            raise RuntimeError(
                f"{self._writer_error_kind}: book writer failed"
            ) from self._writer_error


def _row_sane(row: dict[str, Any]) -> bool:
    bid, ask = row.get("best_bid"), row.get("best_ask")
    if bid is not None and not 0 <= bid <= 1:
        return False
    if ask is not None and not 0 <= ask <= 1:
        return False
    if bid is not None and ask is not None and bid > ask:
        return False
    return True
