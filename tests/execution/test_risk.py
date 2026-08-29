import pytest

from std0_quant.execution.portfolio import PortfolioState
from std0_quant.execution.risk import (
    RiskContext,
    RiskDecision,
    RiskLimits,
    RiskOrderIntent,
    evaluate_order_risk,
)


def limits():
    return RiskLimits(
        max_order_notional=10,
        max_market_exposure=20,
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


def test_clean_buy_is_allowed():
    p = PortfolioState(cash=100)

    result = evaluate_order_risk(
        portfolio=p,
        intent=RiskOrderIntent(
            condition_id="m1",
            outcome="Up",
            side="BUY",
            qty=10,
            limit_price=0.50,
        ),
        limits=limits(),
        context=context(),
    )

    assert result.decision == RiskDecision.ALLOW
    assert result.allowed
    assert result.reasons == ()
    assert result.order_notional == pytest.approx(5)


def test_gate_is_side_effect_free():
    p = PortfolioState(cash=100)

    evaluate_order_risk(
        portfolio=p,
        intent=RiskOrderIntent(
            condition_id="m1",
            outcome="Up",
            side="BUY",
            qty=10,
            limit_price=0.50,
        ),
        limits=limits(),
        context=context(),
    )

    assert p.cash == pytest.approx(100)
    assert p.reserved_cash == pytest.approx(0)
    assert p.positions == {}


def test_kill_switch_rejects():
    p = PortfolioState(cash=100)

    result = evaluate_order_risk(
        portfolio=p,
        intent=RiskOrderIntent(
            condition_id="m1",
            outcome="Up",
            side="BUY",
            qty=1,
            limit_price=0.50,
        ),
        limits=limits(),
        context=context(kill_switch=True),
    )

    assert not result.allowed
    assert "KILL_SWITCH_ACTIVE" in result.reasons


def test_stale_market_data_rejects():
    p = PortfolioState(cash=100)

    result = evaluate_order_risk(
        portfolio=p,
        intent=RiskOrderIntent(
            condition_id="m1",
            outcome="Up",
            side="BUY",
            qty=1,
            limit_price=0.50,
        ),
        limits=limits(),
        context=context(
            market_data_ts_ms=8_000,
        ),
    )

    assert "STALE_MARKET_DATA" in result.reasons


def test_future_market_data_rejects():
    p = PortfolioState(cash=100)

    result = evaluate_order_risk(
        portfolio=p,
        intent=RiskOrderIntent(
            condition_id="m1",
            outcome="Up",
            side="BUY",
            qty=1,
            limit_price=0.50,
        ),
        limits=limits(),
        context=context(
            market_data_ts_ms=10_001,
        ),
    )

    assert "MARKET_DATA_FROM_FUTURE" in result.reasons


def test_max_order_notional_rejects():
    p = PortfolioState(cash=100)

    result = evaluate_order_risk(
        portfolio=p,
        intent=RiskOrderIntent(
            condition_id="m1",
            outcome="Up",
            side="BUY",
            qty=30,
            limit_price=0.50,
        ),
        limits=limits(),
        context=context(),
    )

    assert "MAX_ORDER_NOTIONAL_EXCEEDED" in result.reasons


def test_insufficient_available_cash_rejects():
    p = PortfolioState(
        cash=10,
        reserved_cash=8,
    )

    result = evaluate_order_risk(
        portfolio=p,
        intent=RiskOrderIntent(
            condition_id="m1",
            outcome="Up",
            side="BUY",
            qty=6,
            limit_price=0.50,
        ),
        limits=limits(),
        context=context(),
    )

    assert "INSUFFICIENT_AVAILABLE_CASH" in result.reasons


def test_market_exposure_limit_rejects():
    p = PortfolioState(cash=100)

    p.apply_buy_fill(
        condition_id="m1",
        outcome="Up",
        qty=30,
        price=0.50,
    )

    result = evaluate_order_risk(
        portfolio=p,
        intent=RiskOrderIntent(
            condition_id="m1",
            outcome="Down",
            side="BUY",
            qty=20,
            limit_price=0.50,
        ),
        limits=limits(),
        context=context(),
    )

    assert (
        "MAX_MARKET_EXPOSURE_EXCEEDED"
        in result.reasons
    )


def test_gross_exposure_limit_rejects():
    p = PortfolioState(cash=200)

    p.apply_buy_fill(
        condition_id="m1",
        outcome="Up",
        qty=90,
        price=0.50,
    )

    custom_limits = RiskLimits(
        max_order_notional=10,
        max_market_exposure=100,
        max_gross_exposure=50,
        max_daily_loss=5,
        max_market_data_age_ms=1000,
    )

    result = evaluate_order_risk(
        portfolio=p,
        intent=RiskOrderIntent(
            condition_id="m2",
            outcome="Up",
            side="BUY",
            qty=20,
            limit_price=0.50,
        ),
        limits=custom_limits,
        context=context(),
    )

    assert (
        "MAX_GROSS_EXPOSURE_EXCEEDED"
        in result.reasons
    )


def test_daily_loss_limit_rejects():
    p = PortfolioState(
        cash=100,
        realized_pnl=-6,
    )

    result = evaluate_order_risk(
        portfolio=p,
        intent=RiskOrderIntent(
            condition_id="m1",
            outcome="Up",
            side="BUY",
            qty=1,
            limit_price=0.50,
        ),
        limits=limits(),
        context=context(),
    )

    assert "MAX_DAILY_LOSS_REACHED" in result.reasons


def test_sell_requires_inventory():
    p = PortfolioState(cash=100)

    result = evaluate_order_risk(
        portfolio=p,
        intent=RiskOrderIntent(
            condition_id="m1",
            outcome="Up",
            side="SELL",
            qty=1,
            limit_price=0.50,
        ),
        limits=limits(),
        context=context(),
    )

    assert "INSUFFICIENT_AVAILABLE_POSITION" in result.reasons


def test_sell_reduces_exposure():
    p = PortfolioState(cash=100)

    p.apply_buy_fill(
        condition_id="m1",
        outcome="Up",
        qty=10,
        price=0.50,
    )

    result = evaluate_order_risk(
        portfolio=p,
        intent=RiskOrderIntent(
            condition_id="m1",
            outcome="Up",
            side="SELL",
            qty=4,
            limit_price=0.60,
        ),
        limits=limits(),
        context=context(),
    )

    assert result.allowed
    assert (
        result.market_exposure_after
        < result.market_exposure_before
    )
    assert (
        result.gross_exposure_after
        < result.gross_exposure_before
    )


def test_multiple_reasons_are_preserved():
    p = PortfolioState(
        cash=1,
        realized_pnl=-10,
    )

    result = evaluate_order_risk(
        portfolio=p,
        intent=RiskOrderIntent(
            condition_id="m1",
            outcome="Up",
            side="BUY",
            qty=100,
            limit_price=0.50,
        ),
        limits=limits(),
        context=context(
            kill_switch=True,
            market_data_ts_ms=8_000,
        ),
    )

    assert result.decision == RiskDecision.REJECT
    assert "KILL_SWITCH_ACTIVE" in result.reasons
    assert "STALE_MARKET_DATA" in result.reasons
    assert "MAX_ORDER_NOTIONAL_EXCEEDED" in result.reasons
    assert "INSUFFICIENT_AVAILABLE_CASH" in result.reasons
    assert "MAX_DAILY_LOSS_REACHED" in result.reasons


def test_exact_limits_are_allowed():
    p = PortfolioState(cash=100)

    custom_limits = RiskLimits(
        max_order_notional=5,
        max_market_exposure=5,
        max_gross_exposure=5,
        max_daily_loss=5,
        max_market_data_age_ms=1000,
    )

    result = evaluate_order_risk(
        portfolio=p,
        intent=RiskOrderIntent(
            condition_id="m1",
            outcome="Up",
            side="BUY",
            qty=10,
            limit_price=0.50,
        ),
        limits=custom_limits,
        context=RiskContext(
            now_ts_ms=10_000,
            market_data_ts_ms=9_000,
        ),
    )

    assert result.allowed


@pytest.mark.parametrize(
    "side,qty,price",
    [
        ("INVALID", 1, 0.5),
        ("BUY", 0, 0.5),
        ("BUY", -1, 0.5),
        ("BUY", 1, 0),
        ("BUY", 1, -0.5),
    ],
)
def test_invalid_intent_fails_closed(
    side,
    qty,
    price,
):
    with pytest.raises(ValueError):
        RiskOrderIntent(
            condition_id="m1",
            outcome="Up",
            side=side,
            qty=qty,
            limit_price=price,
        )


def test_negative_risk_limit_fails_closed():
    with pytest.raises(ValueError):
        RiskLimits(
            max_order_notional=-1,
            max_market_exposure=1,
            max_gross_exposure=1,
            max_daily_loss=1,
            max_market_data_age_ms=1,
        )


def test_buy_fee_buffer_is_included_in_cash_gate():
    p = PortfolioState(cash=5)

    result = evaluate_order_risk(
        portfolio=p,
        intent=RiskOrderIntent(
            condition_id="m1",
            outcome="Up",
            side="BUY",
            qty=10,
            limit_price=0.50,
        ),
        limits=limits(),
        context=RiskContext(
            now_ts_ms=10_000,
            market_data_ts_ms=9_500,
            estimated_fee_cost=0.01,
        ),
    )

    assert result.order_notional == pytest.approx(5)
    assert result.cash_required == pytest.approx(5.01)
    assert "INSUFFICIENT_AVAILABLE_CASH" in result.reasons


def test_sell_does_not_require_buy_cash_reserve():
    p = PortfolioState(cash=0)

    p.positions[("m1", "Up")] = p.position("m1", "Up")
    p.positions[("m1", "Up")].qty = 10
    p.positions[("m1", "Up")].cost_basis = 5

    result = evaluate_order_risk(
        portfolio=p,
        intent=RiskOrderIntent(
            condition_id="m1",
            outcome="Up",
            side="SELL",
            qty=2,
            limit_price=0.50,
        ),
        limits=limits(),
        context=RiskContext(
            now_ts_ms=10_000,
            market_data_ts_ms=9_500,
            estimated_fee_cost=0.01,
        ),
    )

    assert result.cash_required == pytest.approx(0)
    assert "INSUFFICIENT_AVAILABLE_CASH" not in result.reasons


def test_negative_fee_estimate_fails_closed():
    with pytest.raises(ValueError):
        RiskContext(
            now_ts_ms=10_000,
            market_data_ts_ms=9_500,
            estimated_fee_cost=-0.01,
        )


def test_rejected_sell_does_not_create_empty_position():
    p = PortfolioState(cash=100)

    result = evaluate_order_risk(
        portfolio=p,
        intent=RiskOrderIntent(
            condition_id="m1",
            outcome="Up",
            side="SELL",
            qty=1,
            limit_price=0.50,
        ),
        limits=limits(),
        context=context(),
    )

    assert not result.allowed
    assert "INSUFFICIENT_AVAILABLE_POSITION" in result.reasons
    assert p.positions == {}


def test_reserved_sell_qty_reduces_risk_available_inventory():
    p = PortfolioState(cash=100)

    p.apply_buy_fill(
        condition_id="m1",
        outcome="Up",
        qty=10,
        price=0.50,
    )

    p.reserve_sell_qty(
        "m1",
        "Up",
        7,
    )

    result = evaluate_order_risk(
        portfolio=p,
        intent=RiskOrderIntent(
            condition_id="m1",
            outcome="Up",
            side="SELL",
            qty=4,
            limit_price=0.60,
        ),
        limits=limits(),
        context=context(),
    )

    assert not result.allowed
    assert (
        "INSUFFICIENT_AVAILABLE_POSITION"
        in result.reasons
    )

    assert p.position(
        "m1",
        "Up",
    ).qty == pytest.approx(10)

    assert p.reserved_position_qty(
        "m1",
        "Up",
    ) == pytest.approx(7)


def test_sell_exactly_available_after_reservation_is_allowed():
    p = PortfolioState(cash=100)

    p.apply_buy_fill(
        condition_id="m1",
        outcome="Up",
        qty=10,
        price=0.50,
    )

    p.reserve_sell_qty(
        "m1",
        "Up",
        7,
    )

    result = evaluate_order_risk(
        portfolio=p,
        intent=RiskOrderIntent(
            condition_id="m1",
            outcome="Up",
            side="SELL",
            qty=3,
            limit_price=0.60,
        ),
        limits=limits(),
        context=context(),
    )

    assert result.allowed

    assert p.position(
        "m1",
        "Up",
    ).qty == pytest.approx(10)

    assert p.reserved_position_qty(
        "m1",
        "Up",
    ) == pytest.approx(7)


def test_sell_risk_check_does_not_mutate_sell_reservation():
    p = PortfolioState(cash=100)

    p.apply_buy_fill(
        condition_id="m1",
        outcome="Up",
        qty=10,
        price=0.50,
    )

    p.reserve_sell_qty(
        "m1",
        "Up",
        5,
    )

    before_positions = dict(p.reserved_positions)

    evaluate_order_risk(
        portfolio=p,
        intent=RiskOrderIntent(
            condition_id="m1",
            outcome="Up",
            side="SELL",
            qty=2,
            limit_price=0.60,
        ),
        limits=limits(),
        context=context(),
    )

    assert p.reserved_positions == before_positions
