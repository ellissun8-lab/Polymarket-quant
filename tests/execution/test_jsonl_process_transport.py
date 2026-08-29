import copy
from pathlib import Path
import sys

import pytest

from std0_quant.execution.clodds_shadow_protocol import (
    build_shadow_request,
)
from std0_quant.execution.contracts import OrderIntent
from std0_quant.execution.jsonl_process_transport import (
    JsonlProcessTransport,
    JsonlProcessTransportError,
    make_transport_config,
)


ROOT = Path(__file__).resolve().parents[2]
SIDECAR = (
    ROOT
    / "scripts"
    / "run_clodds_shadow_sidecar.py"
)


def intent(intent_id="intent-1"):
    return OrderIntent(
        intent_id=intent_id,
        condition_id="condition-1",
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


def payload(intent_id="intent-1"):
    return build_shadow_request(
        intent=intent(intent_id),
        token_id="token-up",
        post_only=True,
    )


def transport(timeout_seconds=2.0):
    return JsonlProcessTransport(
        make_transport_config(
            [
                sys.executable,
                str(SIDECAR),
            ],
            cwd=ROOT,
            timeout_seconds=timeout_seconds,
        )
    )


def test_process_transport_roundtrip():
    with transport() as client:
        response = client.submit(
            payload()
        )

    assert response["mode"] == "SHADOW"
    assert response["intent_id"] == "intent-1"
    assert (
        response["event"]["reason"]
        == "SHADOW_SYNTHETIC_ACK"
    )


def test_persistent_process_handles_multiple_requests():
    with transport() as client:
        first = client.submit(
            payload("intent-1")
        )
        second = client.submit(
            payload("intent-2")
        )

    assert first["intent_id"] == "intent-1"
    assert second["intent_id"] == "intent-2"


def test_live_request_is_rejected_by_sidecar():
    bad = payload()
    bad["mode"] = "LIVE"

    with transport() as client:
        response = client.submit(bad)

    assert response["mode"] == "SHADOW"
    assert response["error"]["type"] == "PROTOCOL_REJECT"


def test_forbidden_credentials_are_rejected():
    bad = payload()
    bad["clodds_request"]["privateKey"] = "never"

    with transport() as client:
        response = client.submit(bad)

    assert response["error"]["type"] == "PROTOCOL_REJECT"


def test_payload_is_not_mutated():
    original = payload()
    before = copy.deepcopy(original)

    with transport() as client:
        client.submit(original)

    assert original == before


def test_submit_after_close_fails_closed():
    client = transport()
    client.close()

    with pytest.raises(
        JsonlProcessTransportError,
        match="closed",
    ):
        client.submit(payload())


def test_non_object_response_fails_closed():
    command = [
        sys.executable,
        "-u",
        "-c",
        (
            "import sys\n"
            "for line in sys.stdin:\n"
            "    print('[]', flush=True)\n"
        ),
    ]

    client = JsonlProcessTransport(
        make_transport_config(
            command,
            timeout_seconds=1.0,
        )
    )

    try:
        with pytest.raises(
            JsonlProcessTransportError,
            match="JSON object",
        ):
            client.submit({"x": 1})
    finally:
        client.close()


def test_invalid_json_response_fails_closed():
    command = [
        sys.executable,
        "-u",
        "-c",
        (
            "import sys\n"
            "for line in sys.stdin:\n"
            "    print('not-json', flush=True)\n"
        ),
    ]

    client = JsonlProcessTransport(
        make_transport_config(
            command,
            timeout_seconds=1.0,
        )
    )

    try:
        with pytest.raises(
            JsonlProcessTransportError,
            match="invalid JSON",
        ):
            client.submit({"x": 1})
    finally:
        client.close()


def test_timeout_terminates_child_fail_closed():
    command = [
        sys.executable,
        "-u",
        "-c",
        (
            "import sys,time\n"
            "for line in sys.stdin:\n"
            "    time.sleep(5)\n"
        ),
    ]

    client = JsonlProcessTransport(
        make_transport_config(
            command,
            timeout_seconds=0.05,
        )
    )

    try:
        with pytest.raises(
            JsonlProcessTransportError,
            match="timeout",
        ):
            client.submit({"x": 1})

        assert client._proc.poll() is not None
    finally:
        client.close()


def test_empty_command_is_rejected():
    with pytest.raises(
        ValueError,
        match="command",
    ):
        make_transport_config([])


def test_nonpositive_timeout_is_rejected():
    with pytest.raises(
        ValueError,
        match="timeout",
    ):
        make_transport_config(
            [sys.executable],
            timeout_seconds=0,
        )
