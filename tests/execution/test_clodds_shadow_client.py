import copy
from pathlib import Path
import sys

import pytest

from std0_quant.execution.clodds_shadow_client import (
    CloddsShadowClient,
    CloddsShadowClientError,
)
from std0_quant.execution.contracts import (
    OrderEventType,
    OrderIntent,
)
from std0_quant.execution.jsonl_process_transport import (
    JsonlProcessTransport,
    make_transport_config,
)


ROOT = Path(__file__).resolve().parents[2]
SIDECAR = (
    ROOT
    / "scripts"
    / "run_clodds_shadow_sidecar.py"
)

TOKENS = [
    ("token-up", "Up"),
    ("token-down", "Down"),
]


def intent(
    *,
    intent_id="intent-1",
    outcome="Up",
    tif="GTC",
):
    return OrderIntent(
        intent_id=intent_id,
        condition_id="condition-1",
        outcome=outcome,
        side="BUY",
        qty=10,
        limit_price=0.50,
        time_in_force=tif,
        decision_ts_ms=1001,
        market_data_ts_ms=1000,
        strategy_id="std0_candidate",
        strategy_version="v1",
        risk_policy_version="risk_v1",
    )


def process_transport():
    return JsonlProcessTransport(
        make_transport_config(
            [
                sys.executable,
                str(SIDECAR),
            ],
            cwd=ROOT,
            timeout_seconds=2.0,
        )
    )


def test_full_shadow_pipeline_roundtrip():
    transport = process_transport()

    try:
        client = CloddsShadowClient(
            transport
        )

        event = client.submit(
            intent=intent(),
            market_condition_id="condition-1",
            tokens=TOKENS,
            post_only=True,
        )
    finally:
        transport.close()

    assert event.intent_id == "intent-1"
    assert event.event_type == OrderEventType.VENUE_ACK
    assert event.venue_ts_ms is None
    assert event.venue_order_id == "shadow:intent-1"
    assert event.remaining_qty == pytest.approx(10)
    assert event.reason == "SHADOW_SYNTHETIC_ACK"


def test_down_outcome_resolves_down_token():
    class CaptureTransport:
        def __init__(self):
            self.payload = None

        def submit(self, payload):
            self.payload = copy.deepcopy(payload)
            return {
                "protocol_version": "clodds_shadow_jsonl_v1",
                "mode": "SHADOW",
                "intent_id": "intent-down",
                "clodds_commit": (
                    "e71a5f635d99f453ef25ca1138d5f3ef7c4c686b"
                ),
                "event": {
                    "event_id": "intent-down:shadow_ack",
                    "intent_id": "intent-down",
                    "event_type": "VENUE_ACK",
                    "receive_ts_ms": 2000,
                    "venue_ts_ms": None,
                    "venue_order_id": "shadow:intent-down",
                    "fill_qty": 0.0,
                    "fill_price": None,
                    "cumulative_filled_qty": 0.0,
                    "remaining_qty": 10,
                    "reason": "SHADOW_SYNTHETIC_ACK",
                    "schema_version": "order_event_v1",
                },
            }

    transport = CaptureTransport()
    client = CloddsShadowClient(transport)

    event = client.submit(
        intent=intent(
            intent_id="intent-down",
            outcome="Down",
        ),
        market_condition_id="condition-1",
        tokens=TOKENS,
        post_only=True,
    )

    assert (
        transport.payload["clodds_request"]["tokenId"]
        == "token-down"
    )
    assert event.intent_id == "intent-down"


def test_condition_mismatch_fails_before_transport():
    class MustNotCallTransport:
        def submit(self, payload):
            raise AssertionError(
                "transport must not be called"
            )

    client = CloddsShadowClient(
        MustNotCallTransport()
    )

    with pytest.raises(
        ValueError,
        match="condition_id",
    ):
        client.submit(
            intent=intent(),
            market_condition_id="wrong-condition",
            tokens=TOKENS,
            post_only=True,
        )


def test_unknown_outcome_fails_before_transport():
    class MustNotCallTransport:
        def submit(self, payload):
            raise AssertionError(
                "transport must not be called"
            )

    client = CloddsShadowClient(
        MustNotCallTransport()
    )

    with pytest.raises(
        ValueError,
        match="outcome",
    ):
        client.submit(
            intent=intent(
                outcome="Unknown",
            ),
            market_condition_id="condition-1",
            tokens=TOKENS,
            post_only=True,
        )


