"""Execution timestamp semantics v1.

Research/simulation only. No live order submission.

This module distinguishes venue-effective time from client-observed time.

Order:
send
→ venue arrival
→ venue acceptance
→ client receives acknowledgement

Cancel:
send
→ venue arrival
→ venue cancellation becomes effective
→ client receives cancellation acknowledgement

Fill:
venue execution
→ client receives fill report

Cancel-fill ordering MUST compare:
    fill_venue_ts_ms
vs
    cancel_effective_ts_ms

It MUST NOT compare fill time with client cancel-ack receive time.
Same-timestamp ordering is ambiguous and is never guessed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math


_EPS = 1e-12


class VenueRaceOrdering(str, Enum):
    FILL_BEFORE_CANCEL = "FILL_BEFORE_CANCEL"
    CANCEL_BEFORE_FILL = "CANCEL_BEFORE_FILL"
    AMBIGUOUS_SAME_TIMESTAMP = "AMBIGUOUS_SAME_TIMESTAMP"


@dataclass(frozen=True)
class OrderTimestamps:
    order_send_ts_ms: float
    order_venue_arrival_ts_ms: float
    order_venue_accept_ts_ms: float
    order_ack_receive_ts_ms: float

    def __post_init__(self) -> None:
        values = _checked(
            self.order_send_ts_ms,
            self.order_venue_arrival_ts_ms,
            self.order_venue_accept_ts_ms,
            self.order_ack_receive_ts_ms,
        )
        _require_monotonic(
            values,
            "order timestamps",
        )


@dataclass(frozen=True)
class CancelTimestamps:
    cancel_send_ts_ms: float
    cancel_venue_arrival_ts_ms: float
    cancel_effective_ts_ms: float
    cancel_ack_receive_ts_ms: float

    def __post_init__(self) -> None:
        values = _checked(
            self.cancel_send_ts_ms,
            self.cancel_venue_arrival_ts_ms,
            self.cancel_effective_ts_ms,
            self.cancel_ack_receive_ts_ms,
        )
        _require_monotonic(
            values,
            "cancel timestamps",
        )


@dataclass(frozen=True)
class FillTimestamps:
    fill_venue_ts_ms: float
    fill_receive_ts_ms: float

    def __post_init__(self) -> None:
        values = _checked(
            self.fill_venue_ts_ms,
            self.fill_receive_ts_ms,
        )
        _require_monotonic(
            values,
            "fill timestamps",
        )


def compare_fill_vs_cancel(
    *,
    fill_venue_ts_ms: float,
    cancel_effective_ts_ms: float,
) -> VenueRaceOrdering:
    """Compare venue-effective timestamps only."""

    fill_ts, cancel_ts = _checked(
        fill_venue_ts_ms,
        cancel_effective_ts_ms,
    )

    if abs(fill_ts - cancel_ts) <= _EPS:
        return VenueRaceOrdering.AMBIGUOUS_SAME_TIMESTAMP

    if fill_ts < cancel_ts:
        return VenueRaceOrdering.FILL_BEFORE_CANCEL

    return VenueRaceOrdering.CANCEL_BEFORE_FILL


def _checked(*values: float) -> tuple[float, ...]:
    checked = tuple(float(value) for value in values)

    for value in checked:
        if not math.isfinite(value) or value < 0:
            raise ValueError(
                "timestamps must be finite and >= 0"
            )

    return checked


def _require_monotonic(
    values: tuple[float, ...],
    label: str,
) -> None:
    if any(
        later + _EPS < earlier
        for earlier, later in zip(
            values,
            values[1:],
        )
    ):
        raise ValueError(
            f"{label} must be monotonic"
        )
