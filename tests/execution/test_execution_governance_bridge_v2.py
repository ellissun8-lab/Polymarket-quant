from dataclasses import replace

import pytest

from std0_quant.execution.execution_governance_bridge_v2 import (
    ACCEPTED,
    BLOCKED,
    ExecutionGovernanceBridgeArtifactV2,
    evaluate_execution_governance_bridge_v2,
    execution_governance_bridge_artifact_v2_hash,
    verify_execution_governance_bridge_artifact_v2,
)
from std0_quant.execution.execution_validation import ExecutionValidationTarget
from std0_quant.execution.execution_validation_policy import (
    MEASURED_VENUE_EXECUTION,
    ExecutionValidationDecision,
    ExecutionValidationPolicy,
)
from std0_quant.execution.execution_validation_policy_v2 import (
    ExecutionValidationDecisionV2,
    ExecutionValidationPolicyV2,
    execution_validation_decision_v2_hash,
    execution_validation_policy_v2_hash,
)
from std0_quant.research.factors.contracts import FactorStatus, ValidationStatus
from std0_quant.research.factors.registry import (
    FactorRegistryRecord,
    FactorTransition,
    registry_record_hash,
)


FACTOR_ID = "factor-a"
FACTOR_VERSION = "1"
DEFINITION_HASH = "a" * 64


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


def validated_record(**transition_changes):
    transition_values = {
        "status_before": FactorStatus.CANDIDATE,
        "status_after": FactorStatus.VALIDATED,
        "research_validation_status": ValidationStatus.PASS,
        "temporal_integrity": ValidationStatus.PASS,
        "research_artifact_hash": "b" * 64,
        "research_run_id": "research-run",
        "execution_validation_status": None,
        "execution_artifact_hash": None,
        "execution_run_id": None,
        "decided_at": "2026-09-01T00:01:00Z",
        "research_policy_id": "research-policy",
        "research_policy_version": "3",
        "research_policy_hash": "c" * 64,
        "research_validation_reasons": (),
        "validation_evidence_bundle_hash": "d" * 64,
    }
    transition_values.update(transition_changes)
    transition = FactorTransition(**transition_values)
    return FactorRegistryRecord(
        factor_id=FACTOR_ID,
        factor_version=FACTOR_VERSION,
        definition_hash=DEFINITION_HASH,
        status=FactorStatus.VALIDATED,
        created_by="test",
        created_at="2026-09-01T00:00:00Z",
        transitions=(transition,),
    )


def policy_v2(**changes):
    values = {
        "policy_id": "execution-validation-policy",
        "version": "2",
        "required_pass_evidence_kind": MEASURED_VENUE_EXECUTION,
        "source_qualification_policy_id": "source-policy",
        "source_qualification_policy_version": "1",
        "source_qualification_policy_hash": "1" * 64,
        "trusted_public_key_policy_id": "trusted-key-policy",
        "trusted_public_key_policy_version": "1",
        "trusted_public_key_policy_hash": "2" * 64,
    }
    values.update(changes)
    return ExecutionValidationPolicyV2(**values)


def decision_v2(
    *,
    validation_status=ValidationStatus.PASS,
    reasons=(),
    t=None,
    p=None,
    execution_run_id="execution-v2-run",
    **changes,
):
    p = p or policy_v2()
    values = {
        "execution_run_id": execution_run_id,
        "provenance_run_id": "provenance-run",
        "measured_evidence_run_id": "measured-run",
        "qualification_run_id": "qualification-run",
        "attestation_run_id": "attestation-run",
        "verification_run_id": "verification-run",
        "coverage_run_id": "coverage-run",
        "target": t or target(),
        "validation_status": validation_status,
        "reasons": tuple(reasons),
        "provenance_artifact_hash": "3" * 64,
        "measured_execution_artifact_hash": "4" * 64,
        "source_qualification_artifact_hash": "5" * 64,
        "attestation_artifact_hash": "6" * 64,
        "signature_verification_artifact_hash": "7" * 64,
        "coverage_artifact_hash": "8" * 64,
        "source_artifact_hash": "9" * 64,
        "coverage_manifest_hash": "a" * 64,
        "source_qualification_policy_hash": p.source_qualification_policy_hash,
        "trusted_public_key_policy_hash": p.trusted_public_key_policy_hash,
        "policy_id": p.policy_id,
        "policy_version": p.version,
        "policy_hash": execution_validation_policy_v2_hash(p),
        "artifact_hash": "0" * 64,
    }
    values.update(changes)
    provisional = ExecutionValidationDecisionV2(**values)
    return replace(
        provisional,
        artifact_hash=execution_validation_decision_v2_hash(provisional),
    )