def test_ioc_post_only_fails_before_transport():
    class MustNotCallTransport:
        def submit(self, payload):
            raise AssertionError(
                "transport must not be called"
            )

    client = CloddsShadowClient(
        MustNotCallTransport()
    )

    with pytest.raises(
        ValueError,
        match="post_only",
    ):
        client.submit(
            intent=intent(tif="IOC"),
            market_condition_id="condition-1",
            tokens=TOKENS,
            post_only=True,
        )


def test_sidecar_protocol_rejection_becomes_client_error():
    class RejectTransport:
        def submit(self, payload):
            return {
                "protocol_version": "clodds_shadow_jsonl_v1",
                "mode": "SHADOW",
                "error": {
                    "type": "PROTOCOL_REJECT",
                    "message": "test rejection",
                },
            }

    client = CloddsShadowClient(
        RejectTransport()
    )

    with pytest.raises(
        CloddsShadowClientError,
        match="PROTOCOL_REJECT",
    ):
        client.submit(
            intent=intent(),
            market_condition_id="condition-1",
            tokens=TOKENS,
            post_only=True,
        )


def test_response_intent_mismatch_fails_closed():
    class WrongIntentTransport:
        def submit(self, payload):
            return {
                "protocol_version": "clodds_shadow_jsonl_v1",
                "mode": "SHADOW",
                "intent_id": "wrong",
                "clodds_commit": (
                    "e71a5f635d99f453ef25ca1138d5f3ef7c4c686b"
                ),
                "event": {
                    "event_id": "wrong:shadow_ack",
                    "intent_id": "wrong",
                    "event_type": "VENUE_ACK",
                    "receive_ts_ms": 2000,
                    "venue_ts_ms": None,
                    "venue_order_id": "shadow:wrong",
                    "fill_qty": 0,
                    "fill_price": None,
                    "cumulative_filled_qty": 0,
                    "remaining_qty": 10,
                    "reason": "SHADOW_SYNTHETIC_ACK",
                    "schema_version": "order_event_v1",
                },
            }

    client = CloddsShadowClient(
        WrongIntentTransport()
    )

    with pytest.raises(
        CloddsShadowClientError,
        match="intent_id mismatch",
    ):
        client.submit(
            intent=intent(),
            market_condition_id="condition-1",
            tokens=TOKENS,
            post_only=True,
        )


def test_response_commit_mismatch_fails_closed():
    class WrongCommitTransport:
        def submit(self, payload):
            return {
                "protocol_version": "clodds_shadow_jsonl_v1",
                "mode": "SHADOW",
                "intent_id": "intent-1",
                "clodds_commit": "wrong",
                "event": {},
            }

    client = CloddsShadowClient(
        WrongCommitTransport()
    )

    with pytest.raises(
        CloddsShadowClientError,
        match="commit mismatch",
    ):
        client.submit(
            intent=intent(),
            market_condition_id="condition-1",
            tokens=TOKENS,
            post_only=True,
        )


def test_shadow_response_cannot_claim_venue_time():
    class FakeVenueTimeTransport:
        def submit(self, payload):
            return {
                "protocol_version": "clodds_shadow_jsonl_v1",
                "mode": "SHADOW",
                "intent_id": "intent-1",
                "clodds_commit": (
                    "e71a5f635d99f453ef25ca1138d5f3ef7c4c686b"
                ),
                "event": {
                    "event_id": "intent-1:shadow_ack",
                    "intent_id": "intent-1",
                    "event_type": "VENUE_ACK",
                    "receive_ts_ms": 2000,
                    "venue_ts_ms": 1999,
                    "venue_order_id": "shadow:intent-1",
                    "fill_qty": 0,
                    "fill_price": None,
                    "cumulative_filled_qty": 0,
                    "remaining_qty": 10,
                    "reason": "SHADOW_SYNTHETIC_ACK",
                    "schema_version": "order_event_v1",
                },
            }

    client = CloddsShadowClient(
        FakeVenueTimeTransport()
    )

    with pytest.raises(
        CloddsShadowClientError,
        match="venue timestamp",
    ):
        client.submit(
            intent=intent(),
            market_condition_id="condition-1",
            tokens=TOKENS,
            post_only=True,
        )


def test_client_does_not_mutate_intent_or_tokens():
    original_intent = intent()
    original_tokens = copy.deepcopy(TOKENS)
    before_intent = original_intent.to_dict()

    transport = process_transport()

    try:
        client = CloddsShadowClient(
            transport
        )

        client.submit(
            intent=original_intent,
            market_condition_id="condition-1",
            tokens=TOKENS,
            post_only=True,
        )
    finally:
        transport.close()

    assert original_intent.to_dict() == before_intent
    assert TOKENS == original_tokens
