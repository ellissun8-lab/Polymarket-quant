"""Deterministic cancel-fill race model v1.

Research/simulation only. No live order submission.

The model resolves only races with explicitly ordered venue timestamps.

Rules:
- fill_venue_ts < cancel_effective_ts:
    fill occurs first;
    if the order is only partially filled, cancellation then succeeds;
    if the fill completes the order, the order ends FILLED.
- cancel_effective_ts < fill_venue_ts:
    cancellation wins and no later fill is applied.
- fill_venue_ts == cancel_effective_ts:
    ordering is unobservable at this resolution; fail closed.

No same-timestamp ordering is guessed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from std0_quant.execution.execution_timestamps import (
    VenueRaceOrdering,
    compare_fill_vs_cancel,
)
from std0_quant.execution.order_state import (
    OrderStateMachine,
    OrderStatus,
)


class CancelFillOutcome(str, Enum):
    CANCEL_WON = "CANCEL_WON"
    FILL_THEN_CANCEL = "FILL_THEN_CANCEL"
    FILL_WON_COMPLETE = "FILL_WON_COMPLETE"


class AmbiguousCancelFillRace(ValueError):
    """Raised when fill and cancel confirmation timestamps are equal."""


@dataclass(frozen=True)
class CancelFillRaceResult:
    outcome: CancelFillOutcome
    fill_qty_applied: float
    final_status: OrderStatus
    filled_qty: float
    remaining_qty: float


def resolve_cancel_fill_race(
    *,
    order: OrderStateMachine,
    fill_qty: float,
    fill_venue_ts_ms: float,
    cancel_effective_ts_ms: float,
) -> CancelFillRaceResult:
    """Resolve one fill-vs-cancel-confirmation race.

    The order must already be CANCEL_REQUESTED.
    """

    if order.status != OrderStatus.CANCEL_REQUESTED:
        raise ValueError(
            "cancel-fill race requires CANCEL_REQUESTED order"
        )

    fill_qty = _positive_finite(
        fill_qty,
        "fill_qty",
    )
    fill_venue_ts_ms = _nonnegative_finite(
        fill_venue_ts_ms,
        "fill_venue_ts_ms",
    )
    cancel_effective_ts_ms = _nonnegative_finite(
        cancel_effective_ts_ms,
        "cancel_effective_ts_ms",
    )

    last_ts = order.last_event_ts_ms
    if last_ts is not None:
        if fill_venue_ts_ms < last_ts:
            raise ValueError(
                "fill venue timestamp precedes cancel request"
            )
        if cancel_effective_ts_ms < last_ts:
            raise ValueError(
                "cancel effective timestamp precedes cancel request"
            )

    ordering = compare_fill_vs_cancel(
        fill_venue_ts_ms=fill_venue_ts_ms,
        cancel_effective_ts_ms=cancel_effective_ts_ms,
    )

    if ordering == VenueRaceOrdering.AMBIGUOUS_SAME_TIMESTAMP:
        raise AmbiguousCancelFillRace(
            "fill venue timestamp and cancel effective timestamp "
            "are identical; ordering is ambiguous"
        )

    if ordering == VenueRaceOrdering.CANCEL_BEFORE_FILL:
        order.acknowledge_cancel(cancel_effective_ts_ms)

        return CancelFillRaceResult(
            outcome=CancelFillOutcome.CANCEL_WON,
            fill_qty_applied=0.0,
            final_status=order.status,
            filled_qty=order.filled_qty,
            remaining_qty=order.remaining_qty,
        )

    # fill venue execution precedes venue-effective cancellation.
    order.apply_fill(
        fill_qty,
        fill_venue_ts_ms,
    )

    if order.status == OrderStatus.FILLED:
        return CancelFillRaceResult(
            outcome=CancelFillOutcome.FILL_WON_COMPLETE,
            fill_qty_applied=fill_qty,
            final_status=order.status,
            filled_qty=order.filled_qty,
            remaining_qty=order.remaining_qty,
        )

    order.acknowledge_cancel(cancel_effective_ts_ms)

    return CancelFillRaceResult(
        outcome=CancelFillOutcome.FILL_THEN_CANCEL,
        fill_qty_applied=fill_qty,
        final_status=order.status,
        filled_qty=order.filled_qty,
        remaining_qty=order.remaining_qty,
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