def test_bridge_v2_accepts_only_validated_registry_and_execution_pass():
    record = validated_record()
    policy = policy_v2()
    decision = decision_v2(p=policy)

    artifact = evaluate_execution_governance_bridge_v2(
        record,
        decision,
        policy,
        bridge_run_id="bridge-v2-run",
    )

    assert artifact.status == ACCEPTED
    assert artifact.reasons == ()
    assert artifact.execution_validation_status == ValidationStatus.PASS
    assert artifact.registry_record_hash == registry_record_hash(record)
    transition = record.transitions[-1]
    assert artifact.research_artifact_hash == transition.research_artifact_hash
    assert artifact.research_run_id == transition.research_run_id
    assert artifact.research_policy_id == transition.research_policy_id
    assert artifact.research_policy_version == transition.research_policy_version
    assert artifact.research_policy_hash == transition.research_policy_hash
    assert (
        artifact.validation_evidence_bundle_hash
        == transition.validation_evidence_bundle_hash
    )
    assert artifact.execution_decision_artifact_hash == decision.artifact_hash
    assert artifact.policy_hash == decision.policy_hash
    assert (
        execution_governance_bridge_artifact_v2_hash(artifact)
        == artifact.artifact_hash
    )
    assert verify_execution_governance_bridge_artifact_v2(
        artifact,
        record,
        decision,
        policy,
    ) == ()


@pytest.mark.parametrize(
    "status,reasons",
    (
        (ValidationStatus.PENDING, ("COVERAGE_INCOMPLETE",)),
        (ValidationStatus.FAIL, ("PROVENANCE_BLOCKED",)),
    ),
)
def test_bridge_v2_blocks_non_pass_execution_decisions(status, reasons):
    policy = policy_v2()
    decision = decision_v2(
        validation_status=status,
        reasons=reasons,
        p=policy,
    )

    artifact = evaluate_execution_governance_bridge_v2(
        validated_record(),
        decision,
        policy,
        bridge_run_id="bridge-v2-run",
    )

    assert artifact.status == BLOCKED
    assert "EXECUTION_VALIDATION_V2_NOT_PASS" in artifact.reasons
    assert reasons[0] in artifact.reasons


@pytest.mark.parametrize(
    "field,value",
    (
        ("factor_id", "different-factor"),
        ("factor_version", "2"),
        ("definition_hash", "f" * 64),
    ),
)
def test_bridge_v2_blocks_target_registry_identity_mismatch(field, value):
    policy = policy_v2()
    decision = decision_v2(
        p=policy,
        t=target(**{field: value}),
    )

    artifact = evaluate_execution_governance_bridge_v2(
        validated_record(),
        decision,
        policy,
        bridge_run_id="bridge-v2-run",
    )

    assert artifact.status == BLOCKED
    assert "EXECUTION_TARGET_REGISTRY_IDENTITY_MISMATCH" in artifact.reasons


def test_bridge_v2_blocks_forged_execution_decision_hash():
    policy = policy_v2()
    decision = replace(
        decision_v2(p=policy),
        artifact_hash="f" * 64,
    )

    artifact = evaluate_execution_governance_bridge_v2(
        validated_record(),
        decision,
        policy,
        bridge_run_id="bridge-v2-run",
    )

    assert artifact.status == BLOCKED
    assert "EXECUTION_VALIDATION_DECISION_V2_HASH_MISMATCH" in artifact.reasons


def test_bridge_v2_blocks_execution_policy_provenance_mismatch():
    decision_policy = policy_v2(version="2")
    supplied_policy = policy_v2(version="3")
    decision = decision_v2(p=decision_policy)

    artifact = evaluate_execution_governance_bridge_v2(
        validated_record(),
        decision,
        supplied_policy,
        bridge_run_id="bridge-v2-run",
    )

    assert artifact.status == BLOCKED
    assert (
        "EXECUTION_VALIDATION_POLICY_V2_PROVENANCE_MISMATCH"
        in artifact.reasons
    )


def test_bridge_v2_blocks_source_policy_hash_splice():
    policy = policy_v2()
    provisional = replace(
        decision_v2(p=policy),
        source_qualification_policy_hash="e" * 64,
        artifact_hash="0" * 64,
    )
    decision = replace(
        provisional,
        artifact_hash=execution_validation_decision_v2_hash(provisional),
    )

    artifact = evaluate_execution_governance_bridge_v2(
        validated_record(),
        decision,
        policy,
        bridge_run_id="bridge-v2-run",
    )

    assert artifact.status == BLOCKED
    assert "SOURCE_QUALIFICATION_POLICY_HASH_MISMATCH" in artifact.reasons


def test_bridge_v2_blocks_trusted_key_policy_hash_splice():
    policy = policy_v2()
    provisional = replace(
        decision_v2(p=policy),
        trusted_public_key_policy_hash="e" * 64,
        artifact_hash="0" * 64,
    )
    decision = replace(
        provisional,
        artifact_hash=execution_validation_decision_v2_hash(provisional),
    )

    artifact = evaluate_execution_governance_bridge_v2(
        validated_record(),
        decision,
        policy,
        bridge_run_id="bridge-v2-run",
    )

    assert artifact.status == BLOCKED
    assert "TRUSTED_PUBLIC_KEY_POLICY_HASH_MISMATCH" in artifact.reasons


