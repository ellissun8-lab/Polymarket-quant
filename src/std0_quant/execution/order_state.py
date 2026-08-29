"""Deterministic order state machine v1.

Research/simulation only. No live order submission.

Important:
- CANCEL_REQUESTED is not terminal.
- Fills may still arrive while cancellation is in flight.
- CANCELLED means venue cancellation has been confirmed.
- Event timestamps must be monotonic.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


_EPS = 1e-12


class OrderStatus(str, Enum):
    NEW = "NEW"
    SENT = "SENT"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    FILLED = "FILLED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


TERMINAL_STATUSES = {
    OrderStatus.CANCELLED,
    OrderStatus.FILLED,
    OrderStatus.EXPIRED,
    OrderStatus.REJECTED,
}


@dataclass(frozen=True)
class OrderEvent:
    event_type: str
    event_ts_ms: float
    status_before: OrderStatus
    status_after: OrderStatus
    fill_qty: float = 0.0


@dataclass
class OrderStateMachine:
    order_qty: float
    status: OrderStatus = OrderStatus.NEW
    filled_qty: float = 0.0
    last_event_ts_ms: float | None = None

    def __post_init__(self) -> None:
        self.order_qty = _positive_finite(
            self.order_qty,
            "order_qty",
        )
        self.filled_qty = _nonnegative_finite(
            self.filled_qty,
            "filled_qty",
        )

        if self.filled_qty > self.order_qty + _EPS:
            raise ValueError(
                "filled_qty cannot exceed order_qty"
            )

        if (
            self.status == OrderStatus.FILLED
            and abs(self.filled_qty - self.order_qty) > _EPS
        ):
            raise ValueError(
                "FILLED status requires filled_qty == order_qty"
            )

        if (
            self.status == OrderStatus.PARTIALLY_FILLED
            and (
                self.filled_qty <= _EPS
                or self.filled_qty >= self.order_qty - _EPS
            )
        ):
            raise ValueError(
                "PARTIALLY_FILLED requires "
                "0 < filled_qty < order_qty"
            )

        if self.last_event_ts_ms is not None:
            self.last_event_ts_ms = _nonnegative_finite(
                self.last_event_ts_ms,
                "last_event_ts_ms",
            )

        self._normalize()

    @property
    def remaining_qty(self) -> float:
        return max(
            0.0,
            self.order_qty - self.filled_qty,
        )

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def mark_sent(self, event_ts_ms: float) -> OrderEvent:
        return self._transition(
            event_type="SENT",
            event_ts_ms=event_ts_ms,
            allowed={OrderStatus.NEW},
            target=OrderStatus.SENT,
        )

    def acknowledge(
        self,
        event_ts_ms: float,
    ) -> OrderEvent:
        return self._transition(
            event_type="ACKNOWLEDGED",
            event_ts_ms=event_ts_ms,
            allowed={OrderStatus.SENT},
            target=OrderStatus.ACKNOWLEDGED,
        )

    def reject(
        self,
        event_ts_ms: float,
    ) -> OrderEvent:
        return self._transition(
            event_type="REJECTED",
            event_ts_ms=event_ts_ms,
            allowed={OrderStatus.SENT},
            target=OrderStatus.REJECTED,
        )

    def expire(
        self,
        event_ts_ms: float,
    ) -> OrderEvent:
        """Venue expires an unfilled remainder, e.g. IOC/FOK."""

        return self._transition(
            event_type="EXPIRED",
            event_ts_ms=event_ts_ms,
            allowed={
                OrderStatus.ACKNOWLEDGED,
                OrderStatus.PARTIALLY_FILLED,
            },
            target=OrderStatus.EXPIRED,
        )

    def request_cancel(
        self,
        event_ts_ms: float,
    ) -> OrderEvent:
        return self._transition(
            event_type="CANCEL_REQUESTED",
            event_ts_ms=event_ts_ms,
            allowed={
                OrderStatus.ACKNOWLEDGED,
                OrderStatus.PARTIALLY_FILLED,
            },
            target=OrderStatus.CANCEL_REQUESTED,
        )

    def acknowledge_cancel(
        self,
        event_ts_ms: float,
    ) -> OrderEvent:
        return self._transition(
            event_type="CANCELLED",
            event_ts_ms=event_ts_ms,
            allowed={OrderStatus.CANCEL_REQUESTED},
            target=OrderStatus.CANCELLED,
        )

    def apply_fill(
        self,
        fill_qty: float,
        event_ts_ms: float,
    ) -> OrderEvent:
        fill_qty = _positive_finite(
            fill_qty,
            "fill_qty",
        )
        ts = self._checked_ts(event_ts_ms)

        allowed = {
            OrderStatus.ACKNOWLEDGED,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.CANCEL_REQUESTED,
        }

        if self.status not in allowed:
            raise ValueError(
                f"fill not allowed from {self.status.value}"
            )

        if fill_qty > self.remaining_qty + _EPS:
            raise ValueError(
                "fill_qty exceeds remaining_qty"
            )

        before = self.status
        self.filled_qty += fill_qty
        self._normalize()

        if self.remaining_qty <= _EPS:
            self.status = OrderStatus.FILLED
        elif before == OrderStatus.CANCEL_REQUESTED:
            # Cancellation is still in flight.
            self.status = OrderStatus.CANCEL_REQUESTED
        else:
            self.status = OrderStatus.PARTIALLY_FILLED

        self.last_event_ts_ms = ts

        return OrderEvent(
            event_type="FILL",
            event_ts_ms=ts,
            status_before=before,
            status_after=self.status,
            fill_qty=fill_qty,
        )

    def _transition(
        self,
        *,
        event_type: str,
        event_ts_ms: float,
        allowed: set[OrderStatus],
        target: OrderStatus,
    ) -> OrderEvent:
        ts = self._checked_ts(event_ts_ms)

        if self.status not in allowed:
            raise ValueError(
                f"{event_type} not allowed from "
                f"{self.status.value}"
            )

        before = self.status
        self.status = target
        self.last_event_ts_ms = ts

        return OrderEvent(
            event_type=event_type,
            event_ts_ms=ts,
            status_before=before,
            status_after=target,
        )

    def _checked_ts(
        self,
        event_ts_ms: float,
    ) -> float:
        ts = _nonnegative_finite(
            event_ts_ms,
            "event_ts_ms",
        )

        if (
            self.last_event_ts_ms is not None
            and ts + _EPS < self.last_event_ts_ms
        ):
            raise ValueError(
                "event timestamps must be monotonic"
            )

        return ts

    def _normalize(self) -> None:
        if abs(self.filled_qty) <= _EPS:
            self.filled_qty = 0.0

        if (
            abs(self.filled_qty - self.order_qty)
            <= _EPS
        ):
            self.filled_qty = self.order_qty


def _positive_finite(
    value: float,
    name: str,
) -> float:
    value = float(value)

    if not _is_finite(value) or value <= 0:
        raise ValueError(
            f"{name} must be finite and > 0"
        )

    return value


def _nonnegative_finite(
    value: float,
    name: str,
) -> float:
    value = float(value)

    if not _is_finite(value) or value < 0:
        raise ValueError(
            f"{name} must be finite and >= 0"
        )

    return value


def _is_finite(value: float) -> bool:
    return (
        value == value
        and value
        not in (
            float("inf"),
            float("-inf"),
        )
    )
