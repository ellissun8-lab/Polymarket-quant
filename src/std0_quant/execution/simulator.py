"""Event-driven passive execution simulator v1.

Research/simulation only. No live order submission.

Scope:
- one passive maker order;
- deterministic venue timestamps;
- FIFO queue depletion;
- partial fills;
- cancel request and venue-effective cancellation;
- explicit fail-closed handling for same-timestamp ambiguity.

The simulator's OrderStateMachine ACKNOWLEDGED transition represents
VENUE ACCEPTANCE, not client receipt of the acknowledgement. Client receive
timestamps remain telemetry only.
"""

from __future__ import annotations

from dataclasses import dataclass

from std0_quant.execution.cancel_fill_race import (
    AmbiguousCancelFillRace,
)
from std0_quant.execution.execution_timestamps import (
    CancelTimestamps,
    OrderTimestamps,
)
from std0_quant.execution.fill_model import (
    Fill,
    passive_fill_from_confirmed_trade,
)
from std0_quant.execution.latency_model import (
    FixedLatencyModel,
)
from std0_quant.execution.order_state import (
    OrderStateMachine,
    OrderStatus,
)
from std0_quant.execution.queue_model import (
    FIFOQueueModel,
)


_EPS = 1e-12


@dataclass(frozen=True)
class ConfirmedTradeEvent:
    """Confirmed traded quantity at our resting price."""

    venue_ts_ms: float
    traded_qty: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "venue_ts_ms",
            _nonnegative_finite(
                self.venue_ts_ms,
                "venue_ts_ms",
            ),
        )
        object.__setattr__(
            self,
            "traded_qty",
            _positive_finite(
                self.traded_qty,
                "traded_qty",
            ),
        )


@dataclass(frozen=True)
class SimulationEvent:
    event_type: str
    event_ts_ms: float
    status_after: str
    queue_ahead_after: float
    filled_qty_after: float


@dataclass(frozen=True)
class PassiveSimulationResult:
    final_status: OrderStatus
    order_qty: float
    filled_qty: float
    remaining_qty: float
    queue_ahead_qty: float
    fills: tuple[Fill, ...]
    events: tuple[SimulationEvent, ...]
    order_timestamps: OrderTimestamps
    cancel_timestamps: CancelTimestamps | None

    @property
    def average_fill_price(self) -> float | None:
        if not self.fills:
            return None

        total_qty = sum(fill.qty for fill in self.fills)

        return (
            sum(fill.price * fill.qty for fill in self.fills)
            / total_qty
        )


def order_timestamps_from_latency(
    *,
    market_event_ts_ms: float,
    latency: FixedLatencyModel,
) -> OrderTimestamps:
    """Build venue/client order timestamps from fixed latency profile."""

    timeline = latency.timeline(market_event_ts_ms)

    venue_accept = (
        timeline.venue_arrival_ts_ms
        + latency.profile.venue_processing_ms
    )

    return OrderTimestamps(
        order_send_ts_ms=timeline.order_send_ts_ms,
        order_venue_arrival_ts_ms=timeline.venue_arrival_ts_ms,
        order_venue_accept_ts_ms=venue_accept,
        order_ack_receive_ts_ms=timeline.venue_ack_ts_ms,
    )


def cancel_timestamps_from_latency(
    *,
    cancel_send_ts_ms: float,
    latency: FixedLatencyModel,
) -> CancelTimestamps:
    """Build venue/client cancellation timestamps."""

    send = _nonnegative_finite(
        cancel_send_ts_ms,
        "cancel_send_ts_ms",
    )

    arrival = latency.venue_arrival_from_send(send)

    effective = (
        arrival
        + latency.profile.venue_processing_ms
    )

    ack_receive = latency.venue_ack_from_arrival(
        arrival
    )

    return CancelTimestamps(
        cancel_send_ts_ms=send,
        cancel_venue_arrival_ts_ms=arrival,
        cancel_effective_ts_ms=effective,
        cancel_ack_receive_ts_ms=ack_receive,
    )


