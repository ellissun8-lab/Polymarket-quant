"""Episode builder tests: spec Test B (same-second fills coexist) and
Test D (VWAP), plus 3-second rule boundaries and determinism."""

from __future__ import annotations

from conftest import make_trade
from std0_quant.events.episode_builder import build_episodes
from std0_quant.events.fills import Fill, fill_from_envelope
from std0_quant.storage import envelope


def fill(tx: str, ts_ms: int, *, outcome: str = "Up", size: float = 100.0,
         price: float = 0.5, condition_id: str = "0xcA", side: str = "BUY",
         outcome_index: int = 0) -> object:
    """Build a fill the way the data API reports it: SECOND-granularity
    timestamps (ts_ms is quantized down to whole seconds)."""
    record = make_trade(
        tx, ts_ms // 1000, side=side, outcome=outcome, outcome_index=outcome_index,
        size=size, price=price, condition_id=condition_id,
    )
    return fill_from_envelope(envelope("test", record, "run-1"))


def fill_exact_ms(tx: str, ts_ms: int, *, outcome: str = "Up", size: float = 100.0,
                  price: float = 0.5, condition_id: str = "0xcA") -> Fill:
    """Build a fill carrying an exact millisecond timestamp (used to test the
    episode-builder logic itself; real API data is second-granular)."""
    return Fill(
        fill_id=f"ck:{tx}", proxy_wallet=None, side="BUY", asset=None,
        condition_id=condition_id, size=size, price=price, timestamp_ms=ts_ms,
        timestamp_raw=None, title=None, slug=None, outcome=outcome,
        outcome_index=0, transaction_hash=tx, source="test",
        fetched_at_ms=0, raw_json={},
    )


def test_same_second_multiple_fills_coexist_in_one_episode() -> None:
    """Spec Test B: same market/outcome/timestamp fills must not be dropped
    or overwritten -- they all join the same episode."""
    same_second_ms = 1700000100 * 1000
    fills = [
        fill("0xtx1", same_second_ms, size=10, price=0.40),
        fill("0xtx2", same_second_ms, size=20, price=0.50),
        fill("0xtx3", same_second_ms, size=30, price=0.60),
    ]
    result = build_episodes(fills)
    assert len(result.invalid_buy_fills) == 0
    assert len(result.episodes) == 1
    episode = result.episodes[0]
    assert episode.fill_count == 3
    assert episode.total_shares == 60.0
    assert episode.episode_start_ms == same_second_ms
    assert episode.episode_end_ms == same_second_ms
    assert set(episode.constituent_fill_ids) == {f.fill_id for f in fills}


def test_vwap_formula() -> None:
    """Spec Test D: VWAP == sum(price_i * size_i) / sum(size_i)."""
    fills = [
        fill("0xtx1", 1700000100 * 1000, size=100, price=0.60),
        fill("0xtx2", 1700000101 * 1000, size=300, price=0.40),
        fill("0xtx3", 1700000102 * 1000, size=200, price=0.50),
    ]
    result = build_episodes(fills)
    episode = result.episodes[0]
    expected_dollars = 0.60 * 100 + 0.40 * 300 + 0.50 * 200
    expected_shares = 100 + 300 + 200
    assert episode.total_dollars == expected_dollars
    assert episode.total_shares == expected_shares
    assert episode.vwap == expected_dollars / expected_shares


def test_three_second_window_boundaries() -> None:
    """Gap == 3s merges (inclusive window); gap == 3s + 1ms splits.

    Uses exact-millisecond fills: real API timestamps are second-granular,
    so sub-second gaps never occur in production data, but the builder's
    window logic must still be exact.
    """
    base = 1_700_000_100_000
    result = build_episodes([
        fill_exact_ms("0xtx1", base, size=10),
        fill_exact_ms("0xtx2", base + 3000, size=20),  # exactly 3s -> merge
    ])
    assert len(result.episodes) == 1
    assert result.episodes[0].fill_count == 2
    assert result.episodes[0].episode_end_ms == base + 3000

    result = build_episodes([
        fill_exact_ms("0xtx1", base, size=10),
        fill_exact_ms("0xtx2", base + 3001, size=20),  # 3s + 1ms -> split
    ])
    assert len(result.episodes) == 2
    assert result.episodes[0].fill_count == 1
    assert result.episodes[1].fill_count == 1


