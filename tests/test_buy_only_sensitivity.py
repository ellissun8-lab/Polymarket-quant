"""Tests J - Audit 2: BUY-only label sensitivity (audit-only label)."""

from __future__ import annotations

import pytest

from std0_quant.audit.buy_only_sensitivity import (
    BuyOnlySensitivityResult,
    MarketFillWindows,
    compute_directional_sensitivity,
    opposite_outcome,
    run_buy_only_sensitivity,
    same_second_boundary_note,
)

T0 = 1_787_500_000_000
END = T0 + 300_000  # market end well past the window


def mk(initial="Up", buys=None, sells=None, end=END, y30=None, t0=T0):
    return MarketFillWindows(
        t0_ms=t0,
        initial_direction=initial,
        buy_ts_by_outcome=buys or {},
        sell_ts_by_outcome=sells or {},
        market_end_ms=end,
        y30=y30,
    )


class TestBoundaries:
    def test_buy_opposite_at_exact_t0_excluded(self) -> None:
        # (t0, ...] : an event AT t0 is not inside the window
        o = compute_directional_sensitivity(
            "m1", mk(buys={"Down": [T0]}, y30=0)
        )
        assert o.y30_directional_sensitivity == 0

    def test_buy_opposite_at_t0_plus_30s_included(self) -> None:
        o = compute_directional_sensitivity(
            "m1", mk(buys={"Down": [T0 + 30_000]}, y30=1)
        )
        assert o.y30_directional_sensitivity == 1

    def test_buy_opposite_at_t0_plus_30s_1ms_excluded(self) -> None:
        o = compute_directional_sensitivity(
            "m1", mk(buys={"Down": [T0 + 30_001]}, y30=0)
        )
        assert o.y30_directional_sensitivity == 0

    def test_sell_initial_at_exact_t0_excluded(self) -> None:
        o = compute_directional_sensitivity("m1", mk(sells={"Up": [T0]}))
        assert o.y30_directional_sensitivity == 0

    def test_sell_initial_at_t0_plus_30s_included(self) -> None:
        o = compute_directional_sensitivity(
            "m1", mk(sells={"Up": [T0 + 30_000]})
        )
        assert o.y30_directional_sensitivity == 1

    def test_sell_initial_at_t0_plus_30s_1ms_excluded(self) -> None:
        o = compute_directional_sensitivity(
            "m1", mk(sells={"Up": [T0 + 30_001]})
        )
        assert o.y30_directional_sensitivity == 0

    def test_unsorted_timestamps_handled(self) -> None:
        o = compute_directional_sensitivity(
            "m1", mk(buys={"Down": [T0 + 30_000, T0 + 5_000]}, y30=1)
        )
        assert o.buy_opposite_event_ms == T0 + 5_000


class TestDirectionality:
    def test_initial_down_direction(self) -> None:
        # initial Down -> BUY Up OR SELL Down
        o = compute_directional_sensitivity(
            "m1", mk(initial="Down", sells={"Down": [T0 + 10_000]})
        )
        assert o.y30_directional_sensitivity == 1
        assert o.opposite_direction == "Up"

    def test_sell_opposite_direction_does_not_count(self) -> None:
        # selling the opposite token reduces opposite exposure: not a
        # directional re-entry under this sensitivity label
        o = compute_directional_sensitivity(
            "m1", mk(sells={"Down": [T0 + 10_000]})
        )
        assert o.y30_directional_sensitivity == 0

    def test_buy_initial_direction_does_not_count(self) -> None:
        o = compute_directional_sensitivity(
            "m1", mk(buys={"Up": [T0 + 10_000]})
        )
        assert o.y30_directional_sensitivity == 0

    def test_fills_before_t0_ignored(self) -> None:
        o = compute_directional_sensitivity(
            "m1",
            mk(
                buys={"Down": [T0 - 100_000]},
                sells={"Up": [T0 - 50_000]},
            ),
        )
        assert o.y30_directional_sensitivity == 0

    def test_opposite_outcome_mapping(self) -> None:
        assert opposite_outcome("Up") == "Down"
        assert opposite_outcome("Down") == "Up"
        with pytest.raises(ValueError):
            opposite_outcome("YES")

    def test_extra_outcome_keys_ignored(self) -> None:
        o = compute_directional_sensitivity(
            "m1", mk(buys={"SomeOther": [T0 + 10_000]})
        )
        assert o.y30_directional_sensitivity == 0


