import pytest

from std0_quant.execution.portfolio import PortfolioState


def test_initial_available_cash():
    p = PortfolioState(
        cash=100,
        reserved_cash=20,
    )

    assert p.available_cash == pytest.approx(80)


def test_reserve_and_release_cash():
    p = PortfolioState(cash=100)

    p.reserve_buy_cash(30)

    assert p.reserved_cash == pytest.approx(30)
    assert p.available_cash == pytest.approx(70)

    p.release_reserved_cash(10)

    assert p.reserved_cash == pytest.approx(20)
    assert p.available_cash == pytest.approx(80)


def test_cannot_over_reserve_cash():
    p = PortfolioState(cash=100)

    with pytest.raises(ValueError):
        p.reserve_buy_cash(101)


def test_buy_fill_creates_position():
    p = PortfolioState(cash=100)

    p.apply_buy_fill(
        condition_id="m1",
        outcome="Up",
        qty=10,
        price=0.50,
    )

    pos = p.position("m1", "Up")

    assert p.cash == pytest.approx(95)
    assert pos.qty == pytest.approx(10)
    assert pos.cost_basis == pytest.approx(5)
    assert pos.average_cost == pytest.approx(0.50)
    assert p.gross_cost_exposure == pytest.approx(5)


def test_buy_fill_fee_and_rebate_affect_cash_and_realized_pnl():
    p = PortfolioState(cash=100)

    p.apply_buy_fill(
        condition_id="m1",
        outcome="Up",
        qty=10,
        price=0.50,
        fee_cost=0.10,
        rebate_credit=0.04,
    )

    assert p.cash == pytest.approx(94.94)
    assert p.realized_pnl == pytest.approx(-0.06)


def test_buy_fill_can_consume_reserved_cash():
    p = PortfolioState(cash=100)

    p.reserve_buy_cash(10)

    p.apply_buy_fill(
        condition_id="m1",
        outcome="Up",
        qty=10,
        price=0.50,
        consume_reserved_cash=True,
    )

    assert p.cash == pytest.approx(95)
    assert p.reserved_cash == pytest.approx(5)


def test_sell_fill_realizes_pnl():
    p = PortfolioState(cash=100)

    p.apply_buy_fill(
        condition_id="m1",
        outcome="Up",
        qty=10,
        price=0.50,
    )

    p.apply_sell_fill(
        condition_id="m1",
        outcome="Up",
        qty=4,
        price=0.60,
    )

    pos = p.position("m1", "Up")

    assert pos.qty == pytest.approx(6)
    assert pos.cost_basis == pytest.approx(3)
    assert p.realized_pnl == pytest.approx(0.4)
    assert p.cash == pytest.approx(97.4)


def test_sell_all_clears_position_cost_basis():
    p = PortfolioState(cash=100)

    p.apply_buy_fill(
        condition_id="m1",
        outcome="Down",
        qty=5,
        price=0.40,
    )

    p.apply_sell_fill(
        condition_id="m1",
        outcome="Down",
        qty=5,
        price=0.50,
    )

    pos = p.position("m1", "Down")

    assert pos.qty == pytest.approx(0)
    assert pos.cost_basis == pytest.approx(0)


def test_cannot_sell_more_than_held():
    p = PortfolioState(cash=100)

    p.apply_buy_fill(
        condition_id="m1",
        outcome="Up",
        qty=2,
        price=0.50,
    )

    with pytest.raises(ValueError):
        p.apply_sell_fill(
            condition_id="m1",
            outcome="Up",
            qty=3,
            price=0.60,
        )


def test_positions_are_market_and_outcome_specific():
    p = PortfolioState(cash=100)

    p.apply_buy_fill(
        condition_id="m1",
        outcome="Up",
        qty=2,
        price=0.50,
    )

    p.apply_buy_fill(
        condition_id="m1",
        outcome="Down",
        qty=3,
        price=0.40,
    )

    assert p.position("m1", "Up").qty == pytest.approx(2)
    assert p.position("m1", "Down").qty == pytest.approx(3)


@pytest.mark.parametrize(
    "cash,reserved",
    [
        (-1, 0),
        (100, -1),
        (100, 101),
    ],
)
def test_invalid_initial_state_fails_closed(cash, reserved):
    with pytest.raises(ValueError):
        PortfolioState(
            cash=cash,
            reserved_cash=reserved,
        )


def test_empty_condition_or_outcome_fails_closed():
    p = PortfolioState(cash=100)

    with pytest.raises(ValueError):
        p.position("", "Up")

    with pytest.raises(ValueError):
        p.position("m1", "")


def test_insufficient_cash_for_buy_fails_closed():
    p = PortfolioState(cash=1)

    with pytest.raises(ValueError):
        p.apply_buy_fill(
            condition_id="m1",
            outcome="Up",
            qty=10,
            price=0.50,
        )


def test_sell_position_reservation():
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
        4,
    )

    assert p.reserved_position_qty(
        "m1",
        "Up",
    ) == pytest.approx(4)

    assert p.available_position_qty(
        "m1",
        "Up",
    ) == pytest.approx(6)


def test_release_sell_position_reservation():
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
        6,
    )

    p.release_reserved_sell_qty(
        "m1",
        "Up",
        2,
    )

    assert p.reserved_position_qty(
        "m1",
        "Up",
    ) == pytest.approx(4)

    assert p.available_position_qty(
        "m1",
        "Up",
    ) == pytest.approx(6)


def test_cannot_over_reserve_sell_position():
    p = PortfolioState(cash=100)

    p.apply_buy_fill(
        condition_id="m1",
        outcome="Up",
        qty=5,
        price=0.50,
    )

    p.reserve_sell_qty(
        "m1",
        "Up",
        4,
    )

    with pytest.raises(ValueError):
        p.reserve_sell_qty(
            "m1",
            "Up",
            2,
        )


def test_release_full_sell_reservation_removes_entry():
    p = PortfolioState(cash=100)

    p.apply_buy_fill(
        condition_id="m1",
        outcome="Up",
        qty=5,
        price=0.50,
    )

    p.reserve_sell_qty(
        "m1",
        "Up",
        5,
    )

    p.release_reserved_sell_qty(
        "m1",
        "Up",
        5,
    )

    assert p.reserved_positions == {}
    assert p.available_position_qty(
        "m1",
        "Up",
    ) == pytest.approx(5)


def test_sell_reservation_does_not_change_actual_position():
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

    assert p.position(
        "m1",
        "Up",
    ).qty == pytest.approx(10)

    assert p.available_position_qty(
        "m1",
        "Up",
    ) == pytest.approx(3)
