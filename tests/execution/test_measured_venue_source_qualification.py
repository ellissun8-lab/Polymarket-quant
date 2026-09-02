from dataclasses import replace

import pytest

from std0_quant.execution.clodds_mapping import CLODDS_MAPPING_VERSION_V1
from std0_quant.execution.clodds_shadow_protocol import (
    AUDITED_CLODDS_COMMIT_V1,
    CLODDS_SHADOW_PROTOCOL_V1,
)
from std0_quant.execution.contracts import OrderEvent, OrderEventType, OrderIntent
from std0_quant.execution.execution_validation import (
    BLOCKED,
    READY_FOR_POLICY_EVALUATION,
    ExecutionValidationArtifact,
    ExecutionValidationTarget,
    execution_validation_artifact_hash,
)
from std0_quant.execution.measured_venue_execution import (
    MeasuredVenueExecutionObservation,
    MeasuredVenueExecutionSource,
    build_measured_venue_execution_artifact,
    measured_venue_execution_artifact_hash,
)
from std0_quant.execution.measured_venue_source_qualification import (
    QUALIFIED,
    MeasuredVenueSourceQualificationPolicy,
    MeasuredVenueSourceRule,
    evaluate_measured_venue_source_qualification,
    measured_venue_source_qualification_hash,
    measured_venue_source_qualification_policy_hash,
)


def target():
    return ExecutionValidationTarget(
        factor_id="factor-a",
        factor_version="1",
        definition_hash="a" * 64,
        alpha_id="alpha-a",
        alpha_version="1",
        risk_policy_version="risk-v1",
    )


def provenance():
    provisional = ExecutionValidationArtifact(
        execution_run_id="provenance-run",
        target=target(),
        status=READY_FOR_POLICY_EVALUATION,
        reasons=(),
        alpha_factory_artifact_hash="b" * 64,
        strategy_candidate_hash="c" * 64,
        strategy_shadow_artifact_hash="d" * 64,
        strategy_shadow_run_id="shadow-run",
        protocol_version=CLODDS_SHADOW_PROTOCOL_V1,
        clodds_commit=AUDITED_CLODDS_COMMIT_V1,
        mapping_version=CLODDS_MAPPING_VERSION_V1,
        artifact_hash="PENDING",
    )
    return replace(
        provisional,
        artifact_hash=execution_validation_artifact_hash(provisional),
    )


def measured_evidence():
    p = provenance()
    source = MeasuredVenueExecutionSource(
        source_id="venue-export",
        source_version="1",
        source_run_id="source-run-001",
        source_artifact_hash="e" * 64,
    )
    intent = OrderIntent(
        intent_id="intent-1",
        condition_id="condition-1",
        outcome="YES",
        side="BUY",
        qty=10,
        limit_price=0.50,
        time_in_force="GTC",
        decision_ts_ms=1000,
        market_data_ts_ms=999,
        strategy_id="alpha-a",
        strategy_version="1",
        risk_policy_version="risk-v1",
    )
    ack = OrderEvent(
        event_id="event-ack",
        intent_id="intent-1",
        event_type=OrderEventType.VENUE_ACK,
        receive_ts_ms=1004,
        venue_ts_ms=1003,
        venue_order_id="venue-order-1",
        fill_qty=0,
        fill_price=None,
        cumulative_filled_qty=0,
        remaining_qty=10,
        reason=None,
    )
    obs = MeasuredVenueExecutionObservation(
        intent=intent,
        events=(ack,),
    )
    return build_measured_venue_execution_artifact(
        p,
        source,
        (obs,),
        evidence_run_id="measured-run-001",
    )


def rule(**changes):
    values = {
        "source_id": "venue-export",
        "source_version": "1",
        "collector_id": "collector-a",
        "collector_version": "1",
        "venue_id": "polymarket",
        "acquisition_mode": "read-only-export",
    }
    values.update(changes)
    return MeasuredVenueSourceRule(**values)


def policy(*rules):
    return MeasuredVenueSourceQualificationPolicy(
        policy_id="measured-source-policy",
        version="1",
        rules=tuple(rules or (rule(),)),
    )