class TestEligibilityAndConsistency:
    def test_censored_market_not_in_rates(self) -> None:
        # market ends before t0+30s -> not horizon-eligible
        o = compute_directional_sensitivity(
            "m1", mk(end=T0 + 10_000, buys={"Down": [T0 + 5_000]}, y30=1)
        )
        assert o.horizon_eligible is False
        result = run_buy_only_sensitivity({"m1": mk(end=T0 + 10_000,
                                                    buys={"Down": [T0 + 5_000]},
                                                    y30=1)})
        assert result.n_markets == 1
        assert result.n_eligible == 0
        assert result.original_rate is None
        assert result.status == "NOT_COMPUTABLE"

    def test_y30_consistency_error_detected(self) -> None:
        # caller claims y30=1 but no opposite BUY exists in the window
        o = compute_directional_sensitivity(
            "m1", mk(buys={"Down": []}, y30=1)
        )
        assert o.consistency_error is not None
        result = run_buy_only_sensitivity({"m1": mk(y30=1)})
        assert result.n_consistency_errors == 1

    def test_y30_consistency_ok(self) -> None:
        o = compute_directional_sensitivity(
            "m1", mk(buys={"Down": [T0 + 5_000]}, y30=1)
        )
        assert o.consistency_error is None


class TestAggregation:
    def test_sell_only_upgrade_counted(self) -> None:
        markets = {
            # y30=0 but SELL of initial inside window -> sensitivity=1
            "a": mk(sells={"Up": [T0 + 10_000]}, y30=0),
            # classic BUY-only positive
            "b": mk(buys={"Down": [T0 + 10_000]}, y30=1),
            # both event types present
            "c": mk(buys={"Down": [T0 + 10_000]},
                    sells={"Up": [T0 + 20_000]}, y30=1),
            # nothing in window
            "d": mk(y30=0),
        }
        result = run_buy_only_sensitivity(markets)
        assert result.n_markets == 4
        assert result.n_eligible == 4
        assert result.original_positive == 2
        assert result.sensitivity_positive == 3
        assert result.original_rate == 0.5
        assert result.sensitivity_rate == 0.75
        assert result.delta_pp == pytest.approx(25.0)
        assert result.status == "WARN"
        assert result.n_sell_only_upgrades == 1
        assert result.n_both_event_types == 1
        assert result.agreement_rate == pytest.approx(0.75)

    def test_pass_when_delta_below_threshold(self) -> None:
        markets = {f"m{i}": mk(y30=0) for i in range(100)}
        markets["up1"] = mk(buys={"Down": [T0 + 5_000]}, y30=1)
        markets["sell1"] = mk(sells={"Up": [T0 + 5_000]}, y30=0)
        result = run_buy_only_sensitivity(markets)
        # 2/102 vs 1/102 -> delta ~0.98pp < 1.0
        assert result.delta_pp < 1.0
        assert result.status == "PASS"

    def test_empty_input(self) -> None:
        result = run_buy_only_sensitivity({})
        assert result.n_markets == 0
        assert result.status == "NOT_COMPUTABLE"

    def test_sell_only_share(self) -> None:
        markets = {
            "a": mk(sells={"Up": [T0 + 10_000]}, y30=0),
            "b": mk(y30=0),
        }
        result = run_buy_only_sensitivity(markets)
        assert result.sell_only_share_of_eligible == pytest.approx(0.5)


class TestSameSecondNote:
    def test_same_second_flagged(self) -> None:
        # t0 is on a second boundary; T0+999 shares its second
        note = same_second_boundary_note(T0, T0 + 999)
        assert note is not None and "not recoverable" in note

    def test_later_second_not_flagged(self) -> None:
        assert same_second_boundary_note(T0, T0 + 1_000) is None
