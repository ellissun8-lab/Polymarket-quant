import pytest

from std0_quant.execution.order_state import (
    OrderStateMachine,
    OrderStatus,
)


def acknowledged_order(qty=10):
    order = OrderStateMachine(order_qty=qty)
    order.mark_sent(1000)
    order.acknowledge(1001)
    return order


def test_normal_order_lifecycle():
    order = OrderStateMachine(order_qty=10)

    order.mark_sent(1000)
    assert order.status == OrderStatus.SENT

    order.acknowledge(1001)
    assert order.status == OrderStatus.ACKNOWLEDGED

    order.apply_fill(4, 1002)
    assert order.status == OrderStatus.PARTIALLY_FILLED
    assert order.filled_qty == pytest.approx(4)
    assert order.remaining_qty == pytest.approx(6)

    order.apply_fill(6, 1003)
    assert order.status == OrderStatus.FILLED
    assert order.remaining_qty == pytest.approx(0)
    assert order.is_terminal


def test_cancel_lifecycle():
    order = acknowledged_order()

    order.request_cancel(1002)
    assert order.status == OrderStatus.CANCEL_REQUESTED

    order.acknowledge_cancel(1003)
    assert order.status == OrderStatus.CANCELLED
    assert order.is_terminal


def test_partially_filled_order_can_request_cancel():
    order = acknowledged_order()

    order.apply_fill(3, 1002)
    order.request_cancel(1003)

    assert order.filled_qty == pytest.approx(3)
    assert order.remaining_qty == pytest.approx(7)
    assert order.status == OrderStatus.CANCEL_REQUESTED


def test_fill_may_arrive_while_cancel_is_in_flight():
    order = acknowledged_order()

    order.request_cancel(1002)
    event = order.apply_fill(3, 1003)

    assert event.status_before == OrderStatus.CANCEL_REQUESTED
    assert event.status_after == OrderStatus.CANCEL_REQUESTED
    assert order.filled_qty == pytest.approx(3)
    assert order.remaining_qty == pytest.approx(7)


def test_cancel_in_flight_fill_can_complete_order():
    order = acknowledged_order(qty=5)

    order.request_cancel(1002)
    order.apply_fill(5, 1003)

    assert order.status == OrderStatus.FILLED
    assert order.is_terminal


def test_cancelled_order_cannot_fill():
    order = acknowledged_order()

    order.request_cancel(1002)
    order.acknowledge_cancel(1003)

    with pytest.raises(ValueError):
        order.apply_fill(1, 1004)


def test_filled_order_cannot_request_cancel():
    order = acknowledged_order(qty=5)
    order.apply_fill(5, 1002)

    with pytest.raises(ValueError):
        order.request_cancel(1003)


def test_fill_cannot_exceed_remaining_quantity():
    order = acknowledged_order(qty=5)

    with pytest.raises(ValueError):
        order.apply_fill(6, 1002)


def test_rejected_order_is_terminal():
    order = OrderStateMachine(order_qty=10)

    order.mark_sent(1000)
    order.reject(1001)

    assert order.status == OrderStatus.REJECTED
    assert order.is_terminal


def test_out_of_order_timestamp_fails_closed():
    order = OrderStateMachine(order_qty=10)

    order.mark_sent(1000)

    with pytest.raises(ValueError):
        order.acknowledge(999)


def test_duplicate_ack_fails_closed():
    order = acknowledged_order()

    with pytest.raises(ValueError):
        order.acknowledge(1002)


def test_cancel_ack_without_cancel_request_fails_closed():
    order = acknowledged_order()

    with pytest.raises(ValueError):
        order.acknowledge_cancel(1002)


def test_fill_before_ack_fails_closed():
    order = OrderStateMachine(order_qty=10)
    order.mark_sent(1000)

    with pytest.raises(ValueError):
        order.apply_fill(1, 1001)


@pytest.mark.parametrize(
    "qty",
    [
        0,
        -1,
        float("nan"),
        float("inf"),
    ],
)
def test_invalid_order_quantity_fails_closed(qty):
    with pytest.raises(ValueError):
        OrderStateMachine(order_qty=qty)


def test_invalid_fill_quantity_fails_closed():
    order = acknowledged_order()

    with pytest.raises(ValueError):
        order.apply_fill(0, 1002)

    with pytest.raises(ValueError):
        order.apply_fill(-1, 1002)
