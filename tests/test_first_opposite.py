"""FirstOpposite tests: spec Test C (ambiguous direction) plus initial
direction selection and first-opposite episode detection."""

from __future__ import annotations

from conftest import make_trade
from std0_quant.events.episode_builder import build_episodes
from std0_quant.events.fills import fill_from_envelope
from std0_quant.events.first_opposite import (
    analyze_initial_direction,
    find_first_opposite,
)
from std0_quant.storage import envelope


def buy(tx: str, ts_ms: int, outcome: str, condition_id: str = "0xcA",
        size: float = 100.0, price: float = 0.5, outcome_index: int | None = None):
    idx = outcome_index if outcome_index is not None else (0 if outcome == "Up" else 1)
    record = make_trade(tx, ts_ms // 1000, outcome=outcome, outcome_index=idx,
                        size=size, price=price, condition_id=condition_id)
    return fill_from_envelope(envelope("test", record, "run-1"))


class TestInitialDirection:
    def test_earliest_buy_outcome_wins(self) -> None:
        fills = [
            buy("0xdn1", 1700000100 * 1000 + 5000, "Down"),
            buy("0xup1", 1700000100 * 1000, "Up"),
        ]
        initial = analyze_initial_direction(fills)
        assert initial.direction == "Up"
        assert initial.first_timestamp_ms == 1700000100 * 1000
        assert initial.ambiguous is False

    def test_down_can_be_initial(self) -> None:
        fills = [
            buy("0xdn1", 1700000100 * 1000, "Down"),
            buy("0xup1", 1700000100 * 1000 + 9000, "Up"),
        ]
        initial = analyze_initial_direction(fills)
        assert initial.direction == "Down"

    def test_same_second_up_and_down_is_ambiguous(self) -> None:
        """Spec Test C: Up/Down first appearing at the same timestamp must be
        flagged ambiguous, never randomly ordered."""
        same_second = 1700000100 * 1000
        fills = [
            buy("0xup1", same_second, "Up"),
            buy("0xdn1", same_second, "Down"),
        ]
        initial = analyze_initial_direction(fills)
        assert initial.ambiguous is True
        assert initial.direction is None  # do NOT guess

    def test_no_buy_fills(self) -> None:
        initial = analyze_initial_direction([])
        assert initial.direction is None
        assert initial.ambiguous is False

    def test_single_direction_only(self) -> None:
        fills = [buy("0xup1", 1700000100 * 1000, "Up"),
                 buy("0xup2", 1700000100 * 1000 + 2000, "Up")]
        initial = analyze_initial_direction(fills)
        assert initial.direction == "Up"
        assert initial.ambiguous is False

    def test_three_outcomes_is_not_updown_market(self) -> None:
        fills = [
            buy("0xa", 1700000100 * 1000, "Up"),
            buy("0xb", 1700000100 * 1000 + 1000, "Down"),
            buy("0xc", 1700000100 * 1000 + 2000, "Yes"),
        ]
        initial = analyze_initial_direction(fills)
        assert initial.too_many_outcomes is True
        assert initial.direction is None

    def test_sell_fills_are_ignored(self) -> None:
        record = make_trade("0xsell", 1700000000, side="SELL", outcome="Down")
        sell_fill = fill_from_envelope(envelope("test", record, "run-1"))
        fills = [sell_fill, buy("0xup1", 1700000100 * 1000, "Up")]
        initial = analyze_initial_direction(fills)
        assert initial.direction == "Up"


class TestFindFirstOpposite:
    def test_first_opposite_is_earliest_opposite_episode(self) -> None:
        base = 1700000100 * 1000
        fills = [
            buy("0xup1", base, "Up"),
            buy("0xdn1", base + 10_000, "Down"),   # first opposite episode
            buy("0xup2", base + 11_000, "Up"),     # still Up episode chain? no: 10s gap -> new Up episode
            buy("0xdn2", base + 30_000, "Down"),   # later Down episode
        ]
        episodes = build_episodes(fills).episodes
        initial = analyze_initial_direction(fills)
        first_opp = find_first_opposite(episodes, initial)
        assert first_opp is not None
        assert first_opp.direction == "Down"
        assert first_opp.episode_start_ms == base + 10_000
        assert first_opp.fill_count == 1

    def test_no_opposite_returns_none(self) -> None:
        fills = [buy("0xup1", 1700000100 * 1000, "Up"),
                 buy("0xup2", 1700000100 * 1000 + 1000, "Up")]
        episodes = build_episodes(fills).episodes
        initial = analyze_initial_direction(fills)
        assert find_first_opposite(episodes, initial) is None

    def test_multi_fill_first_opposite_episode(self) -> None:
        base = 1700000100 * 1000
        fills = [
            buy("0xup1", base, "Up", size=50),
            buy("0xdn1", base + 10_000, "Down", size=100, price=0.3),
            buy("0xdn2", base + 10_000 + 2000, "Down", size=50, price=0.4),
        ]
        episodes = build_episodes(fills).episodes
        initial = analyze_initial_direction(fills)
        first_opp = find_first_opposite(episodes, initial)
        assert first_opp is not None
        assert first_opp.fill_count == 2
        assert first_opp.total_shares == 150.0
        assert first_opp.vwap == (100 * 0.3 + 50 * 0.4) / 150.0
        assert first_opp.episode_start_ms == base + 10_000
        assert first_opp.episode_end_ms == base + 12_000
