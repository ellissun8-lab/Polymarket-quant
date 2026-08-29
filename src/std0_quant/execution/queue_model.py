"""Conservative FIFO queue model v1.

The model deliberately distinguishes observable facts from assumptions.

FACT:
- displayed quantity at our price when our order reaches the venue;
- confirmed traded quantity at that price;
- our own order quantity and fills.

CONSERVATIVE ASSUMPTION:
- displayed quantity already resting at the same price is ahead of us;
- same-price additions after our arrival are behind us;
- an unexplained book-size decrease does NOT reduce queue ahead, because
  public book data does not reveal whether cancellation occurred ahead of
  or behind our order.

This module is research/simulation only and performs no live execution.
"""

from __future__ import annotations

from dataclasses import dataclass


_EPS = 1e-12


@dataclass(frozen=True)
class QueueFill:
    """Fill caused by confirmed same-price traded quantity."""

    traded_qty: float
    queue_consumed_qty: float
    own_fill_qty: float
    queue_ahead_after: float
    remaining_qty_after: float


@dataclass
class FIFOQueueModel:
    """Conservative price-time-priority queue state for one resting order."""

    order_qty: float
    queue_ahead_qty: float
    filled_qty: float = 0.0

    def __post_init__(self) -> None:
        self.order_qty = _nonnegative(
            self.order_qty,
            "order_qty",
            strictly_positive=True,
        )
        self.queue_ahead_qty = _nonnegative(
            self.queue_ahead_qty,
            "queue_ahead_qty",
        )
        self.filled_qty = _nonnegative(
            self.filled_qty,
            "filled_qty",
        )

        if self.filled_qty > self.order_qty + _EPS:
            raise ValueError("filled_qty cannot exceed order_qty")

        self._normalize()

    @property
    def remaining_qty(self) -> float:
        return max(0.0, self.order_qty - self.filled_qty)

    @property
    def is_filled(self) -> bool:
        return self.remaining_qty <= _EPS

    def on_confirmed_trade(self, traded_qty: float) -> QueueFill:
        """Apply confirmed traded quantity at our resting price.

        Confirmed traded quantity first depletes quantity ahead of us.
        Only residual quantity can fill our own resting order.

        Excess traded quantity after our order is completely filled is
        irrelevant to this order.
        """

        traded_qty = _nonnegative(
            traded_qty,
            "traded_qty",
        )

        before_queue = self.queue_ahead_qty
        queue_consumed = min(before_queue, traded_qty)

        self.queue_ahead_qty = before_queue - queue_consumed

        residual_trade = traded_qty - queue_consumed
        own_fill = min(self.remaining_qty, residual_trade)

        self.filled_qty += own_fill
        self._normalize()

        return QueueFill(
            traded_qty=traded_qty,
            queue_consumed_qty=queue_consumed,
            own_fill_qty=own_fill,
            queue_ahead_after=self.queue_ahead_qty,
            remaining_qty_after=self.remaining_qty,
        )

    def on_same_price_add(self, added_qty: float) -> None:
        """Observe quantity added at our price after arrival.

        Under FIFO it is behind our resting order, so queue ahead is
        unchanged.
        """

        _nonnegative(added_qty, "added_qty")

    def on_unexplained_size_decrease(self, decreased_qty: float) -> None:
        """Observe a book-size decrease without confirmed trade evidence.

        No queue-ahead credit is granted.  The decrease may have resulted
        from cancellation behind our order, so reducing queue ahead would
        create optimistic fill bias.
        """

        _nonnegative(decreased_qty, "decreased_qty")

    def reset_priority(self, displayed_qty_at_new_arrival: float) -> None:
        """Explicitly reset price-time priority.

        Intended for a future cancel/replace or price-change state machine.
        Remaining quantity is treated as newly arriving behind all displayed
        quantity at the new arrival instant.

        Existing fills remain fills; only queue priority is reset.
        """

        if self.is_filled:
            raise ValueError("cannot reset priority of a fully filled order")

        self.queue_ahead_qty = _nonnegative(
            displayed_qty_at_new_arrival,
            "displayed_qty_at_new_arrival",
        )
        self._normalize()

    def _normalize(self) -> None:
        if abs(self.queue_ahead_qty) <= _EPS:
            self.queue_ahead_qty = 0.0
        if abs(self.filled_qty - self.order_qty) <= _EPS:
            self.filled_qty = self.order_qty


def _nonnegative(
    value: float,
    name: str,
    *,
    strictly_positive: bool = False,
) -> float:
    value = float(value)

    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError(f"{name} must be finite")

    if strictly_positive:
        if value <= 0:
            raise ValueError(f"{name} must be > 0")
    elif value < 0:
        raise ValueError(f"{name} must be >= 0")

    return value
