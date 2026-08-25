"""Polymarket order book recorder tests: message parsing, state tracking,
market discovery, and reconnect/resubscribe behavior with a fake socket."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from std0_quant.collectors.polymarket_book import (
    BookState,
    BookRecorder,
    MarketInfo,
    build_subscribe_message,
    find_active_market,
    handle_raw_text,
    _tokens_from_gamma_market,
)
from std0_quant.collectors.ws_runner import (
    SessionJournal,
    compute_backoff_seconds,
)

TOKEN_UP = "71321"
TOKEN_DOWN = "71322"
COND = "0xcondA"

BOOK_MSG = {
    "event_type": "book", "asset_id": TOKEN_UP, "market": COND,
    "buy_levels": [{"price": "0.40", "size": "100"}, {"price": "0.35", "size": "50"}],
    "sell_levels": [{"price": "0.60", "size": "80"}, {"price": "0.65", "size": "20"}],
    "timestamp": "1699248447942",
}

def test_gamma_json_encoded_outcomes_and_token_ids() -> None:
    market = {
        "outcomes": '["Up", "Down"]',
        "clobTokenIds": '["token-up", "token-down"]',
    }
    assert _tokens_from_gamma_market(market) == [
        ("token-up", "Up"), ("token-down", "Down")
    ]


def make_state() -> BookState:
    return BookState(COND, [(TOKEN_UP, "Up"), (TOKEN_DOWN, "Down")])


class TestBookState:
    def test_book_snapshot_derives_best_levels(self) -> None:
        state = make_state()
        row = state.apply(BOOK_MSG, receive_ts_ms=1000)
        assert row is not None
        assert row["best_bid"] == 0.40
        assert row["best_ask"] == 0.60
        assert row["mid"] == pytest.approx(0.50)
        assert row["spread"] == pytest.approx(0.20)
        assert row["outcome"] == "Up"
        assert row["condition_id"] == COND
        assert row["exchange_timestamp_ms"] == 1699248447942
        assert row["receive_timestamp_ms"] == 1000
        assert row["event_type"] == "book"
        assert row["raw_message"] == BOOK_MSG

    def test_book_snapshot_supports_bids_asks_schema_variant(self) -> None:
        state = make_state()
        variant = {
            "event_type": "book", "asset_id": TOKEN_DOWN, "market": COND,
            "bids": [{"price": "0.30", "size": "10"}],
            "asks": [{"price": "0.31", "size": "11"}],
        }
        row = state.apply(variant, receive_ts_ms=1001)
        assert row["best_bid"] == 0.30
        assert row["best_ask"] == 0.31
        assert row["outcome"] == "Down"

    def test_price_change_updates_and_removes_levels(self) -> None:
        state = make_state()
        state.apply(BOOK_MSG, 1000)
        update = {
            "event_type": "price_change", "asset_id": TOKEN_UP, "market": COND,
            "changes": [
                {"price": "0.45", "side": "BUY", "size": "25"},   # new best bid
                {"price": "0.40", "side": "BUY", "size": "0"},    # remove level
                {"price": "0.60", "side": "SELL", "size": "60"},  # resize ask
            ],
            "timestamp": "1699248448100",
        }
        row = state.apply(update, 1002)
        assert row["best_bid"] == 0.45
        assert row["best_ask"] == 0.60
        sizes = {lvl["price"]: lvl["size"] for lvl in row["asks"]}
        assert sizes[0.60] == 60.0
        assert 0.40 not in {lvl["price"] for lvl in row["bids"]}

    def test_last_trade_price_updates(self) -> None:
        state = make_state()
        msg = {
            "event_type": "last_trade_price", "asset_id": TOKEN_UP,
            "market": COND, "price": "0.5", "side": "BUY", "size": "100",
            "timestamp": "2024-01-01T00:00:00Z",
        }
        row = state.apply(msg, 1003)
        assert row["last_trade_price"] == 0.5
        assert row["last_trade_size"] == 100.0
        assert row["last_trade_side"] == "BUY"
        assert row["exchange_timestamp_ms"] is not None  # ISO parsed

    def test_empty_book_gives_null_bid_ask(self) -> None:
        state = make_state()
        msg = {
            "event_type": "book", "asset_id": TOKEN_UP, "market": COND,
            "buy_levels": [], "sell_levels": [],
        }
        row = state.apply(msg, 1004)
        assert row["best_bid"] is None
        assert row["best_ask"] is None
        assert row["mid"] is None
        assert row["spread"] is None

    def test_unknown_token_still_produces_raw_only_row(self) -> None:
        state = make_state()
        msg = {"event_type": "book", "asset_id": "unknown-token",
               "market": COND, "buy_levels": [{"price": "0.1", "size": "1"}]}
        row = state.apply(msg, 1005)
        assert row is not None
        assert row["token_id"] == "unknown-token"
        assert row["best_bid"] is None  # not tracked -> no derived state


class TestHandleRawText:
    def test_event_arrays_produce_multiple_rows(self) -> None:
        state = make_state()
        text = json.dumps([BOOK_MSG, dict(BOOK_MSG, asset_id=TOKEN_DOWN)])
        rows = handle_raw_text(state, text, receive_ts_ms=2000)
        assert len(rows) == 2
        assert {r["token_id"] for r in rows} == {TOKEN_UP, TOKEN_DOWN}

    def test_live_price_changes_batch_expands_per_token(self) -> None:
        state = make_state()
        handle_raw_text(state, json.dumps([
            BOOK_MSG, dict(BOOK_MSG, asset_id=TOKEN_DOWN)
        ]), 1000)
        raw = {
            "event_type": "price_change", "market": COND, "timestamp": "2000",
            "price_changes": [
                {"asset_id": TOKEN_UP, "price": "0.40", "size": "2", "side": "BUY"},
                {"asset_id": TOKEN_DOWN, "price": "0.60", "size": "3", "side": "SELL"},
            ],
        }
        rows = handle_raw_text(state, json.dumps(raw), 2000)
        assert len(rows) == 2
        assert {r["token_id"] for r in rows} == {TOKEN_UP, TOKEN_DOWN}
        assert rows[0]["raw_message"] == raw and rows[1]["raw_message"] is None
        assert rows[0]["raw_message_ref"] == rows[1]["raw_message_ref"]
        assert rows[1]["applied_change"]["asset_id"] == TOKEN_DOWN

    def test_ping_frames_produce_no_rows(self) -> None:
        assert handle_raw_text(make_state(), "PING", 1) == []
        assert handle_raw_text(make_state(), "PONG", 1) == []

    def test_non_json_text_produces_no_rows(self) -> None:
        assert handle_raw_text(make_state(), "not json <", 1) == []


class TestSubscribeMessage:
    def test_subscribe_payload_shape(self) -> None:
        msg = json.loads(build_subscribe_message([TOKEN_UP, TOKEN_DOWN]))
        assert msg == {"assets_ids": [TOKEN_UP, TOKEN_DOWN], "type": "market"}


class TestBackoff:
    def test_growth_and_cap(self) -> None:
        assert compute_backoff_seconds(1, 1.0, 60.0) == 1.0
        assert compute_backoff_seconds(2, 1.0, 60.0) == 2.0
        assert compute_backoff_seconds(3, 1.0, 60.0) == 4.0
        assert compute_backoff_seconds(10, 1.0, 60.0) == 60.0  # capped


def _gamma_market(slug: str, start_ms: int, end_ms: int, cond: str) -> dict:
    return {
        "conditionId": cond, "slug": slug,
        "startDate": start_ms, "endDate": end_ms,  # numeric epoch ms
        "tokens": [
            {"token_id": "tok-up", "outcome": "Up"},
            {"token_id": "tok-dn", "outcome": "Down"},
        ],
    }


def _slug_fetch(markets_by_slug: dict[str, dict], calls: list | None = None):
    """Fake gamma: serves /markets?slug=<slug> from a slug-keyed dict."""
    def fetch(url, params):
        if calls is not None:
            calls.append(params.get("slug"))
        market = markets_by_slug.get(params.get("slug"))
        if market is None:
            return 200, "[]"
        return 200, json.dumps([market])
    return fetch


class TestMarketDiscovery:
    # now_s = 1_700_000_000 -> window-aligned current start 1_699_999_800.
    NOW = 1_700_000_000_000
    CURRENT_SLUG = "btc-updown-5m-1699999800"
    NEXT_SLUG = "btc-updown-5m-1700000100"
    PREV_SLUG = "btc-updown-5m-1699999500"

    def test_window_comes_from_slug_not_gamma_dates(self) -> None:
        # gamma startDate is the CREATION time (earlier); window must come
        # from the slug: [1_699_999_800, 1_700_000_100) seconds.
        market = _gamma_market(self.CURRENT_SLUG,
                                self.NOW - 600_000, self.NOW + 120_000,
                                "0xcurrent")
        calls: list = []
        found = find_active_market(
            _slug_fetch({self.CURRENT_SLUG: market}, calls),
            "http://gamma.local", self.NOW, "btc-updown-5m-",
        )
        assert found is not None
        assert found.condition_id == "0xcurrent"
        assert found.market_start_ms == 1_699_999_800_000
        assert found.market_end_ms == 1_700_000_100_000
        assert [(t, o) for t, o in found.tokens] == [
            ("tok-up", "Up"), ("tok-dn", "Down"),
        ]
        assert calls[0] == self.CURRENT_SLUG  # current window tried first

    def test_falls_back_to_next_market_when_current_missing(self) -> None:
        market = _gamma_market(self.NEXT_SLUG,
                               self.NOW, self.NOW + 600_000, "0xnext")
        found = find_active_market(
            _slug_fetch({self.NEXT_SLUG: market}),
            "http://g", self.NOW, "btc-updown-5m-",
        )
        assert found is not None
        assert found.condition_id == "0xnext"
        assert found.market_start_ms == 1_700_000_100_000

    def test_ended_previous_window_is_never_returned(self) -> None:
        market = _gamma_market(self.PREV_SLUG,
                               self.NOW - 900_000, self.NOW, "0xprev")
        found = find_active_market(
            _slug_fetch({self.PREV_SLUG: market}),
            "http://g", self.NOW, "btc-updown-5m-",
        )
        assert found is None  # previous window ended at NOW

    def test_no_active_market_returns_none(self) -> None:
        assert find_active_market(_slug_fetch({}), "http://g", 1000,
                                  "btc-updown-5m-") is None

    def test_http_failure_returns_none(self) -> None:
        def fetch(url, params):
            return 500, "boom"
        assert find_active_market(fetch, "http://g", 1000,
                                  "btc-updown-5m-") is None

    def test_market_without_two_tokens_is_skipped(self) -> None:
        market = _gamma_market(self.CURRENT_SLUG, self.NOW - 600_000,
                               self.NOW + 120_000, "0xcurrent")
        market["tokens"] = [{"token_id": "tok-up", "outcome": "Up"}]
        found = find_active_market(
            _slug_fetch({self.CURRENT_SLUG: market}),
            "http://g", self.NOW, "btc-updown-5m-",
        )
        assert found is None


class FakeWs:
    """Minimal async websocket double: yields scripted frames, records sends."""

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
        item = self.frames.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class TestBookRecorderReconnect:
    def _market(self) -> MarketInfo:
        return MarketInfo(
            condition_id=COND, slug="bitcoin-up-or-down-test",
            market_start_ms=0, market_end_ms=1,
            tokens=[(TOKEN_UP, "Up"), (TOKEN_DOWN, "Down")],
        )

    def test_reconnects_resubscribes_and_records(self, tmp_path: Path) -> None:
        socket1 = FakeWs([
            "PING",
            json.dumps(BOOK_MSG),
            json.dumps({
                "event_type": "price_change", "asset_id": TOKEN_UP,
                "market": COND,
                "changes": [{"price": "0.42", "side": "BUY", "size": "10"}],
            }),
        ])
        socket2 = FakeWs([
            json.dumps(dict(BOOK_MSG, asset_id=TOKEN_DOWN)),
        ])
        sockets = [socket1, socket2]

        def connect(url):
            assert url == "ws://fake"
            return sockets.pop(0)

        with SessionJournal(tmp_path / "sessions", "sess-1", "polymarket_book") as journal:
            recorder = BookRecorder(
                "ws://fake", self._market(), tmp_path / "raw", journal,
                stale_after_seconds=60, backoff_base_seconds=0.01,
                backoff_max_seconds=0.01, connect=connect,
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

            # both connections re-sent the subscription
            for socket in (socket1, socket2):
                assert json.loads(socket.sent[0]) == {
                    "assets_ids": [TOKEN_UP, TOKEN_DOWN], "type": "market",
                }
            # PING answered with PONG on the app level
            assert "PONG" in socket1.sent
            # raw file holds derived rows from both connections
            from std0_quant.storage import read_ndjson
            rows = list(read_ndjson(recorder.raw_path))
            tokens = {r["token_id"] for r in rows}
            assert tokens == {TOKEN_UP, TOKEN_DOWN}
            assert any(r["event_type"] == "price_change" for r in rows)
            # journal captured the lifecycle, including the reconnect
            events = list(read_ndjson(journal.path))
            kinds = [e["event"] for e in events]
            assert kinds.count("connected") == 2
            assert kinds.count("subscribed") == 2
            assert "disconnected" in kinds
            assert "session_end" in kinds
