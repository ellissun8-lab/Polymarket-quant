"""Generic reconnecting WebSocket session runner.

Shared machinery for the realtime collectors:

* connect -> on_connect hook (subscribe etc.) -> read loop -> on_text hook;
* automatic reconnect with exponential backoff;
* stale-feed watchdog (force-closes the socket when the feed goes quiet);
* every lifecycle event is journaled (connect, subscribe, disconnect,
  stale feed, reconnect schedule) -- no silent data loss.
"""

from __future__ import annotations

import asyncio
import random
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from std0_quant.storage import AppendOnlyNDJSON
from std0_quant.timeutil import utc_now_ms
from std0_quant.collectors.network_stability import (
    classify_network_error, is_receive_stale, probe_proxy, proxy_for_url,
)

logger = logging.getLogger(__name__)


def compute_backoff_seconds(attempt: int, base: float, maximum: float) -> float:
    return min(base * (2 ** max(0, attempt - 1)), maximum)


class SessionJournal:
    """Append-only journal of collector session lifecycle events."""

    def __init__(self, sessions_dir: Path | str, session_id: str, kind: str) -> None:
        self.path = Path(sessions_dir) / f"{session_id}.ndjson"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.session_id = session_id
        self.kind = kind
        self._writer = AppendOnlyNDJSON(self.path)
        self.emit("session_start")

    def emit(self, event: str, **details: Any) -> None:
        entry = {
            "session_id": self.session_id,
            "kind": self.kind,
            "event": event,
            "event_code": event.upper(),
            "source": details.get("source", self.kind),
            "detail": dict(details),
            "timestamp_ms": utc_now_ms(),
            **details,
        }
        self._writer.append(entry)
        self._writer.flush()

    def close(self, event: str = "session_end", **details: Any) -> None:
        self.emit(event, **details)
        self._writer.close()

    def __enter__(self) -> SessionJournal:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


@dataclass
class StreamStats:
    session_id: str
    messages: int = 0
    reconnects: int = 0
    stale_reconnects: int = 0
    errors: list[str] = field(default_factory=list)


class ReconnectingWsSession:
    """Runs ``on_text`` against a WebSocket that must never stay down.

    Hooks (all coroutines):
    * ``on_connect(ws)`` -- send subscriptions; raise to force a reconnect;
    * ``on_text(ws, text)`` -- handle one text frame;
    * ``on_clean_disconnect()`` -- optional bookkeeping after a server-side
      close that was NOT an error.
    """

    def __init__(
        self,
        ws_url: str,
        journal: SessionJournal,
        stale_after_seconds: float,
        backoff_base_seconds: float = 1.0,
        backoff_max_seconds: float = 60.0,
        connect: Callable[..., Any] | None = None,
        clock: Callable[[], int] = utc_now_ms,
        max_queue_messages: int = 4096,
    ) -> None:
        self.ws_url = ws_url
        self.journal = journal
        self.stale_after_ms = int(stale_after_seconds * 1000)
        self.backoff_base = backoff_base_seconds
        self.backoff_max = backoff_max_seconds
        self._connect = connect
        self._clock = clock
        self.stats = StreamStats(session_id=journal.session_id)
        self._last_message_ms = 0
        self._stop = asyncio.Event()
        self.connection_id: str | None = None
        self._connection_sequence = 0
        self._active_ws: Any | None = None
        self.max_queue_messages = max_queue_messages
        # close-task ownership: keep references so the tasks cannot be
        # garbage-collected with an unretrieved exception ("Task exception
        # was never retrieved"); _consume_close_exception retrieves it.
        self._close_tasks: set[asyncio.Task] = set()

    async def run(
        self,
        on_connect: Callable[[Any], Any],
        on_text: Callable[[Any, str], Any],
        on_clean_disconnect: Callable[[], Any] | None = None,
    ) -> StreamStats:
        import websockets

        connect = self._connect or websockets.connect
        attempt = 0
        try:
            while not self._stop.is_set():
                try:
                    await self._run_one_connection(connect, on_connect, on_text)
                    if on_clean_disconnect is not None:
                        await on_clean_disconnect()
                    attempt = 0
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.stats.errors.append(repr(exc))
                    detail = classify_network_error(exc)
                    self.journal.emit("connection_error", error=repr(exc),
                                      stage=("READ" if self.connection_id else "CONNECT"),
                                      **detail)
                if self._stop.is_set():
                    break
                attempt += 1
                delay = compute_backoff_seconds(
                    attempt, self.backoff_base, self.backoff_max
                )
                delay *= random.uniform(0.8, 1.2)
                self.journal.emit("reconnect_scheduled",
                                  attempt=attempt, delay_seconds=delay)
                await asyncio.sleep(delay)
        finally:
            self.journal.emit(
                "session_end", messages=self.stats.messages,
                reconnects=self.stats.reconnects,
                stale_reconnects=self.stats.stale_reconnects,
            )
        return self.stats

    def stop(self) -> None:
        self._stop.set()
        # Wake an ``async for ws`` immediately; merely setting the flag would
        # otherwise wait for the next network frame or stale timeout.
        if self._active_ws is not None:
            try:
                task = asyncio.get_running_loop().create_task(
                    self._active_ws.close())
                self._close_tasks.add(task)
                task.add_done_callback(self._consume_close_exception)
            except RuntimeError:
                pass

    def _consume_close_exception(self, task: "asyncio.Task") -> None:
        self._close_tasks.discard(task)
        try:
            task.exception()  # retrieve so it is never reported as unretrieved
        except (asyncio.CancelledError, asyncio.InvalidStateError):
            pass

    async def _run_one_connection(
        self,
        connect: Callable[..., Any],
        on_connect: Callable[[Any], Any],
        on_text: Callable[[Any, str], Any],
    ) -> None:
        self.connection_id = None
        kwargs = {} if self._connect is not None else {"max_queue": self.max_queue_messages}
        if self._connect is None:
            proxy_url = proxy_for_url(self.ws_url)
            self.journal.emit("proxy_state", state=probe_proxy(proxy_url),
                              proxy_configured=bool(proxy_url))
        async with connect(self.ws_url, **kwargs) as ws:
            self._active_ws = ws
            self._connection_sequence += 1
            self.connection_id = (
                f"{self.journal.session_id}-conn-{self._connection_sequence}"
            )
            self.stats.reconnects += 1
            self._last_message_ms = self._clock()
            self.journal.emit("connected", url=self.ws_url,
                              connection_id=self.connection_id)
            await on_connect(ws)
            watchdog = asyncio.create_task(self._watch_stale(ws))
            try:
                async for text in ws:
                    self._last_message_ms = self._clock()
                    self.stats.messages += 1
                    await on_text(ws, text)
            finally:
                watchdog.cancel()
                try:
                    await watchdog
                except asyncio.CancelledError:
                    pass
                self._active_ws = None
            self.journal.emit("disconnected")

    async def _watch_stale(self, ws: Any) -> None:
        while True:
            await asyncio.sleep(max(self.stale_after_ms / 2000.0, 0.05))
            now_ms = self._clock()
            idle_ms = now_ms - self._last_message_ms
            if is_receive_stale(self._last_message_ms, now_ms,
                                self.stale_after_ms):
                self.stats.stale_reconnects += 1
                self.journal.emit("stale_feed_detected", idle_ms=idle_ms)
                await ws.close()
                return
