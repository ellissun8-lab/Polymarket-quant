"""Hard shadow-only JSONL sidecar protocol v1.

This protocol freezes the process boundary before any live-capable
CloddsBot runtime is introduced.

Safety properties:
- SHADOW mode only;
- audited CloddsBot commit is pinned;
- pure OrderIntent -> Clodds OrderRequest mapping;
- no credentials;
- no dryRun/live/execute controls;
- no network;
- no execution-service import;
- request tampering fails closed.
"""

from __future__ import annotations

import math
from typing import Any

from std0_quant.execution.clodds_mapping import (
    CLODDS_MAPPING_VERSION_V1,
    map_order_intent_to_clodds_request,
)
from std0_quant.execution.contracts import (
    OrderEvent,
    OrderEventType,
    OrderIntent,
)


CLODDS_SHADOW_PROTOCOL_V1 = "clodds_shadow_jsonl_v1"

AUDITED_CLODDS_COMMIT_V1 = (
    "e71a5f635d99f453ef25ca1138d5f3ef7c4c686b"
)

_FORBIDDEN_KEYS = {
    "privateKey",
    "private_key",
    "apiKey",
    "api_key",
    "secret",
    "funderAddress",
    "funder_address",
    "dryRun",
    "dry_run",
    "live",
    "execute",
    "credentials",
}


class CloddsShadowProtocolError(ValueError):
    """Fail-closed protocol validation error."""


def build_shadow_request(
    *,
    intent: OrderIntent,
    token_id: str,
    post_only: bool,
) -> dict[str, Any]:
    """Build one versioned SHADOW-only JSONL request."""

    clodds_request = map_order_intent_to_clodds_request(
        intent=intent,
        token_id=token_id,
        post_only=post_only,
    )

    payload = {
        "protocol_version": CLODDS_SHADOW_PROTOCOL_V1,
        "mode": "SHADOW",
        "clodds_commit": AUDITED_CLODDS_COMMIT_V1,
        "mapping_version": CLODDS_MAPPING_VERSION_V1,
        "intent": intent.to_dict(),
        "clodds_request": clodds_request,
    }

    _reject_forbidden_keys(payload)

    return payload


def validate_shadow_request(
    payload: dict[str, Any],
) -> OrderIntent:
    """Validate a request and return the reconstructed OrderIntent."""

    if not isinstance(payload, dict):
        raise CloddsShadowProtocolError(
            "request must be a JSON object"
        )

    expected_keys = {
        "protocol_version",
        "mode",
        "clodds_commit",
        "mapping_version",
        "intent",
        "clodds_request",
    }

    if set(payload) != expected_keys:
        raise CloddsShadowProtocolError(
            "request fields do not match protocol v1"
        )

    if (
        payload.get("protocol_version")
        != CLODDS_SHADOW_PROTOCOL_V1
    ):
        raise CloddsShadowProtocolError(
            "unsupported shadow protocol version"
        )

    if payload.get("mode") != "SHADOW":
        raise CloddsShadowProtocolError(
            "non-SHADOW mode refused"
        )

    if (
        payload.get("clodds_commit")
        != AUDITED_CLODDS_COMMIT_V1
    ):
        raise CloddsShadowProtocolError(
            "CloddsBot commit mismatch"
        )

    if (
        payload.get("mapping_version")
        != CLODDS_MAPPING_VERSION_V1
    ):
        raise CloddsShadowProtocolError(
            "mapping version mismatch"
        )

    _reject_forbidden_keys(payload)

    intent_payload = payload.get("intent")

    if not isinstance(intent_payload, dict):
        raise CloddsShadowProtocolError(
            "intent must be a JSON object"
        )

    try:
        intent = OrderIntent.from_dict(intent_payload)
    except (TypeError, ValueError) as exc:
        raise CloddsShadowProtocolError(
            f"invalid OrderIntent: {exc}"
        ) from exc

    clodds_request = payload.get("clodds_request")

    if not isinstance(clodds_request, dict):
        raise CloddsShadowProtocolError(
            "clodds_request must be a JSON object"
        )

    token_id = clodds_request.get("tokenId")
    post_only = clodds_request.get("postOnly")

    try:
        expected_request = (
            map_order_intent_to_clodds_request(
                intent=intent,
                token_id=token_id,
                post_only=post_only,
            )
        )
    except (TypeError, ValueError) as exc:
        raise CloddsShadowProtocolError(
            f"invalid Clodds request mapping: {exc}"
        ) from exc

    if clodds_request != expected_request:
        raise CloddsShadowProtocolError(
            "clodds_request does not match OrderIntent"
        )

    return intent


def make_shadow_ack(
    *,
    payload: dict[str, Any],
    receive_ts_ms: float,
) -> dict[str, Any]:
    """Produce a synthetic ACK.

    This is not a venue ACK.  It is explicitly marked synthetic and has
    no venue timestamp.
    """

    intent = validate_shadow_request(payload)

    receive_ts_ms = float(receive_ts_ms)

    if (
        not math.isfinite(receive_ts_ms)
        or receive_ts_ms < 0
    ):
        raise CloddsShadowProtocolError(
            "receive_ts_ms must be finite and >= 0"
        )

    event = OrderEvent(
        event_id=f"{intent.intent_id}:shadow_ack",
        intent_id=intent.intent_id,
        event_type=OrderEventType.VENUE_ACK,
        receive_ts_ms=receive_ts_ms,
        venue_ts_ms=None,
        venue_order_id=f"shadow:{intent.intent_id}",
        fill_qty=0.0,
        fill_price=None,
        cumulative_filled_qty=0.0,
        remaining_qty=intent.qty,
        reason="SHADOW_SYNTHETIC_ACK",
    )

    return {
        "protocol_version": CLODDS_SHADOW_PROTOCOL_V1,
        "mode": "SHADOW",
        "intent_id": intent.intent_id,
        "clodds_commit": AUDITED_CLODDS_COMMIT_V1,
        "event": event.to_dict(),
    }


def _reject_forbidden_keys(
    value: Any,
) -> None:
    if isinstance(value, dict):
        forbidden = _FORBIDDEN_KEYS.intersection(value)

        if forbidden:
            names = ",".join(sorted(forbidden))
            raise CloddsShadowProtocolError(
                f"forbidden execution-control fields: {names}"
            )

        for nested in value.values():
            _reject_forbidden_keys(nested)

    elif isinstance(value, list):
        for nested in value:
            _reject_forbidden_keys(nested)
