import copy

import pytest

from std0_quant.execution.clodds_mapping import (
    CloddsMappingError,
    map_order_intent_to_clodds_request,
)
from std0_quant.execution.contracts import OrderIntent


def make_intent(
    *,
    side="BUY",
    tif="GTC",
):
    return OrderIntent(
        intent_id="intent-1",
        condition_id="condition-1",
        outcome="Up",
        side=side,
        qty=12.5,
        limit_price=0.47,
        time_in_force=tif,
        decision_ts_ms=1001,
        market_data_ts_ms=1000,
        strategy_id="std0_candidate",
        strategy_version="v1",
        risk_policy_version="risk_v1",
    )


def test_buy_gtc_passive_mapping():
    result = map_order_intent_to_clodds_request(
        intent=make_intent(),
        token_id="token-up",
        post_only=True,
    )

    assert result == {
        "platform": "polymarket",
        "marketId": "condition-1",
        "tokenId": "token-up",
        "side": "buy",
        "price": 0.47,
        "size": 12.5,
        "orderType": "GTC",
        "postOnly": True,
    }


def test_sell_gtc_mapping_does_not_infer_post_only():
    result = map_order_intent_to_clodds_request(
        intent=make_intent(side="SELL"),
        token_id="token-down",
        post_only=False,
    )

    assert result["side"] == "sell"
    assert result["orderType"] == "GTC"
    assert result["postOnly"] is False


def test_ioc_maps_to_fak():
    result = map_order_intent_to_clodds_request(
        intent=make_intent(tif="IOC"),
        token_id="token-up",
        post_only=False,
    )

    assert result["orderType"] == "FAK"
    assert result["postOnly"] is False


def test_fok_maps_to_fok():
    result = map_order_intent_to_clodds_request(
        intent=make_intent(tif="FOK"),
        token_id="token-up",
        post_only=False,
    )

    assert result["orderType"] == "FOK"


@pytest.mark.parametrize("tif", ["IOC", "FOK"])
def test_immediate_order_cannot_be_post_only(tif):
    with pytest.raises(
        CloddsMappingError,
        match="post_only",
    ):
        map_order_intent_to_clodds_request(
            intent=make_intent(tif=tif),
            token_id="token-up",
            post_only=True,
        )


def test_blank_token_id_fails_closed():
    with pytest.raises(
        CloddsMappingError,
        match="token_id",
    ):
        map_order_intent_to_clodds_request(
            intent=make_intent(),
            token_id="",
            post_only=True,
        )


def test_post_only_must_be_explicit_bool():
    with pytest.raises(
        CloddsMappingError,
        match="post_only",
    ):
        map_order_intent_to_clodds_request(
            intent=make_intent(),
            token_id="token-up",
            post_only=1,  # type: ignore[arg-type]
        )


def test_mapping_has_only_expected_clodds_order_fields():
    result = map_order_intent_to_clodds_request(
        intent=make_intent(),
        token_id="token-up",
        post_only=True,
    )

    assert set(result) == {
        "platform",
        "marketId",
        "tokenId",
        "side",
        "price",
        "size",
        "orderType",
        "postOnly",
    }

    # No credential or execution-control fields cross this mapper.
    forbidden = {
        "privateKey",
        "apiKey",
        "secret",
        "funderAddress",
        "dryRun",
        "live",
        "execute",
    }
    assert forbidden.isdisjoint(result)


def test_mapping_does_not_mutate_order_intent():
    intent = make_intent()
    before = copy.deepcopy(intent.to_dict())

    map_order_intent_to_clodds_request(
        intent=intent,
        token_id="token-up",
        post_only=True,
    )

    assert intent.to_dict() == before
