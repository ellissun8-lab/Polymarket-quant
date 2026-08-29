import copy
import json

import pytest

from std0_quant.execution.contracts import (
    OrderEventType,
    OrderIntent,
)
from std0_quant.execution.shadow_adapter import (
    SHADOW_REQUEST_SCHEMA_V1,
    ShadowExecutionAdapter,
)


class FakeTransport:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def submit(self, payload):
        self.requests.append(
            copy.deepcopy(payload)
        )
        return copy.deepcopy(
            self.response
        )


def intent():
    return OrderIntent(
        intent_id="intent-1",
        condition_id="m1",
        outcome="Up",
        side="BUY",
        qty=10,
        limit_price=0.50,
        time_in_force="GTC",
        decision_ts_ms=1001,
        market_data_ts_ms=1000,
        strategy_id="std0_candidate",
        strategy_version="v1",
        risk_policy_version="risk_v1",
    )


def ack_response():
    return {
        "mode": "SHADOW",
        "intent_id": "intent-1",
        "event": {
            "event_id": "event-1",
            "intent_id": "intent-1",
            "event_type": "VENUE_ACK",
            "receive_ts_ms": 1010,
            "venue_ts_ms": 1005,
            "venue_order_id": "shadow-order-1",
            "fill_qty": 0.0,
            "fill_price": None,
            "cumulative_filled_qty": 0.0,
            "remaining_qty": 10,
            "reason": None,
            "schema_version": "order_event_v1",
        },
    }


def test_shadow_request_contains_exact_versioned_intent():
    transport = FakeTransport(
        ack_response()
    )

    adapter = ShadowExecutionAdapter(
        transport
    )

    result = adapter.submit(
        intent=intent(),
        submit_ts_ms=1002,
    )

    assert len(transport.requests) == 1

    request = transport.requests[0]

    assert (
        request["schema_version"]
        == SHADOW_REQUEST_SCHEMA_V1
    )
    assert request["mode"] == "SHADOW"
    assert request["intent"] == intent().to_dict()

    # Must remain JSON serializable for process/RPC boundary.
    json.dumps(request)


def test_submit_emits_local_submitted_event():
    adapter = ShadowExecutionAdapter(
        FakeTransport(
            ack_response()
        )
    )

    result = adapter.submit(
        intent=intent(),
        submit_ts_ms=1002,
    )

    event = result.submitted_event

    assert event.event_type == OrderEventType.SUBMITTED
    assert event.intent_id == "intent-1"
    assert event.receive_ts_ms == pytest.approx(1002)
    assert event.venue_ts_ms is None
    assert event.remaining_qty == pytest.approx(10)
    assert event.reason == "SHADOW_ONLY"


def test_shadow_response_decodes_to_order_event():
    adapter = ShadowExecutionAdapter(
        FakeTransport(
            ack_response()
        )
    )

    result = adapter.submit(
        intent=intent(),
        submit_ts_ms=1002,
    )

    event = result.adapter_event

    assert event.event_type == OrderEventType.VENUE_ACK
    assert event.venue_ts_ms == pytest.approx(1005)
    assert event.receive_ts_ms == pytest.approx(1010)
    assert event.venue_order_id == "shadow-order-1"


def test_non_shadow_response_is_rejected():
    response = ack_response()
    response["mode"] = "LIVE"

    adapter = ShadowExecutionAdapter(
        FakeTransport(response)
    )

    with pytest.raises(ValueError):
        adapter.submit(
            intent=intent(),
            submit_ts_ms=1002,
        )


def test_response_intent_id_mismatch_fails_closed():
    response = ack_response()
    response["intent_id"] = "wrong-intent"

    adapter = ShadowExecutionAdapter(
        FakeTransport(response)
    )

    with pytest.raises(ValueError):
        adapter.submit(
            intent=intent(),
            submit_ts_ms=1002,
        )


def test_nested_event_intent_id_mismatch_fails_closed():
    response = ack_response()
    response["event"]["intent_id"] = "wrong-intent"

    adapter = ShadowExecutionAdapter(
        FakeTransport(response)
    )

    with pytest.raises(ValueError):
        adapter.submit(
            intent=intent(),
            submit_ts_ms=1002,
        )


def test_invalid_order_event_contract_fails_closed():
    response = ack_response()
    response["event"]["event_type"] = "FILLED"
    response["event"]["fill_qty"] = 0
    response["event"]["fill_price"] = None

    adapter = ShadowExecutionAdapter(
        FakeTransport(response)
    )

    with pytest.raises(ValueError):
        adapter.submit(
            intent=intent(),
            submit_ts_ms=1002,
        )


def test_submit_cannot_precede_decision():
    adapter = ShadowExecutionAdapter(
        FakeTransport(
            ack_response()
        )
    )

    with pytest.raises(ValueError):
        adapter.submit(
            intent=intent(),
            submit_ts_ms=1000,
        )


def test_intent_is_not_mutated_by_adapter():
    original = intent()
    before = original.to_json()

    adapter = ShadowExecutionAdapter(
        FakeTransport(
            ack_response()
        )
    )

    adapter.submit(
        intent=original,
        submit_ts_ms=1002,
    )

    assert original.to_json() == before


def test_fill_event_can_cross_shadow_boundary():
    response = {
        "mode": "SHADOW",
        "intent_id": "intent-1",
        "event": {
            "event_id": "fill-1",
            "intent_id": "intent-1",
            "event_type": "PARTIAL_FILL",
            "receive_ts_ms": 1011,
            "venue_ts_ms": 1006,
            "venue_order_id": "shadow-order-1",
            "fill_qty": 4,
            "fill_price": 0.50,
            "cumulative_filled_qty": 4,
            "remaining_qty": 6,
            "reason": None,
            "schema_version": "order_event_v1",
        },
    }

    adapter = ShadowExecutionAdapter(
        FakeTransport(response)
    )

    result = adapter.submit(
        intent=intent(),
        submit_ts_ms=1002,
    )

    event = result.adapter_event

    assert event.event_type == OrderEventType.PARTIAL_FILL
    assert event.fill_qty == pytest.approx(4)
    assert event.fill_price == pytest.approx(0.50)
    assert event.remaining_qty == pytest.approx(6)
