import pytest

from std0_quant.execution.cancel_fill_race import (
    AmbiguousCancelFillRace,
    CancelFillOutcome,
    resolve_cancel_fill_race,
)
from std0_quant.execution.order_state import (
    OrderStateMachine,
    OrderStatus,
)


def cancel_requested_order(qty=10):
    order = OrderStateMachine(order_qty=qty)
    order.mark_sent(1000)
    order.acknowledge(1001)
    order.request_cancel(1002)
    return order


def test_cancel_ack_before_fill_means_cancel_wins():
    order = cancel_requested_order()

    result = resolve_cancel_fill_race(
        order=order,
        fill_qty=3,
        fill_venue_ts_ms=1004,
        cancel_effective_ts_ms=1003,
    )

    assert result.outcome == CancelFillOutcome.CANCEL_WON
    assert result.fill_qty_applied == pytest.approx(0)
    assert result.final_status == OrderStatus.CANCELLED
    assert result.filled_qty == pytest.approx(0)
    assert result.remaining_qty == pytest.approx(10)


def test_fill_before_cancel_ack_partially_fills_then_cancels():
    order = cancel_requested_order()

    result = resolve_cancel_fill_race(
        order=order,
        fill_qty=3,
        fill_venue_ts_ms=1003,
        cancel_effective_ts_ms=1004,
    )

    assert result.outcome == CancelFillOutcome.FILL_THEN_CANCEL
    assert result.fill_qty_applied == pytest.approx(3)
    assert result.final_status == OrderStatus.CANCELLED
    assert result.filled_qty == pytest.approx(3)
    assert result.remaining_qty == pytest.approx(7)


def test_fill_before_cancel_ack_can_complete_order():
    order = cancel_requested_order(qty=5)

    result = resolve_cancel_fill_race(
        order=order,
        fill_qty=5,
        fill_venue_ts_ms=1003,
        cancel_effective_ts_ms=1004,
    )

    assert (
        result.outcome
        == CancelFillOutcome.FILL_WON_COMPLETE
    )
    assert result.fill_qty_applied == pytest.approx(5)
    assert result.final_status == OrderStatus.FILLED
    assert result.filled_qty == pytest.approx(5)
    assert result.remaining_qty == pytest.approx(0)


def test_equal_timestamps_fail_closed_without_mutating_order():
    order = cancel_requested_order()

    before_status = order.status
    before_filled = order.filled_qty
    before_ts = order.last_event_ts_ms

    with pytest.raises(AmbiguousCancelFillRace):
        resolve_cancel_fill_race(
            order=order,
            fill_qty=3,
            fill_venue_ts_ms=1003,
            cancel_effective_ts_ms=1003,
        )

    assert order.status == before_status
    assert order.filled_qty == pytest.approx(before_filled)
    assert order.last_event_ts_ms == before_ts


def test_race_requires_cancel_requested_state():
    order = OrderStateMachine(order_qty=10)
    order.mark_sent(1000)
    order.acknowledge(1001)

    with pytest.raises(ValueError):
        resolve_cancel_fill_race(
            order=order,
            fill_qty=1,
            fill_venue_ts_ms=1003,
            cancel_effective_ts_ms=1004,
        )


def test_fill_timestamp_before_cancel_request_fails_closed():
    order = cancel_requested_order()

    with pytest.raises(ValueError):
        resolve_cancel_fill_race(
            order=order,
            fill_qty=1,
            fill_venue_ts_ms=1001,
            cancel_effective_ts_ms=1004,
        )


def test_cancel_ack_before_cancel_request_fails_closed():
    order = cancel_requested_order()

    with pytest.raises(ValueError):
        resolve_cancel_fill_race(
            order=order,
            fill_qty=1,
            fill_venue_ts_ms=1004,
            cancel_effective_ts_ms=1001,
        )


def test_fill_cannot_exceed_remaining_qty():
    order = cancel_requested_order(qty=5)

    with pytest.raises(ValueError):
        resolve_cancel_fill_race(
            order=order,
            fill_qty=6,
            fill_venue_ts_ms=1003,
            cancel_effective_ts_ms=1004,
        )


@pytest.mark.parametrize(
    "fill_qty",
    [
        0,
        -1,
        float("nan"),
        float("inf"),
    ],
)
def test_invalid_fill_qty_fails_closed(fill_qty):
    order = cancel_requested_order()

    with pytest.raises(ValueError):
        resolve_cancel_fill_race(
            order=order,
            fill_qty=fill_qty,
            fill_venue_ts_ms=1003,
            cancel_effective_ts_ms=1004,
        )


def test_existing_partial_fill_is_preserved_when_cancel_wins():
    order = OrderStateMachine(order_qty=10)
    order.mark_sent(1000)
    order.acknowledge(1001)
    order.apply_fill(4, 1002)
    order.request_cancel(1003)

    result = resolve_cancel_fill_race(
        order=order,
        fill_qty=2,
        fill_venue_ts_ms=1005,
        cancel_effective_ts_ms=1004,
    )

    assert result.outcome == CancelFillOutcome.CANCEL_WON
    assert result.filled_qty == pytest.approx(4)
    assert result.remaining_qty == pytest.approx(6)
    assert result.final_status == OrderStatus.CANCELLED
