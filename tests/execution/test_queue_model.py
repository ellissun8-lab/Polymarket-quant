import pytest

from std0_quant.execution.queue_model import FIFOQueueModel


def test_trade_depletes_queue_before_own_order():
    q = FIFOQueueModel(order_qty=5, queue_ahead_qty=10)

    fill = q.on_confirmed_trade(4)

    assert fill.queue_consumed_qty == pytest.approx(4)
    assert fill.own_fill_qty == pytest.approx(0)
    assert q.queue_ahead_qty == pytest.approx(6)
    assert q.remaining_qty == pytest.approx(5)


def test_trade_crossing_queue_boundary_partially_fills_us():
    q = FIFOQueueModel(order_qty=5, queue_ahead_qty=10)

    fill = q.on_confirmed_trade(12)

    assert fill.queue_consumed_qty == pytest.approx(10)
    assert fill.own_fill_qty == pytest.approx(2)
    assert q.queue_ahead_qty == pytest.approx(0)
    assert q.filled_qty == pytest.approx(2)
    assert q.remaining_qty == pytest.approx(3)


def test_partial_fills_accumulate():
    q = FIFOQueueModel(order_qty=5, queue_ahead_qty=2)

    first = q.on_confirmed_trade(3)
    second = q.on_confirmed_trade(2)

    assert first.own_fill_qty == pytest.approx(1)
    assert second.own_fill_qty == pytest.approx(2)
    assert q.filled_qty == pytest.approx(3)
    assert q.remaining_qty == pytest.approx(2)
    assert not q.is_filled


def test_fill_is_capped_at_remaining_order_quantity():
    q = FIFOQueueModel(order_qty=5, queue_ahead_qty=1)

    fill = q.on_confirmed_trade(20)

    assert fill.queue_consumed_qty == pytest.approx(1)
    assert fill.own_fill_qty == pytest.approx(5)
    assert q.filled_qty == pytest.approx(5)
    assert q.remaining_qty == pytest.approx(0)
    assert q.is_filled


def test_unexplained_size_decrease_does_not_improve_queue_position():
    q = FIFOQueueModel(order_qty=5, queue_ahead_qty=10)

    q.on_unexplained_size_decrease(7)

    assert q.queue_ahead_qty == pytest.approx(10)


def test_same_price_add_is_assumed_behind_us():
    q = FIFOQueueModel(order_qty=5, queue_ahead_qty=10)

    q.on_same_price_add(100)

    assert q.queue_ahead_qty == pytest.approx(10)


def test_explicit_priority_reset_uses_new_displayed_quantity():
    q = FIFOQueueModel(order_qty=5, queue_ahead_qty=10)

    q.on_confirmed_trade(12)
    assert q.filled_qty == pytest.approx(2)

    q.reset_priority(7)

    assert q.filled_qty == pytest.approx(2)
    assert q.remaining_qty == pytest.approx(3)
    assert q.queue_ahead_qty == pytest.approx(7)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"order_qty": 0, "queue_ahead_qty": 1},
        {"order_qty": -1, "queue_ahead_qty": 1},
        {"order_qty": 1, "queue_ahead_qty": -1},
        {"order_qty": 1, "queue_ahead_qty": 0, "filled_qty": 2},
    ],
)
def test_invalid_initial_state_fails_closed(kwargs):
    with pytest.raises(ValueError):
        FIFOQueueModel(**kwargs)


def test_invalid_event_quantity_fails_closed():
    q = FIFOQueueModel(order_qty=1, queue_ahead_qty=1)

    with pytest.raises(ValueError):
        q.on_confirmed_trade(-1)

    with pytest.raises(ValueError):
        q.on_same_price_add(-1)

    with pytest.raises(ValueError):
        q.on_unexplained_size_decrease(-1)


def test_cannot_reset_fully_filled_order():
    q = FIFOQueueModel(order_qty=1, queue_ahead_qty=0)
    q.on_confirmed_trade(1)

    with pytest.raises(ValueError):
        q.reset_priority(5)
