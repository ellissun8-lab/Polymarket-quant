"""Integrated Clodds shadow client v1.

This is the highest shadow-only execution boundary so far.

Pipeline:
    OrderIntent
    -> market identifier resolution
    -> Clodds OrderRequest mapping
    -> hard SHADOW protocol
    -> injected JSONL transport
    -> validated OrderEvent

No live execution capability exists in this module.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol

from std0_quant.execution.clodds_shadow_protocol import (
    AUDITED_CLODDS_COMMIT_V1,
    CLODDS_SHADOW_PROTOCOL_V1,
    build_shadow_request,
)
from std0_quant.execution.contracts import (
    OrderEvent,
    OrderIntent,
)
from std0_quant.execution.market_identifier import (
    resolve_token_id,
)


class ShadowRequestTransport(Protocol):
    def submit(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        ...


class CloddsShadowClientError(RuntimeError):
    """Fail-closed integrated shadow-client error."""


class CloddsShadowClient:
    def __init__(
        self,
        transport: ShadowRequestTransport,
    ) -> None:
        self._transport = transport

    def submit(
        self,
        *,
        intent: OrderIntent,
        market_condition_id: str,
        tokens: Iterable[tuple[str, str]],
        post_only: bool,
    ) -> OrderEvent:
        token_id = resolve_token_id(
            intent_condition_id=intent.condition_id,
            intent_outcome=intent.outcome,
            market_condition_id=market_condition_id,
            tokens=tokens,
        )

        request = build_shadow_request(
            intent=intent,
            token_id=token_id,
            post_only=post_only,
        )

        response = self._transport.submit(request)

        return self._decode_response(
            intent=intent,
            response=response,
        )

    def _decode_response(
        self,
        *,
        intent: OrderIntent,
        response: dict[str, Any],
    ) -> OrderEvent:
        if not isinstance(response, dict):
            raise CloddsShadowClientError(
                "shadow response must be a JSON object"
            )

        if (
            response.get("protocol_version")
            != CLODDS_SHADOW_PROTOCOL_V1
        ):
            raise CloddsShadowClientError(
                "shadow response protocol mismatch"
            )

        if response.get("mode") != "SHADOW":
            raise CloddsShadowClientError(
                "non-SHADOW response refused"
            )

        if "error" in response:
            error = response["error"]

            if isinstance(error, dict):
                kind = error.get(
                    "type",
                    "UNKNOWN",
                )
                message = error.get(
                    "message",
                    "",
                )
                raise CloddsShadowClientError(
                    f"sidecar rejected request: "
                    f"{kind}: {message}"
                )

            raise CloddsShadowClientError(
                "sidecar returned malformed error"
            )

        expected_keys = {
            "protocol_version",
            "mode",
            "intent_id",
            "clodds_commit",
            "event",
        }

        if set(response) != expected_keys:
            raise CloddsShadowClientError(
                "shadow response fields do not match protocol v1"
            )

        if (
            response.get("clodds_commit")
            != AUDITED_CLODDS_COMMIT_V1
        ):
            raise CloddsShadowClientError(
                "shadow response CloddsBot commit mismatch"
            )

        if response.get("intent_id") != intent.intent_id:
            raise CloddsShadowClientError(
                "shadow response intent_id mismatch"
            )

        event_payload = response.get("event")

        if not isinstance(event_payload, dict):
            raise CloddsShadowClientError(
                "shadow response event must be an object"
            )

        try:
            event = OrderEvent.from_dict(
                event_payload
            )
        except (TypeError, ValueError) as exc:
            raise CloddsShadowClientError(
                f"invalid OrderEvent: {exc}"
            ) from exc

        if event.intent_id != intent.intent_id:
            raise CloddsShadowClientError(
                "OrderEvent intent_id mismatch"
            )

        if event.venue_ts_ms is not None:
            raise CloddsShadowClientError(
                "shadow synthetic event cannot claim venue timestamp"
            )

        if event.reason != "SHADOW_SYNTHETIC_ACK":
            raise CloddsShadowClientError(
                "unexpected shadow event reason"
            )

        return event
