"""BTC tick recorder tests: message parsing and the recorder loop with a
fake websocket."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from std0_quant.collectors.btc_ticks import (
    TickRecorder,
    handle_binance_text,
    parse_binance_trade,
)
from std0_quant.collectors.ws_runner import SessionJournal
from std0_quant.storage import read_ndjson

TRADE_MSG = {
    "e": "trade", "E": 1700000000123, "s": "BTCUSDT", "t": 12345,
    "p": "60000.15", "q": "0.0012", "T": 1700000000100, "m": True, "M": True,
}


class TestParse:
    def test_trade_message_mapped(self) -> None:
        row = parse_binance_trade(TRADE_MSG, receive_ts_ms=1700000000200,
                                  source="binance:BTCUSDT@trade")
        assert row is not None
        assert row["exchange_timestamp_ms"] == 1700000000100  # T, not E
        assert row["event_timestamp_ms"] == 1700000000123
        assert row["receive_timestamp_ms"] == 1700000000200
        assert row["price"] == 60000.15
        assert row["size"] == 0.0012
        assert row["trade_id"] == 12345
        assert row["buyer_is_maker"] is True
        assert row["source"] == "binance:BTCUSDT@trade"
        assert row["raw_message"] == TRADE_MSG

    def test_non_trade_message_returns_none(self) -> None:
        assert parse_binance_trade({"e": "kline", "p": "1"}, 1, "binance") is None
        assert parse_binance_trade({}, 1, "binance") is None

    def test_malformed_trade_returns_none(self) -> None:
        bad = {"e": "trade", "T": 123}  # missing p/q
        assert parse_binance_trade(bad, 1, "binance") is None

    def test_handle_text_accepts_single_and_array_frames(self) -> None:
        single = handle_binance_text(json.dumps(TRADE_MSG), 5, "binance:x")
        assert len(single) == 1
        array = handle_binance_text(json.dumps([TRADE_MSG, TRADE_MSG]), 5, "binance:x")
        assert len(array) == 2

    def test_handle_text_ignores_garbage(self) -> None:
        assert handle_binance_text("<<<", 5, "binance:x") == []


class FakeWs:
    def __init__(self, frames: list) -> None:
        self.frames = list(frames)
        self.sent: list[str] = []
        self.closed = False

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def close(self) -> None:
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.frames:
            raise StopAsyncIteration
        return self.frames.pop(0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class TestTickRecorder:
    def test_records_ticks_and_reconnects(self, tmp_path: Path) -> None:
        socket1 = FakeWs([json.dumps(TRADE_MSG),
                          json.dumps(dict(TRADE_MSG, t=12346, p="60001.0"))])
        socket2 = FakeWs([json.dumps(dict(TRADE_MSG, t=12347, p="60002.0"))])
        sockets = [socket1, socket2]

        def connect(url):
            assert url == "ws://fake-btc"
            return sockets.pop(0)

        with SessionJournal(tmp_path / "sessions", "btc-1", "btc_ticks") as journal:
            recorder = TickRecorder(
                "ws://fake-btc", "binance:BTCUSDT@trade", tmp_path / "raw",
                journal, stale_after_seconds=60,
                backoff_base_seconds=0.01, backoff_max_seconds=0.01,
                connect=connect,
            )

            async def scenario() -> None:
                task = asyncio.create_task(recorder.run())
                for _ in range(200):
                    if recorder.stats.reconnects >= 2:
                        break
                    await asyncio.sleep(0.005)
                recorder.stop()
                await asyncio.wait_for(task, timeout=5)

            asyncio.run(scenario())

            rows = list(read_ndjson(recorder.raw_path))
            assert len(rows) == 3
            assert [r["trade_id"] for r in rows] == [12345, 12346, 12347]
            assert all(r["source"] == "binance:BTCUSDT@trade" for r in rows)
            # every row keeps both timestamps (no forward/back-filling)
            assert all(r["receive_timestamp_ms"] >= r["exchange_timestamp_ms"]
                       for r in rows)

            events = [e["event"] for e in read_ndjson(journal.path)]
            assert events.count("connected") == 2
            assert "session_end" in events
