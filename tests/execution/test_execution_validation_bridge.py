from dataclasses import replace

import pytest

from std0_quant.execution.execution_validation import ExecutionValidationTarget
from std0_quant.execution.execution_validation_bridge import (
    promotion_evidence_from_execution_decision,
)
from std0_quant.execution.execution_validation_policy import (
    MEASURED_VENUE_EXECUTION,
    ExecutionValidationDecision,
    ExecutionValidationPolicy,
    execution_validation_decision_hash,
    execution_validation_policy_hash,
)
from std0_quant.research.factors.contracts import FactorStatus, ValidationStatus
from std0_quant.research.factors.registry import (
    FactorRegistryRecord,
    promote_factor,
)
from std0_quant.research.factors.validation_bridge import (
    promotion_evidence_from_decision,
)
from std0_quant.research.factors.validator import ValidationDecision


FACTOR_ID = "factor-a"
FACTOR_VERSION = "1"
DEFINITION_HASH = "a" * 64


def execution_policy(**changes):
    values = {
        "policy_id": "execution-validation-policy",
        "version": "1",
        "required_pass_evidence_kind": MEASURED_VENUE_EXECUTION,
    }
    values.update(changes)
    return ExecutionValidationPolicy(**values)


def target(**changes):
    values = {
        "factor_id": FACTOR_ID,
        "factor_version": FACTOR_VERSION,
        "definition_hash": DEFINITION_HASH,
        "alpha_id": "alpha-a",
        "alpha_version": "1",
        "risk_policy_version": "risk-v1",
    }
    values.update(changes)
    return ExecutionValidationTarget(**values)


def research_decision():
    return ValidationDecision(
        factor_id=FACTOR_ID,
        factor_version=FACTOR_VERSION,
        research_validation_status=ValidationStatus.PASS,
        temporal_integrity=ValidationStatus.PASS,
        reasons=(),
        research_artifact_hash="b" * 64,
        research_run_id="research-run",
        policy_id="research-policy",
        policy_version="3",
        policy_hash="c" * 64,
        validation_evidence_bundle_hash="d" * 64,
    )


def candidate_record():
    return FactorRegistryRecord(
        factor_id=FACTOR_ID,
        factor_version=FACTOR_VERSION,
        definition_hash=DEFINITION_HASH,
        status=FactorStatus.CANDIDATE,
        created_by="test",
        created_at="2026-09-01T00:00:00Z",
    )


def validated_record():
    evidence = promotion_evidence_from_decision(
        research_decision(),
        decided_at="2026-09-01T00:01:00Z",
    )
    return promote_factor(
        candidate_record(),
        FactorStatus.VALIDATED,
        evidence,
    )


def execution_decision(
    *,
    validation_status=ValidationStatus.PENDING,
    reasons=("MEASURED_VENUE_EXECUTION_EVIDENCE_MISSING",),
    t=None,
    p=None,
    execution_run_id="execution-decision-run",
):
    p = p or execution_policy()
    provisional = ExecutionValidationDecision(
        execution_run_id=execution_run_id,
        provenance_run_id="execution-provenance-run",
        target=t or target(),
        validation_status=validation_status,
        reasons=tuple(reasons),
        provenance_artifact_hash="e" * 64,
        policy_id=p.policy_id,
        policy_version=p.version,
        policy_hash=execution_validation_policy_hash(p),
        artifact_hash="PENDING",
    )
    return replace(
        provisional,
        artifact_hash=execution_validation_decision_hash(provisional),
    )


def test_bridge_preserves_registry_research_provenance_and_binds_execution_decision():
    record = validated_record()
    decision = execution_decision()
    policy = execution_policy()

    evidence = promotion_evidence_from_execution_decision(
        record,
        decision,
        policy,
        decided_at="2026-09-01T00:02:00Z",
    )

    transition = record.transitions[-1]
    assert evidence.research_validation_status == transition.research_validation_status
    assert evidence.temporal_integrity == transition.temporal_integrity
    assert evidence.research_artifact_hash == transition.research_artifact_hash
    assert evidence.research_run_id == transition.research_run_id
    assert evidence.research_policy_id == transition.research_policy_id
    assert evidence.research_policy_version == transition.research_policy_version
    assert evidence.research_policy_hash == transition.research_policy_hash
    assert evidence.research_validation_reasons == transition.research_validation_reasons
    assert evidence.validation_evidence_bundle_hash == transition.validation_evidence_bundle_hash

    assert evidence.execution_validation_status == ValidationStatus.PENDING
    assert evidence.execution_artifact_hash == decision.artifact_hash
    assert evidence.execution_run_id == decision.execution_run_id
    assert evidence.decided_at == "2026-09-01T00:02:00Z"

    assert record.status == FactorStatus.VALIDATED
    assert len(record.transitions) == 1


