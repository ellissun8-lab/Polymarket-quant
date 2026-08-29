import pytest

from std0_quant.execution.cost_pnl import (
    FeeSchedule,
    mark_to_market_pnl,
    summarize_execution,
)
from std0_quant.execution.fill_model import Fill


def test_buy_vwap_and_adverse_slippage():
    result = summarize_execution(
        side="BUY",
        requested_qty=5,
        reference_price=0.50,
        fills=[
            Fill(0.50, 2, "taker"),
            Fill(0.52, 3, "taker"),
        ],
    )

    assert result.filled_qty == pytest.approx(5)
    assert result.fill_ratio == pytest.approx(1)
    assert result.average_fill_price == pytest.approx(
        (0.50 * 2 + 0.52 * 3) / 5
    )
    assert result.slippage_per_unit == pytest.approx(
        result.average_fill_price - 0.50
    )
    assert result.slippage_cost > 0


def test_sell_adverse_slippage_sign():
    result = summarize_execution(
        side="SELL",
        requested_qty=2,
        reference_price=0.60,
        fills=[
            Fill(0.58, 2, "taker"),
        ],
    )

    assert result.slippage_per_unit == pytest.approx(0.02)
    assert result.slippage_cost == pytest.approx(0.04)


def test_price_improvement_is_negative_slippage_cost():
    result = summarize_execution(
        side="BUY",
        requested_qty=2,
        reference_price=0.55,
        fills=[
            Fill(0.53, 2, "maker"),
        ],
    )

    assert result.slippage_per_unit == pytest.approx(-0.02)
    assert result.slippage_cost == pytest.approx(-0.04)


def test_partial_fill_keeps_unfilled_quantity_explicit():
    result = summarize_execution(
        side="BUY",
        requested_qty=10,
        reference_price=0.50,
        fills=[
            Fill(0.51, 4, "taker"),
        ],
    )

    assert result.filled_qty == pytest.approx(4)
    assert result.unfilled_qty == pytest.approx(6)
    assert result.fill_ratio == pytest.approx(0.4)


def test_zero_fill_has_no_fake_vwap_or_slippage():
    result = summarize_execution(
        side="BUY",
        requested_qty=5,
        reference_price=0.50,
        fills=[],
    )

    assert result.filled_qty == pytest.approx(0)
    assert result.average_fill_price is None
    assert result.slippage_per_unit is None
    assert result.slippage_cost == pytest.approx(0)


def test_maker_fee_and_rebate_are_separate():
    result = summarize_execution(
        side="BUY",
        requested_qty=10,
        reference_price=0.50,
        fills=[
            Fill(0.50, 10, "maker"),
        ],
        fee_schedule=FeeSchedule(
            maker_fee_bps=10,
            maker_rebate_bps=4,
        ),
    )

    notional = 5.0

    assert result.fee_cost == pytest.approx(
        notional * 10 / 10000
    )
    assert result.rebate_credit == pytest.approx(
        notional * 4 / 10000
    )
    assert result.net_fee_cost == pytest.approx(
        notional * 6 / 10000
    )


def test_taker_fee_is_applied():
    result = summarize_execution(
        side="BUY",
        requested_qty=10,
        reference_price=0.50,
        fills=[
            Fill(0.50, 10, "taker"),
        ],
        fee_schedule=FeeSchedule(
            taker_fee_bps=20,
        ),
    )

    assert result.net_fee_cost == pytest.approx(
        5.0 * 20 / 10000
    )


def test_buy_mark_to_market_pnl_uses_actual_fills_only():
    execution = summarize_execution(
        side="BUY",
        requested_qty=10,
        reference_price=0.50,
        fills=[
            Fill(0.50, 4, "taker"),
        ],
    )

    pnl = mark_to_market_pnl(
        execution=execution,
        mark_price=0.60,
    )

    assert pnl.filled_qty == pytest.approx(4)
    assert pnl.gross_pnl == pytest.approx(0.4)
    assert pnl.net_pnl == pytest.approx(0.4)


def test_sell_mark_to_market_pnl():
    execution = summarize_execution(
        side="SELL",
        requested_qty=2,
        reference_price=0.60,
        fills=[
            Fill(0.60, 2, "maker"),
        ],
    )

    pnl = mark_to_market_pnl(
        execution=execution,
        mark_price=0.50,
    )

    assert pnl.gross_pnl == pytest.approx(0.2)


def test_fee_reduces_net_pnl():
    execution = summarize_execution(
        side="BUY",
        requested_qty=10,
        reference_price=0.50,
        fills=[
            Fill(0.50, 10, "taker"),
        ],
        fee_schedule=FeeSchedule(
            taker_fee_bps=100,
        ),
    )

    pnl = mark_to_market_pnl(
        execution=execution,
        mark_price=0.60,
    )

    assert pnl.gross_pnl == pytest.approx(1.0)
    assert pnl.net_pnl < pnl.gross_pnl


def test_filled_qty_cannot_exceed_requested():
    with pytest.raises(ValueError):
        summarize_execution(
            side="BUY",
            requested_qty=1,
            reference_price=0.50,
            fills=[
                Fill(0.50, 2, "taker"),
            ],
        )


def test_unknown_liquidity_type_fails_closed():
    with pytest.raises(ValueError):
        summarize_execution(
            side="BUY",
            requested_qty=1,
            reference_price=0.50,
            fills=[
                Fill(0.50, 1, "unknown"),
            ],
        )


def test_negative_fee_input_fails_closed():
    with pytest.raises(ValueError):
        FeeSchedule(
            taker_fee_bps=-1,
        )
