import pytest

from std0_quant.execution.fill_model import (
    aggressive_sweep,
    passive_fill_from_confirmed_trade,
)
from std0_quant.execution.queue_model import FIFOQueueModel


def test_passive_trade_only_depletes_queue_when_not_reached():
    queue = FIFOQueueModel(order_qty=5, queue_ahead_qty=10)

    result = passive_fill_from_confirmed_trade(
        queue=queue,
        order_price=0.55,
        traded_qty=4,
    )

    assert result.filled_qty == pytest.approx(0)
    assert result.remaining_qty == pytest.approx(5)
    assert result.average_price is None
    assert queue.queue_ahead_qty == pytest.approx(6)


def test_passive_trade_crosses_queue_and_partially_fills():
    queue = FIFOQueueModel(order_qty=5, queue_ahead_qty=10)

    result = passive_fill_from_confirmed_trade(
        queue=queue,
        order_price=0.55,
        traded_qty=12,
    )

    assert result.filled_qty == pytest.approx(2)
    assert result.remaining_qty == pytest.approx(3)
    assert result.average_price == pytest.approx(0.55)
    assert result.fills[0].liquidity == "maker"


def test_passive_fill_is_capped_at_remaining_quantity():
    queue = FIFOQueueModel(order_qty=5, queue_ahead_qty=1)

    result = passive_fill_from_confirmed_trade(
        queue=queue,
        order_price=0.55,
        traded_qty=100,
    )

    assert result.filled_qty == pytest.approx(5)
    assert result.remaining_qty == pytest.approx(0)
    assert result.is_fully_filled


def test_aggressive_buy_sweeps_asks_best_to_worst():
    result = aggressive_sweep(
        side="BUY",
        order_qty=5,
        levels=[
            (0.50, 2),
            (0.51, 2),
            (0.52, 10),
        ],
    )

    assert result.filled_qty == pytest.approx(5)
    assert result.remaining_qty == pytest.approx(0)
    assert result.is_fully_filled
    assert [f.qty for f in result.fills] == pytest.approx(
        [2, 2, 1]
    )
    assert result.average_price == pytest.approx(
        (0.50 * 2 + 0.51 * 2 + 0.52) / 5
    )
    assert all(f.liquidity == "taker" for f in result.fills)


def test_aggressive_sell_sweeps_bids_best_to_worst():
    result = aggressive_sweep(
        side="SELL",
        order_qty=4,
        levels=[
            (0.60, 1),
            (0.59, 2),
            (0.58, 10),
        ],
    )

    assert result.filled_qty == pytest.approx(4)
    assert [f.qty for f in result.fills] == pytest.approx(
        [1, 2, 1]
    )
    assert result.average_price == pytest.approx(
        (0.60 + 0.59 * 2 + 0.58) / 4
    )


def test_aggressive_fill_can_be_partial_when_book_is_insufficient():
    result = aggressive_sweep(
        side="BUY",
        order_qty=10,
        levels=[
            (0.50, 2),
            (0.51, 3),
        ],
    )

    assert result.filled_qty == pytest.approx(5)
    assert result.remaining_qty == pytest.approx(5)
    assert not result.is_fully_filled


def test_zero_size_levels_do_not_create_fills():
    result = aggressive_sweep(
        side="BUY",
        order_qty=2,
        levels=[
            (0.50, 0),
            (0.51, 2),
        ],
    )

    assert len(result.fills) == 1
    assert result.fills[0].price == pytest.approx(0.51)


def test_buy_levels_out_of_order_fail_closed():
    with pytest.raises(ValueError):
        aggressive_sweep(
            side="BUY",
            order_qty=1,
            levels=[
                (0.51, 1),
                (0.50, 1),
            ],
        )


def test_sell_levels_out_of_order_fail_closed():
    with pytest.raises(ValueError):
        aggressive_sweep(
            side="SELL",
            order_qty=1,
            levels=[
                (0.59, 1),
                (0.60, 1),
            ],
        )


@pytest.mark.parametrize(
    "side,qty,levels",
    [
        ("INVALID", 1, [(0.5, 1)]),
        ("BUY", 0, [(0.5, 1)]),
        ("BUY", -1, [(0.5, 1)]),
        ("BUY", 1, [(-0.5, 1)]),
        ("BUY", 1, [(0.5, -1)]),
    ],
)
def test_invalid_aggressive_inputs_fail_closed(
    side,
    qty,
    levels,
):
    with pytest.raises(ValueError):
        aggressive_sweep(
            side=side,
            order_qty=qty,
            levels=levels,
        )
