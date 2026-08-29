import pytest

from std0_quant.execution.cost_pnl import FeeSchedule
from std0_quant.execution.execution_timestamps import OrderTimestamps
from std0_quant.execution.order_state import OrderStatus
from std0_quant.execution.portfolio import PortfolioState
from std0_quant.execution.risk import (
    RiskContext,
    RiskLimits,
    RiskOrderIntent,
)
from std0_quant.execution.risk_execution_pipeline import (
    execute_aggressive_with_risk,
)


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


def test_rejected_order_does_not_mutate_portfolio():
    p = PortfolioState(cash=100)

    result = execute_aggressive_with_risk(
        portfolio=p,
        intent=RiskOrderIntent(
            condition_id="m1",
            outcome="Up",
            side="BUY",
            qty=10,
            limit_price=0.50,
        ),
        tif="IOC",
        levels=[(0.50, 10)],
        order_timestamps=order_times(),
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


def test_allowed_buy_reserves_executes_and_releases_cash():
    p = PortfolioState(cash=100)

    result = execute_aggressive_with_risk(
        portfolio=p,
        intent=RiskOrderIntent(
            condition_id="m1",
            outcome="Up",
            side="BUY",
            qty=10,
            limit_price=0.50,
        ),
        tif="IOC",
        levels=[(0.50, 10)],
        order_timestamps=order_times(),
        reference_price=0.50,
        mark_price=0.60,
        limits=limits(),
        context=context(),
    )

    assert result.risk.allowed
    assert result.execution is not None
    assert result.execution.simulation.final_status == OrderStatus.FILLED

    assert p.position("m1", "Up").qty == pytest.approx(10)
    assert p.cash == pytest.approx(95)
    assert p.reserved_cash == pytest.approx(0)


def test_partial_ioc_releases_unused_reservation():
    p = PortfolioState(cash=100)

    result = execute_aggressive_with_risk(
        portfolio=p,
        intent=RiskOrderIntent(
            condition_id="m1",
            outcome="Up",
            side="BUY",
            qty=10,
            limit_price=0.50,
        ),
        tif="IOC",
        levels=[(0.50, 4)],
        order_timestamps=order_times(),
        reference_price=0.50,
        mark_price=0.60,
        limits=limits(),
        context=context(),
    )

    assert result.execution.simulation.final_status == OrderStatus.EXPIRED
    assert p.position("m1", "Up").qty == pytest.approx(4)
    assert p.cash == pytest.approx(98)
    assert p.reserved_cash == pytest.approx(0)


def test_fok_failure_releases_all_reservation_and_has_no_fill():
    p = PortfolioState(cash=100)

    result = execute_aggressive_with_risk(
        portfolio=p,
        intent=RiskOrderIntent(
            condition_id="m1",
            outcome="Up",
            side="BUY",
            qty=10,
            limit_price=0.50,
        ),
        tif="FOK",
        levels=[(0.50, 4)],
        order_timestamps=order_times(),
        reference_price=0.50,
        mark_price=0.60,
        limits=limits(),
        context=context(),
    )

    assert result.execution.simulation.final_status == OrderStatus.EXPIRED
    assert result.execution.execution.filled_qty == pytest.approx(0)
    assert p.cash == pytest.approx(100)
    assert p.reserved_cash == pytest.approx(0)
    assert p.positions == {}


def test_taker_fee_is_reserved_and_applied():
    p = PortfolioState(cash=5.01)

    result = execute_aggressive_with_risk(
        portfolio=p,
        intent=RiskOrderIntent(
            condition_id="m1",
            outcome="Up",
            side="BUY",
            qty=10,
            limit_price=0.50,
        ),
        tif="IOC",
        levels=[(0.50, 10)],
        order_timestamps=order_times(),
        reference_price=0.50,
        mark_price=0.60,
        limits=limits(),
        context=context(),
        fee_schedule=FeeSchedule(
            taker_fee_bps=20,
        ),
    )

    assert result.risk.allowed
    assert result.risk.cash_required == pytest.approx(5.01)
    assert p.cash == pytest.approx(0)
    assert p.reserved_cash == pytest.approx(0)
    assert p.realized_pnl == pytest.approx(-0.01)


def test_fee_buffer_can_reject_order_before_execution():
    p = PortfolioState(cash=5)

    result = execute_aggressive_with_risk(
        portfolio=p,
        intent=RiskOrderIntent(
            condition_id="m1",
            outcome="Up",
            side="BUY",
            qty=10,
            limit_price=0.50,
        ),
        tif="IOC",
        levels=[(0.50, 10)],
        order_timestamps=order_times(),
        reference_price=0.50,
        mark_price=0.60,
        limits=limits(),
        context=context(),
        fee_schedule=FeeSchedule(
            taker_fee_bps=20,
        ),
    )

    assert not result.risk.allowed
    assert "INSUFFICIENT_AVAILABLE_CASH" in result.risk.reasons
    assert result.execution is None
    assert p.cash == pytest.approx(5)
    assert p.reserved_cash == pytest.approx(0)


def test_sell_execution_reduces_inventory_and_realizes_pnl():
    p = PortfolioState(cash=100)

    p.apply_buy_fill(
        condition_id="m1",
        outcome="Up",
        qty=10,
        price=0.50,
    )

    result = execute_aggressive_with_risk(
        portfolio=p,
        intent=RiskOrderIntent(
            condition_id="m1",
            outcome="Up",
            side="SELL",
            qty=4,
            limit_price=0.60,
        ),
        tif="IOC",
        levels=[(0.60, 4)],
        order_timestamps=order_times(),
        reference_price=0.60,
        mark_price=0.50,
        limits=limits(),
        context=context(),
    )

    assert result.risk.allowed
    assert p.position("m1", "Up").qty == pytest.approx(6)
    assert p.realized_pnl == pytest.approx(0.4)
    assert p.reserved_cash == pytest.approx(0)


def test_sell_without_inventory_is_rejected_without_mutation():
    p = PortfolioState(cash=100)

    result = execute_aggressive_with_risk(
        portfolio=p,
        intent=RiskOrderIntent(
            condition_id="m1",
            outcome="Up",
            side="SELL",
            qty=1,
            limit_price=0.50,
        ),
        tif="IOC",
        levels=[(0.50, 1)],
        order_timestamps=order_times(),
        reference_price=0.50,
        mark_price=0.40,
        limits=limits(),
        context=context(),
    )

    assert not result.risk.allowed
    assert (
        "INSUFFICIENT_AVAILABLE_POSITION"
        in result.risk.reasons
    )
    assert p.positions == {}


def test_buy_limit_price_blocks_worse_levels():
    p = PortfolioState(cash=100)

    result = execute_aggressive_with_risk(
        portfolio=p,
        intent=RiskOrderIntent(
            condition_id="m1",
            outcome="Up",
            side="BUY",
            qty=10,
            limit_price=0.50,
        ),
        tif="IOC",
        levels=[
            (0.50, 2),
            (0.51, 8),
        ],
        order_timestamps=order_times(),
        reference_price=0.50,
        mark_price=0.60,
        limits=limits(),
        context=context(),
    )

    assert result.risk.allowed
    assert result.execution.simulation.final_status == OrderStatus.EXPIRED
    assert result.execution.execution.filled_qty == pytest.approx(2)
    assert p.position("m1", "Up").qty == pytest.approx(2)
    assert p.cash == pytest.approx(99)
    assert p.reserved_cash == pytest.approx(0)


def test_fok_does_not_use_liquidity_beyond_buy_limit():
    p = PortfolioState(cash=100)

    result = execute_aggressive_with_risk(
        portfolio=p,
        intent=RiskOrderIntent(
            condition_id="m1",
            outcome="Up",
            side="BUY",
            qty=10,
            limit_price=0.50,
        ),
        tif="FOK",
        levels=[
            (0.50, 2),
            (0.51, 20),
        ],
        order_timestamps=order_times(),
        reference_price=0.50,
        mark_price=0.60,
        limits=limits(),
        context=context(),
    )

    assert result.execution.simulation.final_status == OrderStatus.EXPIRED
    assert result.execution.execution.filled_qty == pytest.approx(0)
    assert p.cash == pytest.approx(100)
    assert p.reserved_cash == pytest.approx(0)


def test_existing_unrelated_reservation_is_not_released():
    p = PortfolioState(cash=100)
    p.reserve_buy_cash(20)

    result = execute_aggressive_with_risk(
        portfolio=p,
        intent=RiskOrderIntent(
            condition_id="m1",
            outcome="Up",
            side="BUY",
            qty=10,
            limit_price=0.50,
        ),
        tif="IOC",
        levels=[
            (0.50, 10),
        ],
        order_timestamps=order_times(),
        reference_price=0.50,
        mark_price=0.60,
        limits=limits(),
        context=context(),
    )

    assert result.risk.allowed
    assert p.cash == pytest.approx(95)
    assert p.reserved_cash == pytest.approx(20)
    assert p.available_cash == pytest.approx(75)


def test_partial_fill_preserves_unrelated_reservation():
    p = PortfolioState(cash=100)
    p.reserve_buy_cash(10)

    result = execute_aggressive_with_risk(
        portfolio=p,
        intent=RiskOrderIntent(
            condition_id="m1",
            outcome="Up",
            side="BUY",
            qty=10,
            limit_price=0.50,
        ),
        tif="IOC",
        levels=[
            (0.50, 4),
        ],
        order_timestamps=order_times(),
        reference_price=0.50,
        mark_price=0.60,
        limits=limits(),
        context=context(),
    )

    assert result.execution.execution.filled_qty == pytest.approx(4)
    assert p.cash == pytest.approx(98)
    assert p.reserved_cash == pytest.approx(10)