def evaluate(evidence=None, p=None, **changes):
    values = {
        "collector_id": "collector-a",
        "collector_version": "1",
        "venue_id": "polymarket",
        "acquisition_mode": "read-only-export",
        "qualification_run_id": "qualification-run-001",
    }
    values.update(changes)
    return evaluate_measured_venue_source_qualification(
        evidence or measured_evidence(),
        p or policy(),
        **values,
    )


def test_matching_policy_qualifies_and_binds_exact_evidence_and_source_run():
    evidence = measured_evidence()
    qualification = evaluate(evidence=evidence)

    assert qualification.status == QUALIFIED
    assert qualification.reasons == ()
    assert qualification.evidence_run_id == evidence.evidence_run_id
    assert qualification.evidence_artifact_hash == evidence.artifact_hash
    assert qualification.source_run_id == evidence.source.source_run_id
    assert qualification.source_artifact_hash == evidence.source.source_artifact_hash
    assert qualification.policy_hash == measured_venue_source_qualification_policy_hash(policy())
    assert measured_venue_source_qualification_hash(qualification) == qualification.artifact_hash


def test_tampered_measured_evidence_hash_is_blocked():
    evidence = replace(
        measured_evidence(),
        artifact_hash="0" * 64,
    )

    qualification = evaluate(evidence=evidence)

    assert qualification.status == BLOCKED
    assert "MEASURED_EVIDENCE_ARTIFACT_HASH_MISMATCH" in qualification.reasons


def test_blocked_measured_evidence_cannot_be_washed_by_qualification():
    evidence = measured_evidence()
    provisional = replace(
        evidence,
        status=BLOCKED,
        reasons=("UPSTREAM_BLOCKED",),
        artifact_hash="PENDING",
    )
    blocked = replace(
        provisional,
        artifact_hash=measured_venue_execution_artifact_hash(provisional),
    )

    qualification = evaluate(evidence=blocked)

    assert qualification.status == BLOCKED
    assert "MEASURED_EVIDENCE_BLOCKED" in qualification.reasons
    assert "UPSTREAM_BLOCKED" in qualification.reasons


@pytest.mark.parametrize(
    "field,value",
    (
        ("collector_id", "collector-b"),
        ("collector_version", "2"),
        ("venue_id", "other-venue"),
        ("acquisition_mode", "unknown-mode"),
    ),
)
def test_unapproved_collection_identity_is_blocked(field, value):
    qualification = evaluate(**{field: value})

    assert qualification.status == BLOCKED
    assert "SOURCE_NOT_ALLOWED_BY_POLICY" in qualification.reasons


def test_duplicate_policy_rules_are_rejected():
    duplicate = rule()

    with pytest.raises(ValueError):
        policy(duplicate, duplicate)


def test_policy_hash_is_order_insensitive_for_allowlist_rules():
    first = rule()
    second = rule(
        source_id="venue-export-b",
        collector_id="collector-b",
    )

    left = policy(first, second)
    right = policy(second, first)

    assert (
        measured_venue_source_qualification_policy_hash(left)
        == measured_venue_source_qualification_policy_hash(right)
    )


def test_qualification_hash_binds_source_run_id():
    qualification = evaluate()

    changed = replace(
        qualification,
        source_run_id="different-source-run",
    )

    assert (
        measured_venue_source_qualification_hash(changed)
        != qualification.artifact_hash
    )


def test_qualification_hash_binds_evidence_artifact_hash():
    qualification = evaluate()

    changed = replace(
        qualification,
        evidence_artifact_hash="f" * 64,
    )

    assert (
        measured_venue_source_qualification_hash(changed)
        != qualification.artifact_hash
    )


def test_qualification_hash_excludes_only_qualification_run_identity():
    qualification = evaluate()

    changed = replace(
        qualification,
        qualification_run_id="different-qualification-run",
    )

    assert (
        measured_venue_source_qualification_hash(changed)
        == qualification.artifact_hash
    )


def test_ambiguous_source_identity_rules_are_rejected():
    first = rule()
    conflicting = rule(
        collector_id="collector-b",
        venue_id="other-venue",
    )

    with pytest.raises(ValueError):
        policy(first, conflicting)


