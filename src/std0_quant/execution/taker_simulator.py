"""Aggressive/taker execution simulator v1.

Research/simulation only. No live order submission.

Scope:
- immediate matching at venue acceptance;
- IOC and FOK only;
- visible opposite-side liquidity only;
- no hidden liquidity;
- no future replenishment;
- insufficient IOC liquidity produces partial fill + EXPIRED remainder;
- insufficient FOK liquidity produces zero fill + EXPIRED.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from std0_quant.execution.execution_timestamps import (
    OrderTimestamps,
)
from std0_quant.execution.fill_model import (
    Fill,
    aggressive_sweep,
)
from std0_quant.execution.order_state import (
    OrderStateMachine,
    OrderStatus,
)


_EPS = 1e-12


class AggressiveTIF(str, Enum):
    IOC = "IOC"
    FOK = "FOK"


@dataclass(frozen=True)
class AggressiveSimulationEvent:
    event_type: str
    event_ts_ms: float
    status_after: str
    filled_qty_after: float


@dataclass(frozen=True)
class AggressiveSimulationResult:
    side: str
    tif: AggressiveTIF
    final_status: OrderStatus
    order_qty: float
    filled_qty: float
    remaining_qty: float
    fills: tuple[Fill, ...]
    events: tuple[AggressiveSimulationEvent, ...]
    order_timestamps: OrderTimestamps

    @property
    def average_fill_price(self) -> float | None:
        if not self.fills:
            return None

        qty = sum(fill.qty for fill in self.fills)

        return (
            sum(fill.price * fill.qty for fill in self.fills)
            / qty
        )


def simulate_aggressive_order(
    *,
    side: str,
    tif: AggressiveTIF | str,
    order_qty: float,
    levels: list[tuple[float, float]],
    order_timestamps: OrderTimestamps,
) -> AggressiveSimulationResult:
    """Simulate one IOC/FOK aggressive order."""

    side = str(side).upper()
    if side not in {"BUY", "SELL"}:
        raise ValueError("side must be BUY or SELL")

    try:
        tif = AggressiveTIF(tif)
    except ValueError as exc:
        raise ValueError("tif must be IOC or FOK") from exc

    order_qty = float(order_qty)
    if (
        order_qty != order_qty
        or order_qty in (float("inf"), float("-inf"))
        or order_qty <= 0
    ):
        raise ValueError(
            "order_qty must be finite and > 0"
        )

    order = OrderStateMachine(order_qty=order_qty)

    events: list[AggressiveSimulationEvent] = []

    order.mark_sent(
        order_timestamps.order_send_ts_ms
    )
    events.append(
        _event(
            "ORDER_SENT",
            order_timestamps.order_send_ts_ms,
            order,
        )
    )

    accept_ts = (
        order_timestamps.order_venue_accept_ts_ms
    )

    order.acknowledge(accept_ts)
    events.append(
        _event(
            "ORDER_VENUE_ACCEPTED",
            accept_ts,
            order,
        )
    )

    # Validate ordering/prices/quantities through the frozen sweep
    # implementation even for FOK.
    preview = aggressive_sweep(
        side=side,
        order_qty=order_qty,
        levels=levels,
    )

    if (
        tif == AggressiveTIF.FOK
        and not preview.is_fully_filled
    ):
        order.expire(accept_ts)

        events.append(
            _event(
                "FOK_EXPIRED_UNFILLED",
                accept_ts,
                order,
            )
        )

        return AggressiveSimulationResult(
            side=side,
            tif=tif,
            final_status=order.status,
            order_qty=order.order_qty,
            filled_qty=order.filled_qty,
            remaining_qty=order.remaining_qty,
            fills=(),
            events=tuple(events),
            order_timestamps=order_timestamps,
        )

    fills: list[Fill] = []

    for fill in preview.fills:
        order.apply_fill(
            fill.qty,
            accept_ts,
        )
        fills.append(fill)

        events.append(
            _event(
                "TAKER_FILL",
                accept_ts,
                order,
            )
        )

    if (
        order.status != OrderStatus.FILLED
        and tif == AggressiveTIF.IOC
    ):
        order.expire(accept_ts)

        events.append(
            _event(
                "IOC_REMAINDER_EXPIRED",
                accept_ts,
                order,
            )
        )

    if (
        tif == AggressiveTIF.FOK
        and order.status != OrderStatus.FILLED
    ):
        raise AssertionError(
            "FOK preview promised full fill but execution did not fill"
        )

    return AggressiveSimulationResult(
        side=side,
        tif=tif,
        final_status=order.status,
        order_qty=order.order_qty,
        filled_qty=order.filled_qty,
        remaining_qty=order.remaining_qty,
        fills=tuple(fills),
        events=tuple(events),
        order_timestamps=order_timestamps,
    )


def _event(
    event_type: str,
    event_ts_ms: float,
    order: OrderStateMachine,
) -> AggressiveSimulationEvent:
    return AggressiveSimulationEvent(
        event_type=event_type,
        event_ts_ms=event_ts_ms,
        status_after=order.status.value,
        filled_qty_after=order.filled_qty,
    )
