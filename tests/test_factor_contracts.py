from dataclasses import FrozenInstanceError

import pytest

from std0_quant.research.factors.contracts import (
    FACTOR_SPEC_SCHEMA_V1,
    FactorSpec,
    FactorStatus,
    ValidationStatus,
    factor_spec_hash,
)


def make_spec(**overrides):
    values = {
        "factor_id": "btc_ret_3s",
        "version": "1",
        "hypothesis": "Recent BTC return contains incremental Y30 information.",
        "inputs": ("btc_ret_3s",),
        "transform": "identity",
        "parameters": (),
        "lookback_ms": 3000,
        "decision_ts_rule": "first_opp_end_ms",
        "availability_ts_rule": "source_timestamp_max_ms<=feature_cutoff_ms",
        "missing_policy": "EXCLUDE",
        "universe": "btc_updown_5m",
        "label": "y30",
        "period_key": "iso_week",
        "expected_direction": "POSITIVE",
        "expected_regime": "ALL",
        "created_by": "human",
        "created_at": "2026-08-30T00:00:00+00:00",
    }
    values.update(overrides)
    return FactorSpec(**values)


def test_factor_spec_v1_normalizes_and_is_frozen():
    spec = make_spec()
    assert spec.schema_version == FACTOR_SPEC_SCHEMA_V1
    assert spec.inputs == ("btc_ret_3s",)
    with pytest.raises(FrozenInstanceError):
        spec.factor_id = "changed"


def test_factor_status_values_are_frozen_contract():
    assert {x.value for x in FactorStatus} == {
        "CANDIDATE",
        "VALIDATED",
        "REJECTED",
        "PRODUCTION_ELIGIBLE",
        "DEPRECATED",
    }
    assert {x.value for x in ValidationStatus} == {"PENDING", "PASS", "FAIL"}


@pytest.mark.parametrize(
    "field,value",
    [
        ("factor_id", ""),
        ("version", ""),
        ("hypothesis", ""),
        ("inputs", ()),
        ("transform", ""),
        ("decision_ts_rule", ""),
        ("availability_ts_rule", ""),
        ("universe", ""),
        ("label", ""),
        ("period_key", ""),
        ("created_by", ""),
        ("created_at", ""),
    ],
)
def test_factor_spec_rejects_empty_required_fields(field, value):
    with pytest.raises(ValueError):
        make_spec(**{field: value})


def test_factor_spec_rejects_negative_lookback():
    with pytest.raises(ValueError):
        make_spec(lookback_ms=-1)


def test_factor_spec_hash_is_deterministic():
    a = make_spec()
    b = make_spec()
    assert factor_spec_hash(a) == factor_spec_hash(b)
    assert len(factor_spec_hash(a)) == 64


def test_factor_spec_hash_changes_when_research_definition_changes():
    base = make_spec()
    changed_parameter = make_spec(parameters=(("window_ms", 5000),))
    changed_lookback = make_spec(lookback_ms=5000)
    changed_availability = make_spec(
        availability_ts_rule="source_timestamp_max_ms<=decision_ts_ms"
    )
    assert factor_spec_hash(base) != factor_spec_hash(changed_parameter)
    assert factor_spec_hash(base) != factor_spec_hash(changed_lookback)
    assert factor_spec_hash(base) != factor_spec_hash(changed_availability)


def test_factor_spec_hash_excludes_creation_metadata():
    a = make_spec(created_by="human", created_at="2026-08-30T00:00:00+00:00")
    b = make_spec(
        created_by="prime-agent",
        created_at="2026-08-31T00:00:00+00:00",
    )
    assert factor_spec_hash(a) == factor_spec_hash(b)