def test_invalid_source_artifact_hash_is_blocked():
    evidence = measured_evidence()
    changed_source = replace(
        evidence.source,
        source_artifact_hash="not-a-sha256",
    )
    provisional = replace(
        evidence,
        source=changed_source,
        artifact_hash="PENDING",
    )
    changed = replace(
        provisional,
        artifact_hash=measured_venue_execution_artifact_hash(
            provisional
        ),
    )

    qualification = evaluate(evidence=changed)

    assert qualification.status == BLOCKED
    assert "SOURCE_ARTIFACT_HASH_INVALID" in qualification.reasons


def test_verifier_detects_source_run_tampering_even_with_rehashed_qualification():
    from std0_quant.execution.measured_venue_source_qualification import (
        verify_measured_venue_source_qualification,
    )

    evidence = measured_evidence()
    qualification = evaluate(evidence=evidence)

    provisional = replace(
        qualification,
        source_run_id="forged-source-run",
        artifact_hash="PENDING",
    )
    forged = replace(
        provisional,
        artifact_hash=measured_venue_source_qualification_hash(
            provisional
        ),
    )

    reasons = verify_measured_venue_source_qualification(
        forged,
        evidence,
        policy(),
    )

    assert "QUALIFICATION_SOURCE_RUN_ID_MISMATCH" in reasons


def test_verifier_accepts_evaluator_produced_qualification():
    from std0_quant.execution.measured_venue_source_qualification import (
        verify_measured_venue_source_qualification,
    )

    evidence = measured_evidence()
    qualification = evaluate(evidence=evidence)

    assert (
        verify_measured_venue_source_qualification(
            qualification,
            evidence,
            policy(),
        )
        == ()
    )


def test_verifier_rejects_rehashed_qualification_for_blocked_evidence():
    from std0_quant.execution.measured_venue_source_qualification import (
        verify_measured_venue_source_qualification,
    )

    evidence = measured_evidence()
    qualification = evaluate(evidence=evidence)

    provisional_blocked = replace(
        evidence,
        status=BLOCKED,
        reasons=("UPSTREAM_BLOCKED",),
        artifact_hash="PENDING",
    )
    blocked_evidence = replace(
        provisional_blocked,
        artifact_hash=measured_venue_execution_artifact_hash(
            provisional_blocked
        ),
    )

    provisional_qualification = replace(
        qualification,
        evidence_artifact_hash=blocked_evidence.artifact_hash,
        artifact_hash="PENDING",
    )
    forged_qualification = replace(
        provisional_qualification,
        artifact_hash=measured_venue_source_qualification_hash(
            provisional_qualification
        ),
    )

    reasons = verify_measured_venue_source_qualification(
        forged_qualification,
        blocked_evidence,
        policy(),
    )

    assert "MEASURED_EVIDENCE_BLOCKED" in reasons
    assert "UPSTREAM_BLOCKED" in reasons


def test_verifier_rejects_qualification_artifact_hash_tampering():
    from std0_quant.execution.measured_venue_source_qualification import (
        verify_measured_venue_source_qualification,
    )

    evidence = measured_evidence()
    qualification = replace(
        evaluate(evidence=evidence),
        artifact_hash="0" * 64,
    )

    reasons = verify_measured_venue_source_qualification(
        qualification,
        evidence,
        policy(),
    )

    assert "QUALIFICATION_ARTIFACT_HASH_MISMATCH" in reasons


def test_qualification_hash_binds_policy_hash():
    qualification = evaluate()

    changed = replace(
        qualification,
        policy_hash="f" * 64,
    )

    assert (
        measured_venue_source_qualification_hash(changed)
        != qualification.artifact_hash
    )


def test_policy_hash_changes_when_source_governance_changes():
    baseline = policy()
    changed = policy(
        rule(
            acquisition_mode="audited-read-only-export",
        ),
    )

    assert (
        measured_venue_source_qualification_policy_hash(baseline)
        != measured_venue_source_qualification_policy_hash(changed)
    )