def simulate_passive_order(
    *,
    order_qty: float,
    order_price: float,
    displayed_qty_at_accept: float,
    order_timestamps: OrderTimestamps,
    trades: list[ConfirmedTradeEvent],
    cancel_timestamps: CancelTimestamps | None = None,
) -> PassiveSimulationResult:
    """Run one deterministic passive maker order simulation."""

    order_qty = _positive_finite(
        order_qty,
        "order_qty",
    )
    order_price = _positive_finite(
        order_price,
        "order_price",
    )
    displayed_qty_at_accept = _nonnegative_finite(
        displayed_qty_at_accept,
        "displayed_qty_at_accept",
    )

    accept_ts = order_timestamps.order_venue_accept_ts_ms

    checked_trades = sorted(
        trades,
        key=lambda event: event.venue_ts_ms,
    )

    for trade in checked_trades:
        if trade.venue_ts_ms < accept_ts - _EPS:
            raise ValueError(
                "trade precedes order venue acceptance"
            )

        if abs(trade.venue_ts_ms - accept_ts) <= _EPS:
            raise ValueError(
                "trade timestamp equals order venue acceptance; "
                "queue ordering is ambiguous"
            )

    if cancel_timestamps is not None:
        if (
            cancel_timestamps.cancel_send_ts_ms
            < accept_ts - _EPS
        ):
            raise ValueError(
                "cancel request precedes order venue acceptance"
            )

        for trade in checked_trades:
            if (
                abs(
                    trade.venue_ts_ms
                    - cancel_timestamps.cancel_effective_ts_ms
                )
                <= _EPS
            ):
                raise AmbiguousCancelFillRace(
                    "trade timestamp equals cancel effective timestamp"
                )

    order = OrderStateMachine(
        order_qty=order_qty,
    )

    queue = FIFOQueueModel(
        order_qty=order_qty,
        queue_ahead_qty=displayed_qty_at_accept,
    )

    events: list[SimulationEvent] = []
    fills: list[Fill] = []

    order.mark_sent(
        order_timestamps.order_send_ts_ms
    )

    events.append(
        _event(
            "ORDER_SENT",
            order_timestamps.order_send_ts_ms,
            order,
            queue,
        )
    )

    # Internal execution state becomes active at venue acceptance.
    order.acknowledge(accept_ts)

    events.append(
        _event(
            "ORDER_VENUE_ACCEPTED",
            accept_ts,
            order,
            queue,
        )
    )

    timeline_events: list[
        tuple[float, int, str, object]
    ] = []

    for trade in checked_trades:
        timeline_events.append(
            (
                trade.venue_ts_ms,
                1,
                "TRADE",
                trade,
            )
        )

    if cancel_timestamps is not None:
        timeline_events.extend(
            [
                (
                    cancel_timestamps.cancel_send_ts_ms,
                    0,
                    "CANCEL_REQUEST",
                    cancel_timestamps,
                ),
                (
                    cancel_timestamps.cancel_effective_ts_ms,
                    2,
                    "CANCEL_EFFECTIVE",
                    cancel_timestamps,
                ),
            ]
        )

    timeline_events.sort(
        key=lambda item: (
            item[0],
            item[1],
        )
    )

    for event_ts, _, event_type, payload in timeline_events:
        if order.is_terminal:
            break

        if event_type == "CANCEL_REQUEST":
            order.request_cancel(event_ts)

            events.append(
                _event(
                    "CANCEL_REQUESTED",
                    event_ts,
                    order,
                    queue,
                )
            )
            continue

        if event_type == "CANCEL_EFFECTIVE":
            if order.status == OrderStatus.CANCEL_REQUESTED:
                order.acknowledge_cancel(event_ts)

                events.append(
                    _event(
                        "CANCEL_EFFECTIVE",
                        event_ts,
                        order,
                        queue,
                    )
                )
            continue

        trade = payload
        assert isinstance(
            trade,
            ConfirmedTradeEvent,
        )

        if order.status not in {
            OrderStatus.ACKNOWLEDGED,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.CANCEL_REQUESTED,
        }:
            continue

        fill_result = passive_fill_from_confirmed_trade(
            queue=queue,
            order_price=order_price,
            traded_qty=trade.traded_qty,
        )

        if fill_result.filled_qty > _EPS:
            order.apply_fill(
                fill_result.filled_qty,
                trade.venue_ts_ms,
            )
            fills.extend(fill_result.fills)

        events.append(
            _event(
                "CONFIRMED_TRADE",
                trade.venue_ts_ms,
                order,
                queue,
            )
        )

    return PassiveSimulationResult(
        final_status=order.status,
        order_qty=order.order_qty,
        filled_qty=order.filled_qty,
        remaining_qty=order.remaining_qty,
        queue_ahead_qty=queue.queue_ahead_qty,
        fills=tuple(fills),
        events=tuple(events),
        order_timestamps=order_timestamps,
        cancel_timestamps=cancel_timestamps,
    )


def _event(
    event_type: str,
    event_ts_ms: float,
    order: OrderStateMachine,
    queue: FIFOQueueModel,
) -> SimulationEvent:
    return SimulationEvent(
        event_type=event_type,
        event_ts_ms=event_ts_ms,
        status_after=order.status.value,
        queue_ahead_after=queue.queue_ahead_qty,
        filled_qty_after=order.filled_qty,
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
