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
from std0_quant.execution.production_eligibility_gate import (
    BLOCKED,
    ELIGIBLE,
    PRODUCTION_ELIGIBILITY_DECISION_SCHEMA_V1,
    PRODUCTION_ELIGIBILITY_GATE_V1,
    ProductionEligibilityDecision,
    evaluate_production_eligibility,
    production_eligibility_decision_hash,
    promotion_evidence_hash,
)
from std0_quant.research.factors.contracts import FactorStatus, ValidationStatus
from std0_quant.research.factors.registry import (
    FactorRegistryRecord,
    promote_factor,
    registry_record_hash,
)
from std0_quant.research.factors.validation_bridge import (
    promotion_evidence_from_decision,
)
from std0_quant.research.factors.validator import ValidationDecision


FACTOR_ID = "factor-a"
FACTOR_VERSION = "1"
DEFINITION_HASH = "a" * 64


def policy(**changes):
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
        created_at="2026-09-02T00:00:00Z",
    )


def validated_record():
    research_evidence = promotion_evidence_from_decision(
        research_decision(),
        decided_at="2026-09-02T00:01:00Z",
    )
    return promote_factor(
        candidate_record(),
        FactorStatus.VALIDATED,
        research_evidence,
    )


def execution_decision(
    *,
    validation_status=ValidationStatus.PENDING,
    reasons=("MEASURED_VENUE_EXECUTION_EVIDENCE_MISSING",),
    p=None,
    t=None,
    execution_run_id="execution-run",
):
    p = p or policy()
    provisional = ExecutionValidationDecision(
        execution_run_id=execution_run_id,
        provenance_run_id="provenance-run",
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


def evidence(record=None, decision=None, p=None):
    record = record or validated_record()
    decision = decision or execution_decision()
    p = p or policy()
    return promotion_evidence_from_execution_decision(
        record,
        decision,
        p,
        decided_at="2026-09-02T00:02:00Z",
    )


def evaluate(*, record=None, decision=None, p=None, promo=None):
    record = record or validated_record()
    p = p or policy()
    decision = decision or execution_decision(p=p)
    promo = promo or evidence(record, decision, p)
    return evaluate_production_eligibility(
        record,
        promo,
        decision,
        p,
        gate_run_id="gate-run",
    )


def test_contract_symbols_and_versions():
    assert PRODUCTION_ELIGIBILITY_GATE_V1 == "production_eligibility_gate_v1"
    assert (
        PRODUCTION_ELIGIBILITY_DECISION_SCHEMA_V1
        == "production_eligibility_decision_v1"
    )
    assert ELIGIBLE == "ELIGIBLE"
    assert BLOCKED == "BLOCKED"
    assert ProductionEligibilityDecision is not None


def test_current_execution_policy_v1_cannot_construct_pass_decision():
    decision = execution_decision()

    with pytest.raises(ValueError):
        replace(
            decision,
            validation_status=ValidationStatus.PASS,
            reasons=(),
        )


def test_pending_execution_decision_is_blocked():
    record = validated_record()
    decision = execution_decision()
    promo = evidence(record, decision)

    result = evaluate(
        record=record,
        decision=decision,
        promo=promo,
    )

    assert result.status == BLOCKED
    assert "EXECUTION_VALIDATION_NOT_PASS" in result.reasons
    assert result.registry_record_hash == registry_record_hash(record)
    assert result.promotion_evidence_hash == promotion_evidence_hash(promo)
    assert result.execution_decision_artifact_hash == decision.artifact_hash
    assert result.execution_run_id == decision.execution_run_id
    assert production_eligibility_decision_hash(result) == result.artifact_hash


def test_failed_execution_decision_is_blocked():
    decision = execution_decision(
        validation_status=ValidationStatus.FAIL,
        reasons=("PROVENANCE_BLOCKED",),
    )

    result = evaluate(
        decision=decision,
        promo=evidence(
            validated_record(),
            decision,
            policy(),
        ),
    )

    assert result.status == BLOCKED
    assert "EXECUTION_VALIDATION_NOT_PASS" in result.reasons


def test_registry_must_still_be_validated():
    record = candidate_record()
    valid_record = validated_record()
    decision = execution_decision()
    promo = evidence(valid_record, decision)

    result = evaluate(
        record=record,
        decision=decision,
        promo=promo,
    )

    assert result.status == BLOCKED
    assert "REGISTRY_NOT_VALIDATED" in result.reasons


def test_registry_factor_identity_must_match_execution_target():
    record = replace(
        validated_record(),
        factor_id="other-factor",
    )
    valid_record = validated_record()
    decision = execution_decision()
    promo = evidence(valid_record, decision)

    result = evaluate(
        record=record,
        decision=decision,
        promo=promo,
    )

    assert result.status == BLOCKED
    assert "REGISTRY_TARGET_IDENTITY_MISMATCH" in result.reasons


def test_tampered_execution_decision_hash_is_blocked():
    decision = replace(
        execution_decision(),
        artifact_hash="0" * 64,
    )
    valid_decision = execution_decision()
    promo = evidence(
        validated_record(),
        valid_decision,
    )

    result = evaluate(
        decision=decision,
        promo=promo,
    )

    assert result.status == BLOCKED
    assert "EXECUTION_DECISION_ARTIFACT_HASH_MISMATCH" in result.reasons


def test_execution_policy_provenance_mismatch_is_blocked():
    decision = execution_decision(
        p=policy(version="1"),
    )
    promo = evidence(
        validated_record(),
        decision,
        policy(version="1"),
    )

    result = evaluate(
        decision=decision,
        p=policy(version="2"),
        promo=promo,
    )

    assert result.status == BLOCKED
    assert "EXECUTION_POLICY_PROVENANCE_MISMATCH" in result.reasons


def test_promotion_execution_provenance_mismatch_is_blocked():
    record = validated_record()
    decision = execution_decision()
    promo = replace(
        evidence(record, decision),
        execution_artifact_hash="f" * 64,
    )

    result = evaluate(
        record=record,
        decision=decision,
        promo=promo,
    )

    assert result.status == BLOCKED
    assert "PROMOTION_EVIDENCE_MISMATCH" in result.reasons


def test_promotion_research_provenance_mismatch_is_blocked():
    record = validated_record()
    decision = execution_decision()
    promo = replace(
        evidence(record, decision),
        research_run_id="other-research-run",
    )

    result = evaluate(
        record=record,
        decision=decision,
        promo=promo,
    )

    assert result.status == BLOCKED
    assert "PROMOTION_EVIDENCE_MISMATCH" in result.reasons


@pytest.mark.parametrize(
    "field,value",
    (
        ("research_artifact_hash", None),
        ("research_run_id", None),
        ("decided_at", None),
    ),
)
def test_malformed_validated_transition_provenance_is_blocked(field, value):
    record = validated_record()
    malformed_transition = replace(
        record.transitions[-1],
        **{field: value},
    )
    malformed_record = replace(
        record,
        transitions=(malformed_transition,),
    )

    result = evaluate(
        record=malformed_record,
        promo=evidence(
            record,
            execution_decision(),
        ),
    )

    assert result.status == BLOCKED
    assert "VALIDATED_TRANSITION_PROVENANCE_INVALID" in result.reasons


def test_gate_does_not_mutate_registry_record():
    record = validated_record()
    before = record

    result = evaluate(record=record)

    assert result.status == BLOCKED
    assert record == before
    assert record.status == FactorStatus.VALIDATED
    assert len(record.transitions) == 1


def test_gate_hash_excludes_gate_run_id():
    result = evaluate()

    changed = replace(
        result,
        gate_run_id="different-gate-run",
    )

    assert (
        production_eligibility_decision_hash(changed)
        == result.artifact_hash
    )


@pytest.mark.parametrize(
    "field,value",
    (
        ("registry_record_hash", "1" * 64),
        ("promotion_evidence_hash", "2" * 64),
        ("execution_decision_artifact_hash", "3" * 64),
        ("execution_run_id", "other-execution-run"),
        ("policy_hash", "4" * 64),
    ),
)
def test_gate_hash_binds_governance_semantics(field, value):
    result = evaluate()
    changed = replace(result, **{field: value})

    assert (
        production_eligibility_decision_hash(changed)
        != result.artifact_hash
    )


def test_promotion_evidence_hash_binds_execution_run_id():
    promo = evidence()
    changed = replace(
        promo,
        execution_run_id="other-execution-run",
    )

    assert promotion_evidence_hash(changed) != promotion_evidence_hash(promo)


def test_blocked_decision_requires_reasons():
    result = evaluate()

    with pytest.raises(ValueError):
        replace(
            result,
            status=BLOCKED,
            reasons=(),
        )


def test_eligible_decision_cannot_have_reasons():
    result = evaluate()

    with pytest.raises(ValueError):
        replace(
            result,
            status=ELIGIBLE,
            reasons=("SHOULD_NOT_EXIST",),
        )


def test_gate_v1_cannot_directly_construct_eligible_authorization():
    result = evaluate()

    with pytest.raises(ValueError):
        replace(
            result,
            status=ELIGIBLE,
            reasons=(),
        )


def test_gate_artifact_binds_execution_provenance_run_id():
    record = validated_record()
    decision = execution_decision()
    promo = evidence(record, decision)

    result = evaluate(
        record=record,
        decision=decision,
        promo=promo,
    )

    assert result.provenance_run_id == decision.provenance_run_id

    changed_decision = replace(
        decision,
        provenance_run_id="different-provenance-run",
    )

    assert (
        execution_validation_decision_hash(changed_decision)
        == decision.artifact_hash
    )

    changed_result = evaluate(
        record=record,
        decision=changed_decision,
        promo=promo,
    )

    assert (
        changed_result.provenance_run_id
        == "different-provenance-run"
    )
    assert changed_result.artifact_hash != result.artifact_hash
