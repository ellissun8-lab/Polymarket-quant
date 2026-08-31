import json

from std0_quant.research.factors.contracts import FactorStatus, ValidationStatus
from std0_quant.research.factors.registry import (
    FactorRegistryRecord,
    load_registry_record,
    promote_factor,
    write_registry_record,
)
from std0_quant.research.factors.validation_bridge import (
    promotion_evidence_from_decision,
)
from std0_quant.research.factors.validator import ValidationDecision


def decision(status=ValidationStatus.PASS, reasons=(), bundle_hash=None):
    return ValidationDecision(
        factor_id="test_factor",
        factor_version="1",
        research_validation_status=status,
        temporal_integrity=ValidationStatus.PASS,
        reasons=reasons,
        research_artifact_hash="a" * 64,
        research_run_id="research-1",
        policy_id="generic-factor-research",
        policy_version="1",
        policy_hash="b" * 64,
        validation_evidence_bundle_hash=bundle_hash,
    )


def candidate():
    return FactorRegistryRecord(
        factor_id="test_factor",
        factor_version="1",
        definition_hash="c" * 64,
        status=FactorStatus.CANDIDATE,
        created_by="test",
        created_at="2026-08-30T00:00:00+00:00",
    )


def test_decision_to_promotion_evidence_preserves_research_policy_provenance():
    evidence = promotion_evidence_from_decision(
        decision(),
        decided_at="2026-08-30T01:00:00+00:00",
    )

    assert evidence.research_validation_status == ValidationStatus.PASS
    assert evidence.temporal_integrity == ValidationStatus.PASS
    assert evidence.research_artifact_hash == "a" * 64
    assert evidence.research_run_id == "research-1"
    assert evidence.research_policy_id == "generic-factor-research"
    assert evidence.research_policy_version == "1"
    assert evidence.research_policy_hash == "b" * 64
    assert evidence.research_validation_reasons == ()


def test_policy_provenance_survives_transition_and_registry_roundtrip(tmp_path):
    evidence = promotion_evidence_from_decision(
        decision(),
        decided_at="2026-08-30T01:00:00+00:00",
    )
    promoted = promote_factor(
        candidate(),
        FactorStatus.VALIDATED,
        evidence,
    )

    transition = promoted.transitions[-1]
    assert transition.research_policy_id == "generic-factor-research"
    assert transition.research_policy_version == "1"
    assert transition.research_policy_hash == "b" * 64
    assert transition.research_validation_reasons == ()

    path = tmp_path / "factor.json"
    write_registry_record(path, promoted)
    assert load_registry_record(path) == promoted


def test_fail_decision_reason_survives_rejected_transition():
    evidence = promotion_evidence_from_decision(
        decision(
            status=ValidationStatus.FAIL,
            reasons=("OOS_AUC_BELOW_MIN",),
        ),
        decided_at="2026-08-30T01:00:00+00:00",
    )
    rejected = promote_factor(
        candidate(),
        FactorStatus.REJECTED,
        evidence,
    )

    transition = rejected.transitions[-1]
    assert transition.research_validation_status == ValidationStatus.FAIL
    assert transition.research_policy_hash == "b" * 64
    assert transition.research_validation_reasons == ("OOS_AUC_BELOW_MIN",)


def test_validation_evidence_bundle_hash_survives_bridge():
    evidence = promotion_evidence_from_decision(
        decision(bundle_hash="d" * 64),
        decided_at="2026-08-30T01:00:00+00:00",
    )

    assert evidence.validation_evidence_bundle_hash == "d" * 64


def test_validation_evidence_bundle_hash_survives_transition_and_roundtrip(tmp_path):
    evidence = promotion_evidence_from_decision(
        decision(bundle_hash="d" * 64),
        decided_at="2026-08-30T01:00:00+00:00",
    )
    promoted = promote_factor(
        candidate(),
        FactorStatus.VALIDATED,
        evidence,
    )

    transition = promoted.transitions[-1]
    assert transition.validation_evidence_bundle_hash == "d" * 64

    path = tmp_path / "factor_bundle.json"
    write_registry_record(path, promoted)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert (
        payload["record"]["transitions"][-1][
            "validation_evidence_bundle_hash"
        ]
        == "d" * 64
    )
    assert load_registry_record(path) == promoted


def test_old_transition_serialization_omits_bundle_hash(tmp_path):
    evidence = promotion_evidence_from_decision(
        decision(),
        decided_at="2026-08-30T01:00:00+00:00",
    )
    promoted = promote_factor(
        candidate(),
        FactorStatus.VALIDATED,
        evidence,
    )

    path = tmp_path / "factor_old_shape.json"
    write_registry_record(path, promoted)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert (
        "validation_evidence_bundle_hash"
        not in payload["record"]["transitions"][-1]
    )
