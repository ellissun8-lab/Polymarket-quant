from dataclasses import replace

import pytest

from std0_quant.execution.clodds_mapping import CLODDS_MAPPING_VERSION_V1
from std0_quant.execution.clodds_shadow_protocol import (
    AUDITED_CLODDS_COMMIT_V1,
    CLODDS_SHADOW_PROTOCOL_V1,
)
from std0_quant.execution.execution_validation import (
    BLOCKED,
    READY_FOR_POLICY_EVALUATION,
    ExecutionValidationArtifact,
    ExecutionValidationTarget,
    execution_validation_artifact_hash,
)
from std0_quant.execution.execution_validation_policy import (
    MEASURED_VENUE_EXECUTION,
    ExecutionValidationPolicy,
    evaluate_execution_validation_policy,
    execution_validation_decision_hash,
    execution_validation_policy_hash,
)
from std0_quant.research.factors.contracts import ValidationStatus


def target():
    return ExecutionValidationTarget(
        factor_id="factor-a",
        factor_version="1",
        definition_hash="a" * 64,
        alpha_id="alpha-a",
        alpha_version="1",
        risk_policy_version="risk-v1",
    )


def provenance(
    *,
    status=READY_FOR_POLICY_EVALUATION,
    reasons=(),
    execution_run_id="provenance-run-a",
):
    provisional = ExecutionValidationArtifact(
        execution_run_id=execution_run_id,
        target=target(),
        status=status,
        reasons=tuple(reasons),
        alpha_factory_artifact_hash="b" * 64,
        strategy_candidate_hash="c" * 64,
        strategy_shadow_artifact_hash="d" * 64,
        strategy_shadow_run_id="strategy-shadow-run",
        protocol_version=CLODDS_SHADOW_PROTOCOL_V1,
        clodds_commit=AUDITED_CLODDS_COMMIT_V1,
        mapping_version=CLODDS_MAPPING_VERSION_V1,
        artifact_hash="PENDING",
    )
    return replace(
        provisional,
        artifact_hash=execution_validation_artifact_hash(provisional),
    )


def policy(**changes):
    values = {
        "policy_id": "execution-validation-policy",
        "version": "1",
        "required_pass_evidence_kind": MEASURED_VENUE_EXECUTION,
    }
    values.update(changes)
    return ExecutionValidationPolicy(**values)


def test_policy_only_allows_measured_venue_execution_for_pass():
    with pytest.raises(ValueError):
        policy(required_pass_evidence_kind="SIMULATION")

    with pytest.raises(ValueError):
        policy(required_pass_evidence_kind="SHADOW")


def test_policy_hash_is_deterministic_and_semantic():
    first = policy()
    same = policy()
    changed = policy(version="2")

    assert execution_validation_policy_hash(first) == execution_validation_policy_hash(same)
    assert execution_validation_policy_hash(first) != execution_validation_policy_hash(changed)


def test_ready_shadow_provenance_is_pending_not_pass():
    evidence = provenance()

    decision = evaluate_execution_validation_policy(
        evidence,
        policy(),
        execution_run_id="decision-run-a",
    )

    assert decision.validation_status == ValidationStatus.PENDING
    assert decision.validation_status != ValidationStatus.PASS
    assert decision.reasons == (
        "MEASURED_VENUE_EXECUTION_EVIDENCE_MISSING",
    )
    assert decision.target == evidence.target
    assert decision.provenance_artifact_hash == evidence.artifact_hash
    assert decision.provenance_run_id == evidence.execution_run_id
    assert decision.policy_hash == execution_validation_policy_hash(policy())
    assert execution_validation_decision_hash(decision) == decision.artifact_hash


def test_blocked_provenance_maps_to_fail():
    evidence = provenance(
        status=BLOCKED,
        reasons=("STRATEGY_SHADOW_NOT_PASS",),
    )

    decision = evaluate_execution_validation_policy(
        evidence,
        policy(),
        execution_run_id="decision-run-a",
    )

    assert decision.validation_status == ValidationStatus.FAIL
    assert decision.reasons == (
        "PROVENANCE_BLOCKED",
        "STRATEGY_SHADOW_NOT_PASS",
    )


def test_tampered_provenance_hash_fails_closed():
    evidence = replace(
        provenance(),
        artifact_hash="0" * 64,
    )

    decision = evaluate_execution_validation_policy(
        evidence,
        policy(),
        execution_run_id="decision-run-a",
    )

    assert decision.validation_status == ValidationStatus.FAIL
    assert decision.reasons == (
        "PROVENANCE_ARTIFACT_HASH_MISMATCH",
    )


def test_decision_hash_ignores_run_ids_but_binds_semantics():
    first_evidence = provenance(
        execution_run_id="provenance-run-a",
    )
    second_evidence = replace(
        first_evidence,
        execution_run_id="provenance-run-b",
    )

    first = evaluate_execution_validation_policy(
        first_evidence,
        policy(),
        execution_run_id="decision-run-a",
    )
    second = evaluate_execution_validation_policy(
        second_evidence,
        policy(),
        execution_run_id="decision-run-b",
    )

    assert first.execution_run_id != second.execution_run_id
    assert first.provenance_run_id != second.provenance_run_id
    assert first.artifact_hash == second.artifact_hash


def test_v1_decision_cannot_be_manually_forged_as_pass():
    from std0_quant.execution.execution_validation_policy import (
        ExecutionValidationDecision,
        EXECUTION_VALIDATION_DECISION_SCHEMA_V1,
    )

    with pytest.raises(ValueError):
        ExecutionValidationDecision(
            execution_run_id="decision-run",
            provenance_run_id="provenance-run",
            target=target(),
            validation_status=ValidationStatus.PASS,
            reasons=(),
            provenance_artifact_hash="a" * 64,
            policy_id="execution-validation-policy",
            policy_version="1",
            policy_hash="b" * 64,
            artifact_hash="c" * 64,
            schema_version=EXECUTION_VALIDATION_DECISION_SCHEMA_V1,
        )


@pytest.mark.parametrize(
    "field,value",
    (
        ("policy_id", None),
        ("version", None),
    ),
)
def test_policy_identity_fields_require_actual_strings(field, value):
    values = {
        "policy_id": "execution-validation-policy",
        "version": "1",
        "required_pass_evidence_kind": MEASURED_VENUE_EXECUTION,
    }
    values[field] = value

    with pytest.raises((TypeError, ValueError)):
        ExecutionValidationPolicy(**values)


def test_execution_run_id_requires_actual_string():
    with pytest.raises((TypeError, ValueError)):
        evaluate_execution_validation_policy(
            provenance(),
            policy(),
            execution_run_id=None,
        )
