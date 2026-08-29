import json
from pathlib import Path
import subprocess
import sys

import pytest

from std0_quant.execution.clodds_shadow_protocol import (
    AUDITED_CLODDS_COMMIT_V1,
    CLODDS_SHADOW_PROTOCOL_V1,
    CloddsShadowProtocolError,
    build_shadow_request,
    make_shadow_ack,
    validate_shadow_request,
)
from std0_quant.execution.contracts import (
    OrderEvent,
    OrderEventType,
    OrderIntent,
)


def intent(*, tif="GTC"):
    return OrderIntent(
        intent_id="intent-1",
        condition_id="condition-1",
        outcome="Up",
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


def request(*, tif="GTC", post_only=True):
    return build_shadow_request(
        intent=intent(tif=tif),
        token_id="token-up",
        post_only=post_only,
    )


def test_builds_pinned_shadow_request():
    payload = request()

    assert (
        payload["protocol_version"]
        == CLODDS_SHADOW_PROTOCOL_V1
    )
    assert payload["mode"] == "SHADOW"
    assert (
        payload["clodds_commit"]
        == AUDITED_CLODDS_COMMIT_V1
    )
    assert payload["clodds_request"]["tokenId"] == "token-up"
    assert payload["clodds_request"]["postOnly"] is True

    json.dumps(payload)


def test_ioc_maps_through_protocol_as_fak():
    payload = request(
        tif="IOC",
        post_only=False,
    )

    assert payload["clodds_request"]["orderType"] == "FAK"


def test_validate_reconstructs_intent():
    rebuilt = validate_shadow_request(request())

    assert rebuilt == intent()


def test_non_shadow_mode_fails_closed():
    payload = request()
    payload["mode"] = "LIVE"

    with pytest.raises(
        CloddsShadowProtocolError,
        match="non-SHADOW",
    ):
        validate_shadow_request(payload)


def test_commit_mismatch_fails_closed():
    payload = request()
    payload["clodds_commit"] = "wrong"

    with pytest.raises(
        CloddsShadowProtocolError,
        match="commit mismatch",
    ):
        validate_shadow_request(payload)


def test_extra_top_level_field_fails_closed():
    payload = request()
    payload["unexpected"] = True

    with pytest.raises(
        CloddsShadowProtocolError,
        match="fields",
    ):
        validate_shadow_request(payload)


def test_tampered_price_fails_closed():
    payload = request()
    payload["clodds_request"]["price"] = 0.99

    with pytest.raises(
        CloddsShadowProtocolError,
        match="does not match",
    ):
        validate_shadow_request(payload)


def test_credentials_are_forbidden():
    payload = request()
    payload["clodds_request"]["privateKey"] = "never"

    with pytest.raises(
        CloddsShadowProtocolError,
        match="forbidden",
    ):
        validate_shadow_request(payload)


def test_synthetic_ack_is_explicitly_not_venue_timed():
    response = make_shadow_ack(
        payload=request(),
        receive_ts_ms=2000,
    )

    assert response["mode"] == "SHADOW"
    assert response["intent_id"] == "intent-1"

    event = OrderEvent.from_dict(response["event"])

    assert event.event_type == OrderEventType.VENUE_ACK
    assert event.receive_ts_ms == pytest.approx(2000)
    assert event.venue_ts_ms is None
    assert event.venue_order_id == "shadow:intent-1"
    assert event.remaining_qty == pytest.approx(10)
    assert event.reason == "SHADOW_SYNTHETIC_ACK"


def test_reference_jsonl_sidecar_process_roundtrip():
    root = Path(__file__).resolve().parents[2]
    script = root / "scripts" / "run_clodds_shadow_sidecar.py"

    proc = subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(request()) + "\n",
        text=True,
        capture_output=True,
        cwd=root,
        timeout=10,
        check=False,
    )

    assert proc.returncode == 0
    assert proc.stderr == ""

    lines = [
        line
        for line in proc.stdout.splitlines()
        if line.strip()
    ]

    assert len(lines) == 1

    response = json.loads(lines[0])

    assert response["mode"] == "SHADOW"
    assert response["intent_id"] == "intent-1"
    assert response["event"]["reason"] == "SHADOW_SYNTHETIC_ACK"


def test_reference_sidecar_rejects_live_mode():
    root = Path(__file__).resolve().parents[2]
    script = root / "scripts" / "run_clodds_shadow_sidecar.py"

    payload = request()
    payload["mode"] = "LIVE"

    proc = subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(payload) + "\n",
        text=True,
        capture_output=True,
        cwd=root,
        timeout=10,
        check=False,
    )

    assert proc.returncode == 0

    response = json.loads(proc.stdout.strip())

    assert response["mode"] == "SHADOW"
    assert response["error"]["type"] == "PROTOCOL_REJECT"
