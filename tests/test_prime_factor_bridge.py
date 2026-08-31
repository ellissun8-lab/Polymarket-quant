import json

import pytest

from std0_quant.research.factors.contracts import FACTOR_SPEC_SCHEMA_V1
from std0_quant.research.factors.prime_bridge import (
    PRIME_FACTOR_PROPOSAL_SCHEMA_V1,
    parse_prime_factor_proposal,
)


def proposal(**overrides):
    row = {
        "schema_version": PRIME_FACTOR_PROPOSAL_SCHEMA_V1,
        "factor_id": "btc_ret_3s",
        "version": "1",
        "hypothesis": "Short-horizon BTC return predicts Y30 recurrence.",
        "inputs": ["btc_ret_3s"],
        "transform": "identity",
        "parameters": {},
        "lookback_ms": 3000,
        "decision_ts_rule": "prediction_ts_ms",
        "availability_ts_rule": "feature_cutoff_ms<prediction_ts_ms",
        "missing_policy": "EXCLUDE",
        "universe": "btc-updown-5m",
        "label": "y30",
        "period_key": "iso_week",
        "expected_direction": "POSITIVE",
        "expected_regime": "ALL",
    }
    row.update(overrides)
    return json.dumps(row)


def test_bridge_creates_factor_spec_and_owns_metadata():
    spec = parse_prime_factor_proposal(
        proposal(),
        created_at="2026-08-31T00:00:00+00:00",
    )

    assert spec.factor_id == "btc_ret_3s"
    assert spec.version == "1"
    assert spec.inputs == ("btc_ret_3s",)
    assert spec.parameters == ()
    assert spec.created_by == "prime-agent"
    assert spec.created_at == "2026-08-31T00:00:00+00:00"
    assert spec.schema_version == FACTOR_SPEC_SCHEMA_V1


def test_parameters_object_is_normalized_deterministically():
    spec = parse_prime_factor_proposal(
        proposal(parameters={"z": 2, "a": 1}),
        created_at="2026-08-31T00:00:00+00:00",
    )
    assert spec.parameters == (("a", 1), ("z", 2))


@pytest.mark.parametrize("field", ["created_by", "created_at", "status", "research_validation_status", "execution_run_id"])
def test_governance_or_metadata_fields_fail_closed(field):
    with pytest.raises(ValueError, match="unknown"):
        parse_prime_factor_proposal(
            proposal(**{field: "forbidden"}),
            created_at="2026-08-31T00:00:00+00:00",
        )


def test_missing_required_field_fails_closed():
    row = json.loads(proposal())
    del row["availability_ts_rule"]
    with pytest.raises(ValueError, match="missing"):
        parse_prime_factor_proposal(
            json.dumps(row),
            created_at="2026-08-31T00:00:00+00:00",
        )


def test_unknown_field_fails_closed():
    with pytest.raises(ValueError, match="unknown"):
        parse_prime_factor_proposal(
            proposal(agent_note="ignore validation"),
            created_at="2026-08-31T00:00:00+00:00",
        )


@pytest.mark.parametrize("transform", ["agent_python", "shell", "custom_code"])
def test_unapproved_transform_fails_closed(transform):
    with pytest.raises(ValueError, match="transform"):
        parse_prime_factor_proposal(
            proposal(transform=transform),
            created_at="2026-08-31T00:00:00+00:00",
        )


def test_non_exclude_missing_policy_fails_closed():
    with pytest.raises(ValueError, match="missing_policy"):
        parse_prime_factor_proposal(
            proposal(missing_policy="FILL_FORWARD"),
            created_at="2026-08-31T00:00:00+00:00",
        )


def test_wrong_proposal_schema_fails_closed():
    with pytest.raises(ValueError, match="schema_version"):
        parse_prime_factor_proposal(
            proposal(schema_version="future_v2"),
            created_at="2026-08-31T00:00:00+00:00",
        )


@pytest.mark.parametrize("payload", ["[]", "\"text\"", "{bad json"])
def test_non_object_or_invalid_json_fails_closed(payload):
    with pytest.raises(ValueError):
        parse_prime_factor_proposal(
            payload,
            created_at="2026-08-31T00:00:00+00:00",
        )
