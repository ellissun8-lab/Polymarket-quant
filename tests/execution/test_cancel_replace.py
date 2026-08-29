import pytest

from std0_quant.execution.cancel_replace import (
    confirm_cancel,
    create_replacement_after_cancel,
    request_cancel,
)
from std0_quant.execution.order_state import (
    OrderStateMachine,
    OrderStatus,
)


def acknowledged_order(qty=10):
    order = OrderStateMachine(order_qty=qty)
    order.mark_sent(1000)
    order.acknowledge(1001)
    return order


def cancelled_order(qty=10):
    order = acknowledged_order(qty)
    request_cancel(order, 1002)
    confirm_cancel(order, 1003)
    return order


def test_cancel_request_is_not_cancel_confirmation():
    order = acknowledged_order()

    request_cancel(order, 1002)

    assert order.status == OrderStatus.CANCEL_REQUESTED
    assert not order.is_terminal


def test_confirm_cancel_makes_order_terminal():
    order = acknowledged_order()

    request_cancel(order, 1002)
    confirm_cancel(order, 1003)

    assert order.status == OrderStatus.CANCELLED
    assert order.is_terminal


def test_replace_requires_confirmed_cancel():
    order = acknowledged_order()

    request_cancel(order, 1002)

    with pytest.raises(ValueError):
        create_replacement_after_cancel(
            old_order=order,
            new_price=0.55,
            new_qty=5,
            displayed_qty_at_arrival=7,
        )


def test_replacement_is_fresh_new_order():
    old = cancelled_order()

    replacement = create_replacement_after_cancel(
        old_order=old,
        new_price=0.55,
        new_qty=5,
        displayed_qty_at_arrival=7,
    )

    assert replacement.price == pytest.approx(0.55)

    assert replacement.order.status == OrderStatus.NEW
    assert replacement.order.order_qty == pytest.approx(5)
    assert replacement.order.filled_qty == pytest.approx(0)
    assert replacement.order.remaining_qty == pytest.approx(5)

    assert replacement.queue.order_qty == pytest.approx(5)
    assert replacement.queue.queue_ahead_qty == pytest.approx(7)
    assert replacement.queue.filled_qty == pytest.approx(0)


def test_partial_old_fill_does_not_leak_into_replacement():
    old = acknowledged_order(qty=10)

    old.apply_fill(4, 1002)
    request_cancel(old, 1003)
    confirm_cancel(old, 1004)

    replacement = create_replacement_after_cancel(
        old_order=old,
        new_price=0.56,
        new_qty=3,
        displayed_qty_at_arrival=9,
    )

    assert old.filled_qty == pytest.approx(4)
    assert old.status == OrderStatus.CANCELLED

    assert replacement.order.filled_qty == pytest.approx(0)
    assert replacement.order.order_qty == pytest.approx(3)
    assert replacement.queue.queue_ahead_qty == pytest.approx(9)


def test_replacement_qty_is_explicit_not_old_remaining_qty():
    old = acknowledged_order(qty=10)

    old.apply_fill(4, 1002)
    request_cancel(old, 1003)
    confirm_cancel(old, 1004)

    replacement = create_replacement_after_cancel(
        old_order=old,
        new_price=0.56,
        new_qty=2,
        displayed_qty_at_arrival=1,
    )

    assert old.remaining_qty == pytest.approx(6)
    assert replacement.order.order_qty == pytest.approx(2)


@pytest.mark.parametrize(
    "price,qty,queue_ahead",
    [
        (0, 1, 1),
        (-1, 1, 1),
        (0.5, 0, 1),
        (0.5, -1, 1),
        (0.5, 1, -1),
    ],
)
def test_invalid_replacement_inputs_fail_closed(
    price,
    qty,
    queue_ahead,
):
    old = cancelled_order()

    with pytest.raises(ValueError):
        create_replacement_after_cancel(
            old_order=old,
            new_price=price,
            new_qty=qty,
            displayed_qty_at_arrival=queue_ahead,
        )


def test_cancel_before_ack_fails_closed():
    order = OrderStateMachine(order_qty=10)
    order.mark_sent(1000)

    with pytest.raises(ValueError):
        request_cancel(order, 1001)


def test_cancel_confirmation_without_request_fails_closed():
    order = acknowledged_order()

    with pytest.raises(ValueError):
        confirm_cancel(order, 1002)
