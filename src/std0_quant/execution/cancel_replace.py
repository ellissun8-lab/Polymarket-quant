"""Deterministic cancel/replace model v1.

Research/simulation only. No live order submission.

Rules:
- a cancel request is not cancellation confirmation;
- replacement is allowed only after the old order is confirmed CANCELLED;
- replacement creates a fresh order with fresh price-time priority;
- replacement quantity is explicit and is never inferred from old remaining qty.
"""

from __future__ import annotations

from dataclasses import dataclass

from std0_quant.execution.order_state import (
    OrderEvent,
    OrderStateMachine,
    OrderStatus,
)
from std0_quant.execution.queue_model import FIFOQueueModel


@dataclass(frozen=True)
class ReplacementOrder:
    """Fresh replacement order and its new queue state."""

    price: float
    order: OrderStateMachine
    queue: FIFOQueueModel


def request_cancel(
    order: OrderStateMachine,
    event_ts_ms: float,
) -> OrderEvent:
    """Send a cancel request.

    The order becomes CANCEL_REQUESTED, not CANCELLED.
    """

    return order.request_cancel(event_ts_ms)


def confirm_cancel(
    order: OrderStateMachine,
    event_ts_ms: float,
) -> OrderEvent:
    """Apply venue confirmation that cancellation succeeded."""

    return order.acknowledge_cancel(event_ts_ms)


def create_replacement_after_cancel(
    *,
    old_order: OrderStateMachine,
    new_price: float,
    new_qty: float,
    displayed_qty_at_arrival: float,
) -> ReplacementOrder:
    """Create a fresh replacement after confirmed cancellation.

    The replacement starts NEW with zero fills and receives fresh FIFO
    priority behind all displayed quantity visible at its arrival.
    """

    if old_order.status != OrderStatus.CANCELLED:
        raise ValueError(
            "replacement requires old order to be CANCELLED"
        )

    price = _positive_finite(
        new_price,
        "new_price",
    )
    qty = _positive_finite(
        new_qty,
        "new_qty",
    )
    queue_ahead = _nonnegative_finite(
        displayed_qty_at_arrival,
        "displayed_qty_at_arrival",
    )

    new_order = OrderStateMachine(
        order_qty=qty,
    )

    new_queue = FIFOQueueModel(
        order_qty=qty,
        queue_ahead_qty=queue_ahead,
    )

    return ReplacementOrder(
        price=price,
        order=new_order,
        queue=new_queue,
    )


def _positive_finite(
    value: float,
    name: str,
) -> float:
    value = float(value)

    if (
        value != value
        or value in (
            float("inf"),
            float("-inf"),
        )
        or value <= 0
    ):
        raise ValueError(
            f"{name} must be finite and > 0"
        )

    return value


def _nonnegative_finite(
    value: float,
    name: str,
) -> float:
    value = float(value)

    if (
        value != value
        or value in (
            float("inf"),
            float("-inf"),
        )
        or value < 0
    ):
        raise ValueError(
            f"{name} must be finite and >= 0"
        )

    return value
