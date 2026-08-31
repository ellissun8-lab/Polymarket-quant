import json

import pytest

from std0_quant.research.factors.contracts import FactorStatus, ValidationStatus
from std0_quant.research.factors.registry import (
    FactorRegistryRecord,
    PromotionEvidence,
    load_registry_record,
    promote_factor,
    registry_record_hash,
    write_registry_record,
)


def candidate():
    return FactorRegistryRecord(
        factor_id="btc_ret_3s",
        factor_version="1",
        definition_hash="a" * 64,
        status=FactorStatus.CANDIDATE,
        created_by="human",
        created_at="2026-08-30T00:00:00+00:00",
    )


def validated():
    return promote_factor(
        candidate(),
        FactorStatus.VALIDATED,
        PromotionEvidence(
            research_validation_status=ValidationStatus.PASS,
            temporal_integrity=ValidationStatus.PASS,
            research_artifact_hash="b" * 64,
            research_run_id="research-1",
            decided_at="2026-08-30T01:00:00+00:00",
        ),
    )


def test_registry_hash_is_deterministic_and_transition_sensitive():
    a = candidate()
    b = candidate()
    assert registry_record_hash(a) == registry_record_hash(b)
    assert len(registry_record_hash(a)) == 64
    assert registry_record_hash(a) != registry_record_hash(validated())


def test_registry_atomic_roundtrip(tmp_path):
    path = tmp_path / "factor.json"
    original = validated()

    written = write_registry_record(path, original)
    loaded = load_registry_record(path)

    assert written == path
    assert loaded == original

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "factor_registry_v1"
    assert payload["record_hash"] == registry_record_hash(original)


def test_registry_tamper_detection_fails_closed(tmp_path):
    path = tmp_path / "factor.json"
    write_registry_record(path, validated())

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["record"]["status"] = "REJECTED"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="hash"):
        load_registry_record(path)
