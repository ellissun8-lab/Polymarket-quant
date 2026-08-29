"""Deterministic latency model v1.

Research/simulation only.  No live order submission.

The model separates the timestamp chain explicitly:

market event
→ local receive
→ feature ready
→ decision
→ order send
→ venue arrival
→ venue ack

Configured latency values are assumptions/calibration inputs until they are
replaced by measured production telemetry.  The model does not infer latency
from gaps in market data.
"""

from __future__ import annotations

from dataclasses import dataclass


_EPS = 1e-12


@dataclass(frozen=True)
class LatencyProfile:
    """Fixed per-stage latency budget in milliseconds."""

    market_data_ms: float
    feature_compute_ms: float
    decision_compute_ms: float
    order_send_ms: float
    outbound_network_ms: float
    venue_processing_ms: float = 0.0
    ack_network_ms: float = 0.0

    def __post_init__(self) -> None:
        for name in (
            "market_data_ms",
            "feature_compute_ms",
            "decision_compute_ms",
            "order_send_ms",
            "outbound_network_ms",
            "venue_processing_ms",
            "ack_network_ms",
        ):
            object.__setattr__(
                self,
                name,
                _nonnegative_finite(
                    getattr(self, name),
                    name,
                ),
            )

    @property
    def event_to_arrival_ms(self) -> float:
        return (
            self.market_data_ms
            + self.feature_compute_ms
            + self.decision_compute_ms
            + self.order_send_ms
            + self.outbound_network_ms
        )

    @property
    def event_to_ack_ms(self) -> float:
        return (
            self.event_to_arrival_ms
            + self.venue_processing_ms
            + self.ack_network_ms
        )


@dataclass(frozen=True)
class LatencyTimeline:
    """One simulated order-decision latency timeline."""

    market_event_ts_ms: float
    receive_ts_ms: float
    feature_ready_ts_ms: float
    decision_ts_ms: float
    order_send_ts_ms: float
    venue_arrival_ts_ms: float
    venue_ack_ts_ms: float

    def __post_init__(self) -> None:
        values = (
            self.market_event_ts_ms,
            self.receive_ts_ms,
            self.feature_ready_ts_ms,
            self.decision_ts_ms,
            self.order_send_ts_ms,
            self.venue_arrival_ts_ms,
            self.venue_ack_ts_ms,
        )

        checked = tuple(
            _nonnegative_finite(
                value,
                "timeline_timestamp",
            )
            for value in values
        )

        if any(
            later + _EPS < earlier
            for earlier, later in zip(
                checked,
                checked[1:],
            )
        ):
            raise ValueError(
                "latency timeline must be monotonic"
            )

    @property
    def event_to_arrival_ms(self) -> float:
        return (
            self.venue_arrival_ts_ms
            - self.market_event_ts_ms
        )

    @property
    def event_to_ack_ms(self) -> float:
        return (
            self.venue_ack_ts_ms
            - self.market_event_ts_ms
        )


@dataclass(frozen=True)
class FixedLatencyModel:
    """Deterministic latency model using one fixed profile."""

    profile: LatencyProfile

    def timeline(
        self,
        market_event_ts_ms: float,
    ) -> LatencyTimeline:
        event = _nonnegative_finite(
            market_event_ts_ms,
            "market_event_ts_ms",
        )

        receive = event + self.profile.market_data_ms
        feature_ready = (
            receive + self.profile.feature_compute_ms
        )
        decision = (
            feature_ready
            + self.profile.decision_compute_ms
        )
        order_send = (
            decision + self.profile.order_send_ms
        )
        venue_arrival = (
            order_send
            + self.profile.outbound_network_ms
        )
        venue_ack = (
            venue_arrival
            + self.profile.venue_processing_ms
            + self.profile.ack_network_ms
        )

        return LatencyTimeline(
            market_event_ts_ms=event,
            receive_ts_ms=receive,
            feature_ready_ts_ms=feature_ready,
            decision_ts_ms=decision,
            order_send_ts_ms=order_send,
            venue_arrival_ts_ms=venue_arrival,
            venue_ack_ts_ms=venue_ack,
        )

    def venue_arrival_from_send(
        self,
        send_ts_ms: float,
    ) -> float:
        send = _nonnegative_finite(
            send_ts_ms,
            "send_ts_ms",
        )
        return send + self.profile.outbound_network_ms

    def venue_ack_from_arrival(
        self,
        arrival_ts_ms: float,
    ) -> float:
        arrival = _nonnegative_finite(
            arrival_ts_ms,
            "arrival_ts_ms",
        )
        return (
            arrival
            + self.profile.venue_processing_ms
            + self.profile.ack_network_ms
        )


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
