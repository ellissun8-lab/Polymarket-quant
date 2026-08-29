import pytest

from std0_quant.execution.cost_pnl import FeeSchedule
from std0_quant.execution.execution_timestamps import (
    CancelTimestamps,
    OrderTimestamps,
)
from std0_quant.execution.order_state import OrderStatus
from std0_quant.execution.passive_risk_pipeline import (
    execute_passive_with_risk,
)
from std0_quant.execution.portfolio import PortfolioState
from std0_quant.execution.risk import (
    RiskContext,
    RiskLimits,
    RiskOrderIntent,
)
from std0_quant.execution.simulator import ConfirmedTradeEvent


def limits():
    return RiskLimits(
        max_order_notional=20,
        max_market_exposure=30,
        max_gross_exposure=50,
        max_daily_loss=5,
        max_market_data_age_ms=1000,
    )


def context(**kwargs):
    values = {
        "now_ts_ms": 10_000,
        "market_data_ts_ms": 9_500,
        "kill_switch": False,
    }
    values.update(kwargs)
    return RiskContext(**values)


def order_times():
    return OrderTimestamps(
        order_send_ts_ms=10_001,
        order_venue_arrival_ts_ms=10_002,
        order_venue_accept_ts_ms=10_003,
        order_ack_receive_ts_ms=10_005,
    )


def cancel_times():
    return CancelTimestamps(
        cancel_send_ts_ms=10_006,
        cancel_venue_arrival_ts_ms=10_007,
        cancel_effective_ts_ms=10_008,
        cancel_ack_receive_ts_ms=10_010,
    )


def buy_intent(qty=10):
    return RiskOrderIntent(
        condition_id="m1",
        outcome="Up",
        side="BUY",
        qty=qty,
        limit_price=0.50,
    )


def sell_intent(qty=4):
    return RiskOrderIntent(
        condition_id="m1",
        outcome="Up",
        side="SELL",
        qty=qty,
        limit_price=0.60,
    )


def test_rejected_passive_order_does_not_mutate_portfolio():
    p = PortfolioState(cash=100)

    result = execute_passive_with_risk(
        portfolio=p,
        intent=buy_intent(),
        displayed_qty_at_accept=0,
        order_timestamps=order_times(),
        trades=[],
        reference_price=0.50,
        mark_price=0.60,
        limits=limits(),
        context=context(kill_switch=True),
    )

    assert not result.risk.allowed
    assert result.execution is None
    assert p.cash == pytest.approx(100)
    assert p.reserved_cash == pytest.approx(0)
    assert p.positions == {}


def test_resting_buy_keeps_cash_reserved():
    p = PortfolioState(cash=100)

    result = execute_passive_with_risk(
        portfolio=p,
        intent=buy_intent(),
        displayed_qty_at_accept=10,
        order_timestamps=order_times(),
        trades=[],
        reference_price=0.50,
        mark_price=0.60,
        limits=limits(),
        context=context(),
    )

    assert result.execution.simulation.final_status == OrderStatus.ACKNOWLEDGED
    assert result.reservation_active
    assert result.reserved_cash_remaining == pytest.approx(5)
    assert p.reserved_cash == pytest.approx(5)
    assert p.available_cash == pytest.approx(95)


def test_partial_buy_fill_keeps_only_unused_reservation():
    p = PortfolioState(cash=100)

    result = execute_passive_with_risk(
        portfolio=p,
        intent=buy_intent(),
        displayed_qty_at_accept=0,
        order_timestamps=order_times(),
        trades=[
            ConfirmedTradeEvent(
                venue_ts_ms=10_005,
                traded_qty=4,
            )
        ],
        reference_price=0.50,
        mark_price=0.60,
        limits=limits(),
        context=context(),
    )

    assert result.execution.simulation.final_status == OrderStatus.PARTIALLY_FILLED
    assert result.reservation_active
    assert p.position("m1", "Up").qty == pytest.approx(4)
    assert p.cash == pytest.approx(98)
    assert p.reserved_cash == pytest.approx(3)


def test_partial_buy_then_cancel_releases_remainder():
    p = PortfolioState(cash=100)

    result = execute_passive_with_risk(
        portfolio=p,
        intent=buy_intent(),
        displayed_qty_at_accept=0,
        order_timestamps=order_times(),
        trades=[
            ConfirmedTradeEvent(
                venue_ts_ms=10_007,
                traded_qty=4,
            )
        ],
        reference_price=0.50,
        mark_price=0.60,
        limits=limits(),
        context=context(),
        cancel_timestamps=cancel_times(),
    )

    assert result.execution.simulation.final_status == OrderStatus.CANCELLED
    assert not result.reservation_active
    assert p.position("m1", "Up").qty == pytest.approx(4)
    assert p.cash == pytest.approx(98)
    assert p.reserved_cash == pytest.approx(0)


def test_full_buy_fill_releases_fee_buffer_remainder():
    p = PortfolioState(cash=5.005)

    result = execute_passive_with_risk(
        portfolio=p,
        intent=buy_intent(),
        displayed_qty_at_accept=0,
        order_timestamps=order_times(),
        trades=[
            ConfirmedTradeEvent(
                venue_ts_ms=10_005,
                traded_qty=10,
            )
        ],
        reference_price=0.50,
        mark_price=0.60,
        limits=limits(),
        context=context(),
        fee_schedule=FeeSchedule(
            maker_fee_bps=10,
            maker_rebate_bps=4,
        ),
    )

    assert result.risk.allowed
    assert result.execution.simulation.final_status == OrderStatus.FILLED
    assert p.reserved_cash == pytest.approx(0)
    assert p.cash == pytest.approx(0.002)
    assert p.realized_pnl == pytest.approx(-0.003)