def test_bridge_v2_requires_real_validated_transition():
    record = FactorRegistryRecord(
        factor_id=FACTOR_ID,
        factor_version=FACTOR_VERSION,
        definition_hash=DEFINITION_HASH,
        status=FactorStatus.VALIDATED,
        created_by="test",
        created_at="2026-09-01T00:00:00Z",
        transitions=(),
    )
    policy = policy_v2()

    artifact = evaluate_execution_governance_bridge_v2(
        record,
        decision_v2(p=policy),
        policy,
        bridge_run_id="bridge-v2-run",
    )

    assert artifact.status == BLOCKED
    assert "VALIDATION_TRANSITION_MISSING" in artifact.reasons


def test_bridge_v2_rejects_v1_decision_and_policy_types():
    v1_policy = ExecutionValidationPolicy(
        policy_id="execution-validation-policy",
        version="1",
        required_pass_evidence_kind=MEASURED_VENUE_EXECUTION,
    )
    v1_decision = ExecutionValidationDecision(
        execution_run_id="execution-v1-run",
        provenance_run_id="provenance-run",
        target=target(),
        validation_status=ValidationStatus.PENDING,
        reasons=("MEASURED_VENUE_EXECUTION_EVIDENCE_MISSING",),
        provenance_artifact_hash="e" * 64,
        policy_id=v1_policy.policy_id,
        policy_version=v1_policy.version,
        policy_hash="f" * 64,
        artifact_hash="a" * 64,
    )

    with pytest.raises(TypeError, match="ExecutionValidationDecisionV2"):
        evaluate_execution_governance_bridge_v2(
            validated_record(),
            v1_decision,
            policy_v2(),
            bridge_run_id="bridge-v2-run",
        )

    with pytest.raises(TypeError, match="ExecutionValidationPolicyV2"):
        evaluate_execution_governance_bridge_v2(
            validated_record(),
            decision_v2(),
            v1_policy,
            bridge_run_id="bridge-v2-run",
        )


def test_bridge_v2_verifier_rejects_tampered_artifact_hash():
    record = validated_record()
    policy = policy_v2()
    decision = decision_v2(p=policy)
    artifact = evaluate_execution_governance_bridge_v2(
        record,
        decision,
        policy,
        bridge_run_id="bridge-v2-run",
    )
    tampered = replace(artifact, artifact_hash="f" * 64)

    reasons = verify_execution_governance_bridge_artifact_v2(
        tampered,
        record,
        decision,
        policy,
    )

    assert "EXECUTION_GOVERNANCE_BRIDGE_V2_HASH_MISMATCH" in reasons
    assert "EXECUTION_GOVERNANCE_BRIDGE_V2_SEMANTICS_MISMATCH" in reasons


def test_bridge_v2_verifier_rejects_forged_accepted_semantics_even_with_new_hash():
    record = validated_record()
    policy = policy_v2()
    decision = decision_v2(
        validation_status=ValidationStatus.FAIL,
        reasons=("PROVENANCE_BLOCKED",),
        p=policy,
    )
    blocked = evaluate_execution_governance_bridge_v2(
        record,
        decision,
        policy,
        bridge_run_id="bridge-v2-run",
    )
    forged = replace(
        blocked,
        status=ACCEPTED,
        reasons=(),
        execution_validation_status=ValidationStatus.PASS,
        artifact_hash="0" * 64,
    )
    forged = replace(
        forged,
        artifact_hash=execution_governance_bridge_artifact_v2_hash(forged),
    )

    reasons = verify_execution_governance_bridge_artifact_v2(
        forged,
        record,
        decision,
        policy,
    )

    assert "EXECUTION_GOVERNANCE_BRIDGE_V2_SEMANTICS_MISMATCH" in reasons


def test_bridge_v2_binds_decision_run_ids_against_cross_chain_splice():
    record = validated_record()
    policy = policy_v2()
    decision = decision_v2(p=policy)
    artifact = evaluate_execution_governance_bridge_v2(
        record,
        decision,
        policy,
        bridge_run_id="bridge-v2-run",
    )

    spliced = replace(
        artifact,
        coverage_run_id="different-coverage-run",
        artifact_hash="0" * 64,
    )
    spliced = replace(
        spliced,
        artifact_hash=execution_governance_bridge_artifact_v2_hash(spliced),
    )

    reasons = verify_execution_governance_bridge_artifact_v2(
        spliced,
        record,
        decision,
        policy,
    )

    assert "EXECUTION_GOVERNANCE_BRIDGE_V2_SEMANTICS_MISMATCH" in reasons