def test_chaining_bridges_but_long_gap_splits() -> None:
    base = 1700000100 * 1000
    fills = [
        fill("0xtx1", base, size=10),
        fill("0xtx2", base + 2500, size=10),
        fill("0xtx3", base + 5000, size=10),  # 2.5s from previous: same episode
        fill("0xtx4", base + 9000, size=10),  # 4s from previous: new episode
    ]
    result = build_episodes(fills)
    assert len(result.episodes) == 2
    assert result.episodes[0].fill_count == 3
    assert result.episodes[0].episode_start_ms == base
    assert result.episodes[0].episode_end_ms == base + 5000
    assert result.episodes[1].fill_count == 1


def test_per_direction_chaining_with_interleaved_opposite() -> None:
    """An interleaved opposite-direction BUY does not break a same-direction
    chain (documented interpretation of the frozen rule)."""
    base = 1700000100 * 1000
    fills = [
        fill("0xup1", base, outcome="Up", outcome_index=0),
        fill("0xdn1", base + 1000, outcome="Down", outcome_index=1),
        fill("0xup2", base + 2000, outcome="Up", outcome_index=0),
    ]
    result = build_episodes(fills)
    assert len(result.episodes) == 2
    up = [e for e in result.episodes if e.direction == "Up"][0]
    down = [e for e in result.episodes if e.direction == "Down"][0]
    assert up.fill_count == 2
    assert up.episode_start_ms == base
    assert up.episode_end_ms == base + 2000
    assert down.fill_count == 1


def test_sell_fills_do_not_form_episodes() -> None:
    fills = [
        fill("0xsell1", 1700000100 * 1000, side="SELL"),
        fill("0xbuy1", 1700000110 * 1000, side="BUY"),
    ]
    result = build_episodes(fills)
    assert len(result.episodes) == 1
    assert result.episodes[0].fill_count == 1
    assert result.episodes[0].direction == "Up"


def test_invalid_buy_fills_are_reported_not_dropped() -> None:
    record_bad_ts = make_trade("0xbad1", "not-a-timestamp")
    record_bad_size = make_trade("0xbad2", 1700000100, size="abc")
    record_bad_price = make_trade("0xbad3", 1700000100, price=7.5)
    fills = [
        fill_from_envelope(envelope("test", record_bad_ts, "run-1")),
        fill_from_envelope(envelope("test", record_bad_size, "run-1")),
        fill_from_envelope(envelope("test", record_bad_price, "run-1")),
        fill("0xgood", 1700000100 * 1000),
    ]
    result = build_episodes(fills)
    assert len(result.episodes) == 1
    invalid = {f.fill_id: f.reason for f in result.invalid_buy_fills}
    assert len(invalid) == 3
    reasons = sorted(invalid.values())
    assert reasons.count("TIMESTAMP_INVALID") == 1
    assert reasons.count("FIELD_INCOMPLETE") == 2


def test_empty_input() -> None:
    result = build_episodes([])
    assert result.episodes == []
    assert result.invalid_buy_fills == []


def test_multiple_markets_are_grouped() -> None:
    base = 1700000100 * 1000
    fills = [
        fill("0xa1", base, condition_id="0xcA"),
        fill("0xa2", base + 1000, condition_id="0xcA"),
        fill("0xb1", base + 2000, condition_id="0xcB"),
    ]
    result = build_episodes(fills)
    assert {e.market_id for e in result.episodes} == {"0xcA", "0xcB"}


def test_build_is_deterministic_across_input_permutations() -> None:
    base = 1700000100 * 1000
    fills = [
        fill("0xtx1", base),
        fill("0xtx2", base + 1000),
        fill("0xtx3", base + 2000),
        fill("0xdn1", base + 500, outcome="Down", outcome_index=1),
    ]
    straight = build_episodes(fills)
    shuffled = build_episodes(list(reversed(fills)))
    assert [e.constituent_fill_ids for e in straight.episodes] == \
           [e.constituent_fill_ids for e in shuffled.episodes]


def test_rejects_unfrozen_rule() -> None:
    import pytest

    with pytest.raises(ValueError, match="frozen"):
        build_episodes([], rule="v1_5sec")