def test_maker_fee_buffer_can_reject_before_resting():
    p = PortfolioState(cash=5)

    result = execute_passive_with_risk(
        portfolio=p,
        intent=buy_intent(),
        displayed_qty_at_accept=0,
        order_timestamps=order_times(),
        trades=[],
        reference_price=0.50,
        mark_price=0.60,
        limits=limits(),
        context=context(),
        fee_schedule=FeeSchedule(
            maker_fee_bps=10,
        ),
    )

    assert not result.risk.allowed
    assert "INSUFFICIENT_AVAILABLE_CASH" in result.risk.reasons
    assert p.reserved_cash == pytest.approx(0)


def test_unrelated_cash_reservation_is_preserved():
    p = PortfolioState(cash=100)
    p.reserve_buy_cash(20)

    result = execute_passive_with_risk(
        portfolio=p,
        intent=buy_intent(),
        displayed_qty_at_accept=0,
        order_timestamps=order_times(),
        trades=[
            ConfirmedTradeEvent(
                venue_ts_ms=10_005,
                traded_qty=10,
            )
        ],
        reference_price=0.50,
        mark_price=0.60,
        limits=limits(),
        context=context(),
    )

    assert result.execution.simulation.final_status == OrderStatus.FILLED
    assert p.reserved_cash == pytest.approx(20)


def portfolio_with_inventory():
    p = PortfolioState(cash=100)
    p.apply_buy_fill(
        condition_id="m1",
        outcome="Up",
        qty=10,
        price=0.50,
    )
    return p


def test_resting_sell_keeps_shares_reserved():
    p = portfolio_with_inventory()

    result = execute_passive_with_risk(
        portfolio=p,
        intent=sell_intent(),
        displayed_qty_at_accept=10,
        order_timestamps=order_times(),
        trades=[],
        reference_price=0.60,
        mark_price=0.50,
        limits=limits(),
        context=context(),
    )

    assert result.risk.allowed
    assert result.execution.simulation.final_status == OrderStatus.ACKNOWLEDGED
    assert result.reservation_active
    assert p.reserved_position_qty("m1", "Up") == pytest.approx(4)
    assert p.available_position_qty("m1", "Up") == pytest.approx(6)
    assert p.position("m1", "Up").qty == pytest.approx(10)


def test_partial_sell_consumes_only_filled_reservation():
    p = portfolio_with_inventory()

    result = execute_passive_with_risk(
        portfolio=p,
        intent=sell_intent(),
        displayed_qty_at_accept=0,
        order_timestamps=order_times(),
        trades=[
            ConfirmedTradeEvent(
                venue_ts_ms=10_005,
                traded_qty=2,
            )
        ],
        reference_price=0.60,
        mark_price=0.50,
        limits=limits(),
        context=context(),
    )

    assert result.execution.simulation.final_status == OrderStatus.PARTIALLY_FILLED
    assert result.reservation_active
    assert p.position("m1", "Up").qty == pytest.approx(8)
    assert p.reserved_position_qty("m1", "Up") == pytest.approx(2)


def test_partial_sell_then_cancel_releases_remaining_shares():
    p = portfolio_with_inventory()

    result = execute_passive_with_risk(
        portfolio=p,
        intent=sell_intent(),
        displayed_qty_at_accept=0,
        order_timestamps=order_times(),
        trades=[
            ConfirmedTradeEvent(
                venue_ts_ms=10_007,
                traded_qty=2,
            )
        ],
        reference_price=0.60,
        mark_price=0.50,
        limits=limits(),
        context=context(),
        cancel_timestamps=cancel_times(),
    )

    assert result.execution.simulation.final_status == OrderStatus.CANCELLED
    assert not result.reservation_active
    assert p.position("m1", "Up").qty == pytest.approx(8)
    assert p.reserved_position_qty("m1", "Up") == pytest.approx(0)


def test_existing_sell_reservation_is_preserved():
    p = portfolio_with_inventory()
    p.reserve_sell_qty("m1", "Up", 2)

    result = execute_passive_with_risk(
        portfolio=p,
        intent=sell_intent(qty=3),
        displayed_qty_at_accept=0,
        order_timestamps=order_times(),
        trades=[
            ConfirmedTradeEvent(
                venue_ts_ms=10_005,
                traded_qty=3,
            )
        ],
        reference_price=0.60,
        mark_price=0.50,
        limits=limits(),
        context=context(),
    )

    assert result.execution.simulation.final_status == OrderStatus.FILLED
    assert p.position("m1", "Up").qty == pytest.approx(7)
    assert p.reserved_position_qty("m1", "Up") == pytest.approx(2)
    assert p.available_position_qty("m1", "Up") == pytest.approx(5)


def test_reserved_inventory_can_reject_second_sell():
    p = portfolio_with_inventory()
    p.reserve_sell_qty("m1", "Up", 8)

    result = execute_passive_with_risk(
        portfolio=p,
        intent=sell_intent(qty=3),
        displayed_qty_at_accept=0,
        order_timestamps=order_times(),
        trades=[],
        reference_price=0.60,
        mark_price=0.50,
        limits=limits(),
        context=context(),
    )

    assert not result.risk.allowed
    assert (
        "INSUFFICIENT_AVAILABLE_POSITION"
        in result.risk.reasons
    )
    assert p.reserved_position_qty("m1", "Up") == pytest.approx(8)
