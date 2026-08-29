"""Shadow-only execution adapter boundary v1.

This is the std0-quant side of the future CloddsBot sidecar boundary.

IMPORTANT:
- SHADOW ONLY;
- no network transport is implemented here;
- no credentials/private keys;
- no Polymarket order submission;
- caller must inject a transport;
- LIVE mode is not represented or accepted.

The adapter serializes OrderIntent v1 and validates OrderEvent v1 responses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
import math

from std0_quant.execution.contracts import (
    OrderEvent,
    OrderEventType,
    OrderIntent,
)


SHADOW_REQUEST_SCHEMA_V1 = "shadow_execution_request_v1"


class ShadowTransport(Protocol):
    """Injected transport interface.

    A future CloddsBot sidecar transport may implement this protocol.
    """

    def submit(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class ShadowSubmission:
    request_payload: dict[str, Any]
    submitted_event: OrderEvent
    adapter_event: OrderEvent


class ShadowExecutionAdapter:
    """Strict shadow-only adapter."""

    def __init__(
        self,
        transport: ShadowTransport,
    ) -> None:
        self._transport = transport

    def encode_intent(
        self,
        intent: OrderIntent,
    ) -> dict[str, Any]:
        return {
            "schema_version": SHADOW_REQUEST_SCHEMA_V1,
            "mode": "SHADOW",
            "intent": intent.to_dict(),
        }

    def decode_event(
        self,
        *,
        intent: OrderIntent,
        payload: dict[str, Any],
    ) -> OrderEvent:
        if not isinstance(payload, dict):
            raise ValueError(
                "shadow response must be an object"
            )

        if payload.get("mode") != "SHADOW":
            raise ValueError(
                "shadow adapter refuses non-SHADOW response"
            )

        response_intent_id = payload.get("intent_id")

        if response_intent_id != intent.intent_id:
            raise ValueError(
                "shadow response intent_id mismatch"
            )

        event_payload = payload.get("event")

        if not isinstance(event_payload, dict):
            raise ValueError(
                "shadow response requires event object"
            )

        event = OrderEvent.from_dict(event_payload)

        if event.intent_id != intent.intent_id:
            raise ValueError(
                "OrderEvent intent_id mismatch"
            )

        return event

    def submit(
        self,
        *,
        intent: OrderIntent,
        submit_ts_ms: float,
    ) -> ShadowSubmission:
        submit_ts_ms = _nonnegative_finite(
            submit_ts_ms,
            "submit_ts_ms",
        )

        if submit_ts_ms < intent.decision_ts_ms:
            raise ValueError(
                "submit_ts_ms cannot precede decision_ts_ms"
            )

        request_payload = self.encode_intent(
            intent
        )

        submitted_event = OrderEvent(
            event_id=(
                f"{intent.intent_id}:submitted:"
                f"{_timestamp_id(submit_ts_ms)}"
            ),
            intent_id=intent.intent_id,
            event_type=OrderEventType.SUBMITTED,
            receive_ts_ms=submit_ts_ms,
            venue_ts_ms=None,
            venue_order_id=None,
            fill_qty=0.0,
            fill_price=None,
            cumulative_filled_qty=0.0,
            remaining_qty=intent.qty,
            reason="SHADOW_ONLY",
        )

        response = self._transport.submit(
            request_payload
        )

        adapter_event = self.decode_event(
            intent=intent,
            payload=response,
        )

        return ShadowSubmission(
            request_payload=request_payload,
            submitted_event=submitted_event,
            adapter_event=adapter_event,
        )


def _timestamp_id(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return format(float(value), ".12g")


def _nonnegative_finite(
    value: float,
    name: str,
) -> float:
    value = float(value)

    if not math.isfinite(value) or value < 0:
        raise ValueError(
            f"{name} must be finite and >= 0"
        )

    return value
