import pytest

from std0_quant.execution.cost_pnl import FeeSchedule
from std0_quant.execution.costed_simulator import (
    simulate_aggressive_with_costs,
    simulate_passive_with_costs,
)
from std0_quant.execution.execution_timestamps import (
    OrderTimestamps,
)
from std0_quant.execution.order_state import OrderStatus
from std0_quant.execution.simulator import ConfirmedTradeEvent


def order_times():
    return OrderTimestamps(
        order_send_ts_ms=1000,
        order_venue_arrival_ts_ms=1001,
        order_venue_accept_ts_ms=1002,
        order_ack_receive_ts_ms=1010,
    )


def test_passive_simulator_integrates_maker_cost_and_pnl():
    result = simulate_passive_with_costs(
        side="BUY",
        order_qty=5,
        order_price=0.50,
        displayed_qty_at_accept=0,
        order_timestamps=order_times(),
        trades=[
            ConfirmedTradeEvent(
                venue_ts_ms=1003,
                traded_qty=5,
            )
        ],
        reference_price=0.50,
        mark_price=0.60,
        fee_schedule=FeeSchedule(
            maker_fee_bps=10,
            maker_rebate_bps=4,
        ),
    )

    assert result.simulation.final_status == OrderStatus.FILLED
    assert result.execution.filled_qty == pytest.approx(5)
    assert result.execution.fill_ratio == pytest.approx(1)
    assert result.execution.average_fill_price == pytest.approx(0.50)
    assert result.execution.net_fee_cost > 0
    assert result.pnl.gross_pnl == pytest.approx(0.50)
    assert result.pnl.net_pnl < result.pnl.gross_pnl


def test_passive_partial_fill_pnl_uses_filled_qty_only():
    result = simulate_passive_with_costs(
        side="BUY",
        order_qty=10,
        order_price=0.50,
        displayed_qty_at_accept=0,
        order_timestamps=order_times(),
        trades=[
            ConfirmedTradeEvent(
                venue_ts_ms=1003,
                traded_qty=4,
            )
        ],
        reference_price=0.50,
        mark_price=0.60,
    )

    assert result.simulation.filled_qty == pytest.approx(4)
    assert result.execution.unfilled_qty == pytest.approx(6)
    assert result.execution.fill_ratio == pytest.approx(0.4)
    assert result.pnl.gross_pnl == pytest.approx(0.4)


def test_aggressive_ioc_integrates_slippage_fee_and_pnl():
    result = simulate_aggressive_with_costs(
        side="BUY",
        tif="IOC",
        order_qty=5,
        levels=[
            (0.50, 2),
            (0.52, 3),
        ],
        order_timestamps=order_times(),
        reference_price=0.50,
        mark_price=0.60,
        fee_schedule=FeeSchedule(
            taker_fee_bps=20,
        ),
    )

    assert result.simulation.final_status == OrderStatus.FILLED
    assert result.execution.filled_qty == pytest.approx(5)
    assert result.execution.slippage_cost > 0
    assert result.execution.net_fee_cost > 0
    assert result.execution.total_execution_cost > 0
    assert result.pnl.net_pnl < result.pnl.gross_pnl


def test_aggressive_partial_ioc_never_assigns_pnl_to_unfilled_qty():
    result = simulate_aggressive_with_costs(
        side="BUY",
        tif="IOC",
        order_qty=10,
        levels=[
            (0.50, 2),
            (0.51, 3),
        ],
        order_timestamps=order_times(),
        reference_price=0.50,
        mark_price=0.60,
    )

    assert result.simulation.final_status == OrderStatus.EXPIRED
    assert result.execution.filled_qty == pytest.approx(5)
    assert result.execution.unfilled_qty == pytest.approx(5)

    expected_vwap = (0.50 * 2 + 0.51 * 3) / 5
    expected_pnl = (0.60 - expected_vwap) * 5

    assert result.pnl.gross_pnl == pytest.approx(expected_pnl)


def test_fok_failure_has_zero_execution_cost_and_zero_pnl():
    result = simulate_aggressive_with_costs(
        side="BUY",
        tif="FOK",
        order_qty=10,
        levels=[
            (0.50, 2),
        ],
        order_timestamps=order_times(),
        reference_price=0.50,
        mark_price=0.60,
        fee_schedule=FeeSchedule(
            taker_fee_bps=100,
        ),
    )

    assert result.simulation.final_status == OrderStatus.EXPIRED
    assert result.execution.filled_qty == pytest.approx(0)
    assert result.execution.gross_notional == pytest.approx(0)
    assert result.execution.net_fee_cost == pytest.approx(0)
    assert result.execution.slippage_cost == pytest.approx(0)
    assert result.pnl.gross_pnl == pytest.approx(0)
    assert result.pnl.net_pnl == pytest.approx(0)


def test_sell_aggressive_cost_and_pnl_signs():
    result = simulate_aggressive_with_costs(
        side="SELL",
        tif="IOC",
        order_qty=2,
        levels=[
            (0.60, 1),
            (0.58, 1),
        ],
        order_timestamps=order_times(),
        reference_price=0.60,
        mark_price=0.50,
    )

    assert result.execution.average_fill_price == pytest.approx(0.59)
    assert result.execution.slippage_per_unit == pytest.approx(0.01)
    assert result.pnl.gross_pnl == pytest.approx(0.18)
