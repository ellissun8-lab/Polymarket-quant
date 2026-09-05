import base64
from dataclasses import replace
import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)

from std0_quant.execution.clodds_mapping import CLODDS_MAPPING_VERSION_V1
from std0_quant.execution.clodds_shadow_protocol import (
    AUDITED_CLODDS_COMMIT_V1,
    CLODDS_SHADOW_PROTOCOL_V1,
)
from std0_quant.execution.contracts import (
    ORDER_EVENT_SCHEMA_V1,
    ORDER_INTENT_SCHEMA_V1,
)
from std0_quant.execution.execution_validation import (
    READY_FOR_POLICY_EVALUATION,
    ExecutionValidationArtifact,
    ExecutionValidationTarget,
    execution_validation_artifact_hash,
)
from std0_quant.execution.execution_validation_policy import (
    MEASURED_VENUE_EXECUTION,
)
from std0_quant.execution.execution_validation_policy_v2 import (
    EXECUTION_VALIDATION_DECISION_SCHEMA_V2,
    EXECUTION_VALIDATION_POLICY_SCHEMA_V2,
    ExecutionValidationDecisionV2,
    ExecutionValidationPolicyV2,
    evaluate_execution_validation_policy_v2,
    execution_validation_decision_v2_hash,
    execution_validation_policy_v2_hash,
)
from std0_quant.execution.measured_venue_acquisition_attestation import (
    ED25519,
    build_measured_venue_acquisition_attestation,
    measured_venue_acquisition_attestation_artifact_hash,
)
from std0_quant.execution.measured_venue_ed25519_verification import (
    SIGNATURE_VERIFIED,
    measured_venue_ed25519_signing_message,
    measured_venue_ed25519_verification_artifact_hash,
    verify_measured_venue_ed25519_signature,
)
from std0_quant.execution.measured_venue_execution import (
    build_measured_venue_execution_artifact,
    measured_venue_execution_artifact_hash,
)
from std0_quant.execution.measured_venue_source_qualification import (
    MeasuredVenueSourceQualificationPolicy,
    MeasuredVenueSourceRule,
    evaluate_measured_venue_source_qualification,
    measured_venue_source_qualification_policy_hash,
)
from std0_quant.execution.measured_venue_telemetry_bundle import (
    COVERAGE_COMPLETE,
    COVERAGE_INCOMPLETE,
    COVERAGE_MODE_ALL_TARGET_INTENTS_IN_WINDOW,
    FILTERING_MODE_NONE,
    MEASURED_VENUE_COVERAGE_MANIFEST_SCHEMA_V1,
    MEASURED_VENUE_TELEMETRY_BUNDLE_JSONL_V2,
    build_measured_venue_bundle_coverage_artifact,
    import_measured_venue_telemetry_bundle_jsonl,
    measured_venue_bundle_coverage_artifact_hash,
    measured_venue_intent_ids_hash,
)
from std0_quant.execution.measured_venue_trusted_public_key_policy import (
    MeasuredVenueTrustedPublicKeyPolicy,
    MeasuredVenueTrustedPublicKeyRule,
    measured_venue_trusted_public_key_policy_hash,
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


def bundle_raw(*, terminal=True):
    intent_id = "intent-1"
    event_type = "FILLED" if terminal else "VENUE_ACK"
    row = {
        "schema_version": "measured_venue_telemetry_jsonl_v1",
        "intent": {
            "intent_id": intent_id,
            "condition_id": "condition-1",
            "outcome": "YES",
            "side": "BUY",
            "qty": 10.0,
            "limit_price": 0.42,
            "time_in_force": "GTC",
            "decision_ts_ms": 1500.0,
            "market_data_ts_ms": 1499.0,
            "strategy_id": "alpha-a",
            "strategy_version": "1",
            "risk_policy_version": "risk-v1",
            "schema_version": ORDER_INTENT_SCHEMA_V1,
        },
        "events": [
            {
                "event_id": "event-1",
                "intent_id": intent_id,
                "event_type": event_type,
                "receive_ts_ms": 1502.0,
                "venue_ts_ms": 1501.0,
                "venue_order_id": "venue-order-1",
                "fill_qty": 10.0 if terminal else 0.0,
                "fill_price": 0.41 if terminal else None,
                "cumulative_filled_qty": 10.0 if terminal else 0.0,
                "remaining_qty": 0.0 if terminal else 10.0,
                "reason": None,
                "schema_version": ORDER_EVENT_SCHEMA_V1,
            }
        ],
    }
    manifest = {
        "schema_version": MEASURED_VENUE_TELEMETRY_BUNDLE_JSONL_V2,
        "coverage_manifest": {
            "target": {
                "factor_id": "factor-a",
                "factor_version": "1",
                "definition_hash": "a" * 64,
                "alpha_id": "alpha-a",
                "alpha_version": "1",
                "risk_policy_version": "risk-v1",
            },
            "window_start_ms": 1000.0,
            "window_end_ms": 2000.0,
            "coverage_mode": COVERAGE_MODE_ALL_TARGET_INTENTS_IN_WINDOW,
            "filtering_mode": FILTERING_MODE_NONE,
            "declared_intent_count": 1,
            "declared_intent_ids_hash": measured_venue_intent_ids_hash(
                (intent_id,)
            ),
            "require_terminal_lifecycle": True,
            "schema_version": MEASURED_VENUE_COVERAGE_MANIFEST_SCHEMA_V1,
        },
    }
    return (
        "\n".join(
            json.dumps(
                item,
                sort_keys=True,
                separators=(",", ":"),
            )
            for item in (manifest, row)
        )
        + "\n"
    ).encode("utf-8")


def source_policy(*, version="1"):
    return MeasuredVenueSourceQualificationPolicy(
        policy_id="source-policy",
        version=version,
        rules=(
            MeasuredVenueSourceRule(
                source_id="venue-export",
                source_version="2",
                collector_id="collector-a",
                collector_version="1",
                venue_id="polymarket",
                acquisition_mode="read-only-export",
            ),
        ),
    )


def policy_v2(source, trusted, **changes):
    values = {
        "policy_id": "execution-validation-policy",
        "version": "2",
        "required_pass_evidence_kind": MEASURED_VENUE_EXECUTION,
        "source_qualification_policy_id": source.policy_id,
        "source_qualification_policy_version": source.version,
        "source_qualification_policy_hash": (
            measured_venue_source_qualification_policy_hash(source)
        ),
        "trusted_public_key_policy_id": trusted.policy_id,
        "trusted_public_key_policy_version": trusted.version,
        "trusted_public_key_policy_hash": (
            measured_venue_trusted_public_key_policy_hash(trusted)
        ),
    }
    values.update(changes)
    return ExecutionValidationPolicyV2(**values)


def chain(*, terminal=True):
    raw = bundle_raw(terminal=terminal)
    imported = import_measured_venue_telemetry_bundle_jsonl(
        raw,
        source_id="venue-export",
        source_version="2",
        source_run_id="source-run",
    )
    upstream = provenance()
    measured = build_measured_venue_execution_artifact(
        upstream,
        imported.source,
        imported.observations,
        evidence_run_id="measured-run",
    )
    source = source_policy()
    qualification = evaluate_measured_venue_source_qualification(
        measured,
        source,
        collector_id="collector-a",
        collector_version="1",
        venue_id="polymarket",
        acquisition_mode="read-only-export",
        qualification_run_id="qualification-run",
    )

    private_key = Ed25519PrivateKey.generate()
    unsigned = build_measured_venue_acquisition_attestation(
        qualification,
        attestation_run_id="attestation-run",
        signer_id="collector-signer",
        key_id="key-1",
        signature_algorithm=ED25519,
        signature_b64=base64.b64encode(bytes(64)).decode("ascii"),
    )
    signature = private_key.sign(
        measured_venue_ed25519_signing_message(unsigned)
    )
    signed = replace(
        unsigned,
        signature_b64=base64.b64encode(signature).decode("ascii"),
        artifact_hash="PENDING",
    )
    attestation = replace(
        signed,
        artifact_hash=(
            measured_venue_acquisition_attestation_artifact_hash(signed)
        ),
    )
    public_key_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    trusted = MeasuredVenueTrustedPublicKeyPolicy(
        policy_id="trusted-key-policy",
        version="1",
        rules=(
            MeasuredVenueTrustedPublicKeyRule(
                signer_id=attestation.signer_id,
                key_id=attestation.key_id,
                signature_algorithm=ED25519,
                public_key_b64=base64.b64encode(public_key_raw).decode("ascii"),
                collector_id=attestation.collector_id,
                collector_version=attestation.collector_version,
                venue_id=attestation.venue_id,
                acquisition_mode=attestation.acquisition_mode,
            ),
        ),
    )
    verification = verify_measured_venue_ed25519_signature(
        attestation,
        qualification,
        trusted,
        verification_run_id="verification-run",
    )
    coverage = build_measured_venue_bundle_coverage_artifact(
        imported,
        coverage_run_id="coverage-run",
    )
    return {
        "raw_telemetry_bundle": raw,
        "telemetry_bundle": imported,
        "provenance": upstream,
        "measured_execution": measured,
        "source_qualification": qualification,
        "source_qualification_policy": source,
        "attestation": attestation,
        "trusted_public_key_policy": trusted,
        "signature_verification": verification,
        "coverage": coverage,
        "policy": policy_v2(source, trusted),
    }


def evaluate(items, **changes):
    values = dict(items)
    values.update(changes)
    return evaluate_execution_validation_policy_v2(
        **values,
        execution_run_id="policy-v2-run",
    )


def test_v2_contract_is_additive_and_complete_chain_can_pass():
    items = chain()
    decision = evaluate(items)

    assert EXECUTION_VALIDATION_POLICY_SCHEMA_V2 == "execution_validation_policy_v2"
    assert EXECUTION_VALIDATION_DECISION_SCHEMA_V2 == "execution_validation_decision_v2"
    assert isinstance(decision, ExecutionValidationDecisionV2)
    assert decision.validation_status == ValidationStatus.PASS
    assert decision.reasons == ()
    assert decision.target == target()
    assert items["signature_verification"].status == SIGNATURE_VERIFIED
    assert items["coverage"].status == COVERAGE_COMPLETE
    assert (
        execution_validation_decision_v2_hash(decision)
        == decision.artifact_hash
    )


def test_v2_recomputes_measured_execution_instead_of_trusting_ready():
    items = chain()
    measured = items["measured_execution"]
    bad_observation = replace(
        measured.observations[0],
        intent=replace(
            measured.observations[0].intent,
            strategy_id="different-alpha",
        ),
    )
    blocked = replace(
        measured,
        observations=(bad_observation,),
        artifact_hash="PENDING",
    )
    blocked = replace(
        blocked,
        artifact_hash=measured_venue_execution_artifact_hash(blocked),
    )
    forged_ready = replace(
        blocked,
        status=READY_FOR_POLICY_EVALUATION,
        reasons=(),
        artifact_hash="PENDING",
    )
    forged_ready = replace(
        forged_ready,
        artifact_hash=measured_venue_execution_artifact_hash(forged_ready),
    )

    decision = evaluate(items, measured_execution=forged_ready)

    assert decision.validation_status == ValidationStatus.FAIL
    assert "MEASURED_VENUE_EXECUTION_INVALID" in decision.reasons
    assert "MEASURED_VENUE_EXECUTION_STATUS_MISMATCH" in decision.reasons


def test_v2_rejects_raw_bundle_swap_even_when_artifacts_report_complete():
    items = chain()
    tampered = items["raw_telemetry_bundle"].replace(
        b"venue-order-1",
        b"venue-order-x",
    )

    decision = evaluate(items, raw_telemetry_bundle=tampered)

    assert decision.validation_status == ValidationStatus.FAIL
    assert "COVERAGE_INVALID" in decision.reasons
    assert "BUNDLE_SOURCE_ARTIFACT_HASH_MISMATCH" in decision.reasons


def test_v2_rejects_cross_bundle_observation_splicing():
    items = chain()
    forged = replace(
        items["measured_execution"],
        observations=(
            replace(
                items["measured_execution"].observations[0],
                events=(
                    replace(
                        items["measured_execution"].observations[0].events[0],
                        venue_order_id="different-order",
                    ),
                ),
            ),
        ),
        artifact_hash="PENDING",
    )
    forged = replace(
        forged,
        artifact_hash=measured_venue_execution_artifact_hash(forged),
    )

    decision = evaluate(items, measured_execution=forged)

    assert decision.validation_status == ValidationStatus.FAIL
    assert "MEASURED_EXECUTION_BUNDLE_OBSERVATIONS_MISMATCH" in decision.reasons


def test_v2_rejects_unpinned_source_qualification_policy():
    items = chain()
    different = source_policy(version="2")

    decision = evaluate(
        items,
        source_qualification_policy=different,
    )

    assert decision.validation_status == ValidationStatus.FAIL
    assert "SOURCE_QUALIFICATION_POLICY_NOT_PINNED" in decision.reasons


def test_v2_rejects_unpinned_trusted_key_policy():
    items = chain()
    different = replace(
        items["trusted_public_key_policy"],
        version="2",
    )

    decision = evaluate(
        items,
        trusted_public_key_policy=different,
    )

    assert decision.validation_status == ValidationStatus.FAIL
    assert "TRUSTED_PUBLIC_KEY_POLICY_NOT_PINNED" in decision.reasons


def test_v2_recomputes_signature_instead_of_trusting_verified():
    items = chain()
    bad_attestation = replace(
        items["attestation"],
        signature_b64=base64.b64encode(bytes(64)).decode("ascii"),
        artifact_hash="PENDING",
    )
    bad_attestation = replace(
        bad_attestation,
        artifact_hash=(
            measured_venue_acquisition_attestation_artifact_hash(
                bad_attestation
            )
        ),
    )
    forged = replace(
        items["signature_verification"],
        attestation_artifact_hash=bad_attestation.artifact_hash,
        status=SIGNATURE_VERIFIED,
        reasons=(),
        artifact_hash="PENDING",
    )
    forged = replace(
        forged,
        artifact_hash=(
            measured_venue_ed25519_verification_artifact_hash(forged)
        ),
    )

    decision = evaluate(
        items,
        attestation=bad_attestation,
        signature_verification=forged,
    )

    assert decision.validation_status == ValidationStatus.FAIL
    assert "ED25519_VERIFICATION_INVALID" in decision.reasons
    assert "VERIFICATION_STATUS_MISMATCH" in decision.reasons
    assert "ED25519_SIGNATURE_INVALID" in decision.reasons


def test_v2_recomputes_coverage_instead_of_trusting_complete():
    items = chain(terminal=False)
    forged = replace(
        items["coverage"],
        status=COVERAGE_COMPLETE,
        reasons=(),
        terminal_intent_count=items["coverage"].observed_intent_count,
        artifact_hash="0" * 64,
    )
    forged = replace(
        forged,
        artifact_hash=measured_venue_bundle_coverage_artifact_hash(forged),
    )

    decision = evaluate(items, coverage=forged)

    assert decision.validation_status == ValidationStatus.FAIL
    assert "COVERAGE_INVALID" in decision.reasons
    assert "COVERAGE_STATUS_MISMATCH" in decision.reasons
    assert "COVERAGE_REASONS_MISMATCH" in decision.reasons


def test_v2_incomplete_terminal_coverage_is_pending_not_pass():
    items = chain(terminal=False)

    decision = evaluate(items)

    assert items["coverage"].status == COVERAGE_INCOMPLETE
    assert decision.validation_status == ValidationStatus.PENDING
    assert decision.reasons == (
        "MEASURED_VENUE_COVERAGE_INCOMPLETE",
        "COVERAGE_TERMINAL_LIFECYCLE_INCOMPLETE",
    )


def test_v2_policy_and_decision_hashes_bind_semantics_not_run_ids():
    items = chain()
    policy = items["policy"]
    decision = evaluate(items)

    assert execution_validation_policy_v2_hash(policy) == (
        execution_validation_policy_v2_hash(replace(policy))
    )
    assert execution_validation_policy_v2_hash(policy) != (
        execution_validation_policy_v2_hash(
            replace(policy, version="different")
        )
    )

    changed_runs = replace(
        decision,
        execution_run_id="different-policy-run",
        provenance_run_id="different-provenance-run",
        measured_evidence_run_id="different-measured-run",
        qualification_run_id="different-qualification-run",
        attestation_run_id="different-attestation-run",
        verification_run_id="different-verification-run",
        coverage_run_id="different-coverage-run",
    )
    assert execution_validation_decision_v2_hash(changed_runs) == (
        decision.artifact_hash
    )

    changed_coverage = replace(
        decision,
        coverage_artifact_hash="f" * 64,
    )
    assert execution_validation_decision_v2_hash(changed_coverage) != (
        decision.artifact_hash
    )
