"""Y30 tests: spec Test E (window boundaries) and Test F (horizon censoring)."""

from __future__ import annotations

import pytest

from std0_quant.events.event_ledger import compute_y30, y30_horizon_eligible

T0 = 1_700_000_100_000
HORIZON_MS = 30_000


class TestY30Boundaries:
    """Spec Test E: strict boundary semantics of (t0, t0 + 30s]."""

    def test_at_t0_is_not_continuation(self) -> None:
        y30, event_ts = compute_y30(T0, [T0])
        assert y30 == 0
        assert event_ts is None

    def test_at_t0_plus_1ms_counts(self) -> None:
        y30, event_ts = compute_y30(T0, [T0 + 1])
        assert y30 == 1
        assert event_ts == T0 + 1

    def test_at_t0_plus_30s_counts(self) -> None:
        y30, event_ts = compute_y30(T0, [T0 + HORIZON_MS])
        assert y30 == 1
        assert event_ts == T0 + HORIZON_MS

    def test_at_t0_plus_30s_plus_1ms_does_not_count(self) -> None:
        y30, event_ts = compute_y30(T0, [T0 + HORIZON_MS + 1])
        assert y30 == 0
        assert event_ts is None

    def test_events_before_t0_are_ignored(self) -> None:
        y30, _ = compute_y30(T0, [T0 - 1, T0 - 60_000])
        assert y30 == 0

    def test_mixed_timestamps_pick_event_inside_window(self) -> None:
        y30, event_ts = compute_y30(T0, [T0 - 5_000, T0 + 2_000, T0 + 90_000])
        assert y30 == 1
        assert event_ts == T0 + 2_000

    def test_empty_timestamps(self) -> None:
        y30, event_ts = compute_y30(T0, [])
        assert y30 == 0
        assert event_ts is None

    def test_uses_episode_end_not_start(self) -> None:
        """The window anchors at episode END: a buy 10s after episode START
        but 2s before episode END is inside the episode, not a continuation."""
        episode_start = T0
        episode_end = T0 + 10_000
        y30, _ = compute_y30(episode_end, [episode_start + 8_000])
        assert y30 == 0  # before t0 == episode_end

    def test_non_frozen_horizon_rejected(self) -> None:
        with pytest.raises(ValueError, match="frozen"):
            compute_y30(T0, [], horizon_seconds=31)
        with pytest.raises(ValueError, match="frozen"):
            y30_horizon_eligible(T0, T0 + 60_000, horizon_seconds=60)


class TestHorizonCensoring:
    """Spec Test F: markets ending before t0 + 30s are censored, not plain 0."""

    def test_full_horizon_inside_market_is_eligible(self) -> None:
        assert y30_horizon_eligible(T0, T0 + HORIZON_MS) is True
        assert y30_horizon_eligible(T0, T0 + HORIZON_MS + 1) is True

    def test_market_ending_before_horizon_is_not_eligible(self) -> None:
        assert y30_horizon_eligible(T0, T0 + HORIZON_MS - 1) is False
        assert y30_horizon_eligible(T0, T0 + 10_000) is False

    def test_missing_market_end_is_not_eligible(self) -> None:
        assert y30_horizon_eligible(T0, None) is False

    def test_censored_market_keeps_y30_value_but_flagged(self) -> None:
        """A censored market with no observed event is y30=0 + eligible=False:
        it must never be treated as a plain negative sample downstream."""
        market_end = T0 + 10_000  # market closes 20s short of the horizon
        y30, _ = compute_y30(T0, [T0 + 5_000])  # event before market end: y30=1
        eligible = y30_horizon_eligible(T0, market_end)
        assert y30 == 1  # observed event is a fact regardless of censoring
        assert eligible is False
