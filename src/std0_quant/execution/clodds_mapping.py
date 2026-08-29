"""Pure std0-quant -> CloddsBot OrderRequest mapping v1.

This module performs data translation only.

It deliberately:
- performs no network access;
- does not import CloddsBot;
- does not instantiate an execution service;
- does not read credentials;
- does not submit or cancel orders;
- does not infer passive/maker intent from GTC.

Pinned CloddsBot interface audited separately:
OrderType = GTC | FOK | GTD | FAK
FAK is the CloddsBot equivalent used for IOC semantics.
"""

from __future__ import annotations

from typing import Any

from std0_quant.execution.contracts import (
    OrderIntent,
    OrderSide,
    TimeInForce,
)


CLODDS_MAPPING_VERSION_V1 = "clodds_order_request_mapping_v1"


class CloddsMappingError(ValueError):
    """Fail-closed mapping error."""


_TIF_MAP = {
    TimeInForce.GTC: "GTC",
    TimeInForce.IOC: "FAK",
    TimeInForce.FOK: "FOK",
}


def map_order_intent_to_clodds_request(
    *,
    intent: OrderIntent,
    token_id: str,
    post_only: bool,
) -> dict[str, Any]:
    """Translate a validated OrderIntent into CloddsBot OrderRequest data.

    ``post_only`` is intentionally explicit.

    GTC does NOT imply maker/passive behavior.  The calling execution
    pipeline must decide whether the order is passive or aggressive.
    """

    if not isinstance(token_id, str) or not token_id.strip():
        raise CloddsMappingError(
            "token_id must be a non-empty string"
        )

    if type(post_only) is not bool:
        raise CloddsMappingError(
            "post_only must be bool"
        )

    if (
        intent.time_in_force
        in {TimeInForce.IOC, TimeInForce.FOK}
        and post_only
    ):
        raise CloddsMappingError(
            "IOC/FOK cannot be mapped with post_only=True"
        )

    side = {
        OrderSide.BUY: "buy",
        OrderSide.SELL: "sell",
    }[intent.side]

    order_type = _TIF_MAP[intent.time_in_force]

    return {
        "platform": "polymarket",
        "marketId": intent.condition_id,
        "tokenId": token_id,
        "side": side,
        "price": intent.limit_price,
        "size": intent.qty,
        "orderType": order_type,
        "postOnly": post_only,
    }