@pytest.mark.parametrize(
    "field,value",
    (
        ("factor_id", "other-factor"),
        ("factor_version", "2"),
        ("definition_hash", "f" * 64),
    ),
)
def test_bridge_rejects_execution_target_registry_identity_mismatch(field, value):
    decision = execution_decision(t=target(**{field: value}))

    with pytest.raises(ValueError):
        promotion_evidence_from_execution_decision(
            validated_record(),
            decision,
            execution_policy(),
            decided_at="2026-09-01T00:02:00Z",
        )


def test_bridge_requires_registry_record_to_be_validated():
    with pytest.raises(ValueError):
        promotion_evidence_from_execution_decision(
            candidate_record(),
            execution_decision(),
            execution_policy(),
            decided_at="2026-09-01T00:02:00Z",
        )


def test_bridge_requires_a_real_validated_transition():
    malformed = FactorRegistryRecord(
        factor_id=FACTOR_ID,
        factor_version=FACTOR_VERSION,
        definition_hash=DEFINITION_HASH,
        status=FactorStatus.VALIDATED,
        created_by="test",
        created_at="2026-09-01T00:00:00Z",
        transitions=(),
    )

    with pytest.raises(ValueError):
        promotion_evidence_from_execution_decision(
            malformed,
            execution_decision(),
            execution_policy(),
            decided_at="2026-09-01T00:02:00Z",
        )


def test_bridge_rejects_tampered_execution_decision_hash():
    tampered = replace(
        execution_decision(),
        artifact_hash="0" * 64,
    )

    with pytest.raises(ValueError):
        promotion_evidence_from_execution_decision(
            validated_record(),
            tampered,
            execution_policy(),
            decided_at="2026-09-01T00:02:00Z",
        )


def test_bridge_rejects_execution_policy_provenance_mismatch():
    decision = execution_decision(p=execution_policy(version="1"))

    with pytest.raises(ValueError):
        promotion_evidence_from_execution_decision(
            validated_record(),
            decision,
            execution_policy(version="2"),
            decided_at="2026-09-01T00:02:00Z",
        )


def test_bridge_preserves_execution_fail_without_promoting():
    decision = execution_decision(
        validation_status=ValidationStatus.FAIL,
        reasons=("PROVENANCE_BLOCKED",),
    )
    record = validated_record()

    evidence = promotion_evidence_from_execution_decision(
        record,
        decision,
        execution_policy(),
        decided_at="2026-09-01T00:02:00Z",
    )

    assert evidence.execution_validation_status == ValidationStatus.FAIL
    assert evidence.execution_artifact_hash == decision.artifact_hash
    assert record.status == FactorStatus.VALIDATED


def test_pending_execution_evidence_cannot_promote_to_production():
    record = validated_record()
    evidence = promotion_evidence_from_execution_decision(
        record,
        execution_decision(),
        execution_policy(),
        decided_at="2026-09-01T00:02:00Z",
    )

    with pytest.raises(ValueError):
        promote_factor(
            record,
            FactorStatus.PRODUCTION_ELIGIBLE,
            evidence,
        )


def test_bridge_requires_actual_string_decided_at():
    with pytest.raises((TypeError, ValueError)):
        promotion_evidence_from_execution_decision(
            validated_record(),
            execution_decision(),
            execution_policy(),
            decided_at=None,
        )


def test_bridge_requires_research_policy_provenance_on_validated_transition():
    record = validated_record()
    transition = replace(
        record.transitions[-1],
        research_policy_id=None,
        research_policy_version=None,
        research_policy_hash=None,
    )
    malformed = replace(
        record,
        transitions=(transition,),
    )

    with pytest.raises(ValueError):
        promotion_evidence_from_execution_decision(
            malformed,
            execution_decision(),
            execution_policy(),
            decided_at="2026-09-01T00:02:00Z",
        )


def test_bridge_requires_validation_evidence_bundle_on_validated_transition():
    record = validated_record()
    transition = replace(
        record.transitions[-1],
        validation_evidence_bundle_hash=None,
    )
    malformed = replace(
        record,
        transitions=(transition,),
    )

    with pytest.raises(ValueError):
        promotion_evidence_from_execution_decision(
            malformed,
            execution_decision(),
            execution_policy(),
            decided_at="2026-09-01T00:02:00Z",
        )
