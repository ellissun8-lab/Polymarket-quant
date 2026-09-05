"""Deterministic execution-validation policy v2.

This additive policy independently verifies the complete measured-venue trust
chain.  It does not trust cached READY/PASS status fields: every upstream
artifact is re-evaluated by its owning verifier and every adjacent binding is
checked again here.

Execution PASS is only an execution-validation result.  It is not production
eligibility, does not mutate the factor registry, does not submit orders, and
does not enable LIVE execution.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
from typing import Any

from std0_quant.execution.execution_validation import (
    BLOCKED,
    READY_FOR_POLICY_EVALUATION,
    ExecutionValidationArtifact,
    ExecutionValidationTarget,
    execution_validation_artifact_hash,
)
from std0_quant.execution.execution_validation_policy import (
    MEASURED_VENUE_EXECUTION,
)
from std0_quant.execution.measured_venue_acquisition_attestation import (
    BLOCKED_ATTESTATION,
    PENDING_SIGNATURE_VERIFICATION,
    MeasuredVenueAcquisitionAttestation,
    verify_measured_venue_acquisition_attestation,
)
from std0_quant.execution.measured_venue_ed25519_verification import (
    SIGNATURE_VERIFIED,
    MeasuredVenueEd25519Verification,
    verify_measured_venue_ed25519_verification_artifact,
)
from std0_quant.execution.measured_venue_execution import (
    MeasuredVenueExecutionArtifact,
    verify_measured_venue_execution_artifact,
)
from std0_quant.execution.measured_venue_source_qualification import (
    QUALIFIED,
    MeasuredVenueSourceQualification,
    MeasuredVenueSourceQualificationPolicy,
    measured_venue_source_qualification_policy_hash,
    verify_measured_venue_source_qualification,
)
from std0_quant.execution.measured_venue_telemetry_bundle import (
    COVERAGE_BLOCKED,
    COVERAGE_COMPLETE,
    COVERAGE_INCOMPLETE,
    MeasuredVenueBundleCoverageArtifact,
    MeasuredVenueTelemetryBundleImport,
    verify_measured_venue_bundle_coverage_artifact,
)
from std0_quant.execution.measured_venue_trusted_public_key_policy import (
    MeasuredVenueTrustedPublicKeyPolicy,
    measured_venue_trusted_public_key_policy_hash,
)
from std0_quant.research.factors.contracts import ValidationStatus
from std0_quant.storage import canonical_json


EXECUTION_VALIDATION_POLICY_SCHEMA_V2 = "execution_validation_policy_v2"
EXECUTION_VALIDATION_DECISION_SCHEMA_V2 = "execution_validation_decision_v2"


def _nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    text = value.strip()
    if not text:
        raise ValueError(f"{name} must be non-empty")
    return text


def _sha256(value: Any, name: str) -> str:
    text = _nonempty(value, name)
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise ValueError(f"{name} must be lowercase SHA256 hex")
    return text


@dataclass(frozen=True)
class ExecutionValidationPolicyV2:
    policy_id: str
    version: str
    required_pass_evidence_kind: str
    source_qualification_policy_id: str
    source_qualification_policy_version: str
    source_qualification_policy_hash: str
    trusted_public_key_policy_id: str
    trusted_public_key_policy_version: str
    trusted_public_key_policy_hash: str
    schema_version: str = EXECUTION_VALIDATION_POLICY_SCHEMA_V2

    def __post_init__(self) -> None:
        for name in (
            "policy_id",
            "version",
            "source_qualification_policy_id",
            "source_qualification_policy_version",
            "trusted_public_key_policy_id",
            "trusted_public_key_policy_version",
        ):
            object.__setattr__(
                self,
                name,
                _nonempty(getattr(self, name), name),
            )

        if self.required_pass_evidence_kind != MEASURED_VENUE_EXECUTION:
            raise ValueError(
                "execution PASS requires MEASURED_VENUE_EXECUTION evidence"
            )

        for name in (
            "source_qualification_policy_hash",
            "trusted_public_key_policy_hash",
        ):
            object.__setattr__(
                self,
                name,
                _sha256(getattr(self, name), name),
            )

        if self.schema_version != EXECUTION_VALIDATION_POLICY_SCHEMA_V2:
            raise ValueError(
                "unsupported ExecutionValidationPolicyV2 schema_version"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def execution_validation_policy_v2_hash(
    policy: ExecutionValidationPolicyV2,
) -> str:
    if not isinstance(policy, ExecutionValidationPolicyV2):
        raise TypeError("policy must be ExecutionValidationPolicyV2")
    return hashlib.sha256(
        canonical_json(policy.to_dict()).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class ExecutionValidationDecisionV2:
    execution_run_id: str
    provenance_run_id: str
    measured_evidence_run_id: str
    qualification_run_id: str
    attestation_run_id: str
    verification_run_id: str
    coverage_run_id: str
    target: ExecutionValidationTarget
    validation_status: ValidationStatus | str
    reasons: tuple[str, ...]
    provenance_artifact_hash: str
    measured_execution_artifact_hash: str
    source_qualification_artifact_hash: str
    attestation_artifact_hash: str
    signature_verification_artifact_hash: str
    coverage_artifact_hash: str
    source_artifact_hash: str
    coverage_manifest_hash: str
    source_qualification_policy_hash: str
    trusted_public_key_policy_hash: str
    policy_id: str
    policy_version: str
    policy_hash: str
    artifact_hash: str
    schema_version: str = EXECUTION_VALIDATION_DECISION_SCHEMA_V2

    def __post_init__(self) -> None:
        for name in (
            "execution_run_id",
            "provenance_run_id",
            "measured_evidence_run_id",
            "qualification_run_id",
            "attestation_run_id",
            "verification_run_id",
            "coverage_run_id",
            "policy_id",
            "policy_version",
        ):
            object.__setattr__(
                self,
                name,
                _nonempty(getattr(self, name), name),
            )

        if not isinstance(self.target, ExecutionValidationTarget):
            raise TypeError("target must be ExecutionValidationTarget")

        try:
            status = ValidationStatus(self.validation_status)
        except ValueError as exc:
            raise ValueError("unsupported validation_status") from exc
        object.__setattr__(self, "validation_status", status)

        reasons = tuple(_nonempty(reason, "reason") for reason in self.reasons)
        object.__setattr__(self, "reasons", reasons)

        if status == ValidationStatus.PASS and reasons:
            raise ValueError("execution PASS cannot contain reasons")
        if status in {ValidationStatus.PENDING, ValidationStatus.FAIL} and not reasons:
            raise ValueError("execution PENDING/FAIL requires reasons")

        for name in (
            "provenance_artifact_hash",
            "measured_execution_artifact_hash",
            "source_qualification_artifact_hash",
            "attestation_artifact_hash",
            "signature_verification_artifact_hash",
            "coverage_artifact_hash",
            "source_artifact_hash",
            "coverage_manifest_hash",
            "source_qualification_policy_hash",
            "trusted_public_key_policy_hash",
            "policy_hash",
            "artifact_hash",
        ):
            object.__setattr__(
                self,
                name,
                _sha256(getattr(self, name), name),
            )

        if self.schema_version != EXECUTION_VALIDATION_DECISION_SCHEMA_V2:
            raise ValueError(
                "unsupported ExecutionValidationDecisionV2 schema_version"
            )


def _decision_payload(
    decision: ExecutionValidationDecisionV2,
) -> dict[str, Any]:
    return {
        "target": asdict(decision.target),
        "validation_status": decision.validation_status.value,
        "reasons": decision.reasons,
        "provenance_artifact_hash": decision.provenance_artifact_hash,
        "measured_execution_artifact_hash": (
            decision.measured_execution_artifact_hash
        ),
        "source_qualification_artifact_hash": (
            decision.source_qualification_artifact_hash
        ),
        "attestation_artifact_hash": decision.attestation_artifact_hash,
        "signature_verification_artifact_hash": (
            decision.signature_verification_artifact_hash
        ),
        "coverage_artifact_hash": decision.coverage_artifact_hash,
        "source_artifact_hash": decision.source_artifact_hash,
        "coverage_manifest_hash": decision.coverage_manifest_hash,
        "source_qualification_policy_hash": (
            decision.source_qualification_policy_hash
        ),
        "trusted_public_key_policy_hash": (
            decision.trusted_public_key_policy_hash
        ),
        "policy_id": decision.policy_id,
        "policy_version": decision.policy_version,
        "policy_hash": decision.policy_hash,
        "schema_version": decision.schema_version,
    }


def execution_validation_decision_v2_hash(
    decision: ExecutionValidationDecisionV2,
) -> str:
    if not isinstance(decision, ExecutionValidationDecisionV2):
        raise TypeError("decision must be ExecutionValidationDecisionV2")
    return hashlib.sha256(
        canonical_json(_decision_payload(decision)).encode("utf-8")
    ).hexdigest()


def evaluate_execution_validation_policy_v2(
    *,
    raw_telemetry_bundle: bytes,
    telemetry_bundle: MeasuredVenueTelemetryBundleImport,
    provenance: ExecutionValidationArtifact,
    measured_execution: MeasuredVenueExecutionArtifact,
    source_qualification: MeasuredVenueSourceQualification,
    source_qualification_policy: MeasuredVenueSourceQualificationPolicy,
    attestation: MeasuredVenueAcquisitionAttestation,
    trusted_public_key_policy: MeasuredVenueTrustedPublicKeyPolicy,
    signature_verification: MeasuredVenueEd25519Verification,
    coverage: MeasuredVenueBundleCoverageArtifact,
    policy: ExecutionValidationPolicyV2,
    execution_run_id: str,
) -> ExecutionValidationDecisionV2:
    """Re-evaluate a complete measured-venue trust chain and decide v2 status."""

    expected_types = (
        (telemetry_bundle, MeasuredVenueTelemetryBundleImport, "telemetry_bundle"),
        (provenance, ExecutionValidationArtifact, "provenance"),
        (measured_execution, MeasuredVenueExecutionArtifact, "measured_execution"),
        (
            source_qualification,
            MeasuredVenueSourceQualification,
            "source_qualification",
        ),
        (
            source_qualification_policy,
            MeasuredVenueSourceQualificationPolicy,
            "source_qualification_policy",
        ),
        (
            attestation,
            MeasuredVenueAcquisitionAttestation,
            "attestation",
        ),
        (
            trusted_public_key_policy,
            MeasuredVenueTrustedPublicKeyPolicy,
            "trusted_public_key_policy",
        ),
        (
            signature_verification,
            MeasuredVenueEd25519Verification,
            "signature_verification",
        ),
        (coverage, MeasuredVenueBundleCoverageArtifact, "coverage"),
        (policy, ExecutionValidationPolicyV2, "policy"),
    )
    for value, expected, name in expected_types:
        if not isinstance(value, expected):
            raise TypeError(f"{name} must be {expected.__name__}")
    if not isinstance(raw_telemetry_bundle, bytes):
        raise TypeError("raw_telemetry_bundle must be bytes")
    execution_run_id = _nonempty(execution_run_id, "execution_run_id")

    failures: list[str] = []
    pending: list[str] = []

    def add(items: list[str], reason: str) -> None:
        if reason not in items:
            items.append(reason)

    def add_verifier_failures(label: str, reasons: tuple[str, ...]) -> None:
        if reasons:
            add(failures, label)
            for reason in reasons:
                add(failures, reason)

    if execution_validation_artifact_hash(provenance) != provenance.artifact_hash:
        add(failures, "PROVENANCE_ARTIFACT_HASH_MISMATCH")
    if provenance.status == BLOCKED:
        add(failures, "PROVENANCE_BLOCKED")
        for reason in provenance.reasons:
            add(failures, reason)
    elif provenance.status != READY_FOR_POLICY_EVALUATION:
        add(failures, "PROVENANCE_STATUS_UNSUPPORTED")

    measured_reasons = verify_measured_venue_execution_artifact(
        measured_execution,
        provenance,
    )
    add_verifier_failures(
        "MEASURED_VENUE_EXECUTION_INVALID",
        measured_reasons,
    )
    if measured_execution.status != READY_FOR_POLICY_EVALUATION:
        add(failures, "MEASURED_VENUE_EXECUTION_NOT_READY")
        for reason in measured_execution.reasons:
            add(failures, reason)

    if measured_execution.source != telemetry_bundle.source:
        add(failures, "MEASURED_EXECUTION_BUNDLE_SOURCE_MISMATCH")
    if measured_execution.observations != telemetry_bundle.observations:
        add(failures, "MEASURED_EXECUTION_BUNDLE_OBSERVATIONS_MISMATCH")
    if measured_execution.target != telemetry_bundle.manifest.target:
        add(failures, "MEASURED_EXECUTION_BUNDLE_TARGET_MISMATCH")

    source_policy_hash = measured_venue_source_qualification_policy_hash(
        source_qualification_policy
    )
    if (
        source_qualification_policy.policy_id
        != policy.source_qualification_policy_id
        or source_qualification_policy.version
        != policy.source_qualification_policy_version
        or source_policy_hash
        != policy.source_qualification_policy_hash
    ):
        add(failures, "SOURCE_QUALIFICATION_POLICY_NOT_PINNED")

    qualification_reasons = verify_measured_venue_source_qualification(
        source_qualification,
        measured_execution,
        source_qualification_policy,
    )
    add_verifier_failures(
        "SOURCE_QUALIFICATION_INVALID",
        qualification_reasons,
    )
    if source_qualification.status != QUALIFIED:
        add(failures, "SOURCE_NOT_QUALIFIED")
        for reason in source_qualification.reasons:
            add(failures, reason)

    attestation_reasons = verify_measured_venue_acquisition_attestation(
        attestation,
        source_qualification,
    )
    add_verifier_failures(
        "ACQUISITION_ATTESTATION_INVALID",
        attestation_reasons,
    )
    if attestation.status != PENDING_SIGNATURE_VERIFICATION:
        add(failures, "ACQUISITION_ATTESTATION_NOT_VERIFIABLE")
        if attestation.status == BLOCKED_ATTESTATION:
            for reason in attestation.reasons:
                add(failures, reason)

    trusted_policy_hash = measured_venue_trusted_public_key_policy_hash(
        trusted_public_key_policy
    )
    if (
        trusted_public_key_policy.policy_id
        != policy.trusted_public_key_policy_id
        or trusted_public_key_policy.version
        != policy.trusted_public_key_policy_version
        or trusted_policy_hash
        != policy.trusted_public_key_policy_hash
    ):
        add(failures, "TRUSTED_PUBLIC_KEY_POLICY_NOT_PINNED")

    verification_reasons = (
        verify_measured_venue_ed25519_verification_artifact(
            signature_verification,
            attestation,
            source_qualification,
            trusted_public_key_policy,
        )
    )
    add_verifier_failures(
        "ED25519_VERIFICATION_INVALID",
        verification_reasons,
    )
    if signature_verification.status != SIGNATURE_VERIFIED:
        add(failures, "SIGNATURE_NOT_VERIFIED")
        for reason in signature_verification.reasons:
            add(failures, reason)

    coverage_reasons = verify_measured_venue_bundle_coverage_artifact(
        raw_telemetry_bundle,
        coverage,
        telemetry_bundle,
    )
    add_verifier_failures("COVERAGE_INVALID", coverage_reasons)

    if coverage.status == COVERAGE_INCOMPLETE:
        add(pending, "MEASURED_VENUE_COVERAGE_INCOMPLETE")
        for reason in coverage.reasons:
            add(pending, reason)
    elif coverage.status == COVERAGE_BLOCKED:
        add(failures, "MEASURED_VENUE_COVERAGE_BLOCKED")
        for reason in coverage.reasons:
            add(failures, reason)
    elif coverage.status != COVERAGE_COMPLETE:
        add(failures, "MEASURED_VENUE_COVERAGE_STATUS_UNSUPPORTED")

    if failures:
        status = ValidationStatus.FAIL
        reasons = tuple(failures)
    elif pending:
        status = ValidationStatus.PENDING
        reasons = tuple(pending)
    else:
        status = ValidationStatus.PASS
        reasons = ()

    provisional = ExecutionValidationDecisionV2(
        execution_run_id=execution_run_id,
        provenance_run_id=provenance.execution_run_id,
        measured_evidence_run_id=measured_execution.evidence_run_id,
        qualification_run_id=source_qualification.qualification_run_id,
        attestation_run_id=attestation.attestation_run_id,
        verification_run_id=signature_verification.verification_run_id,
        coverage_run_id=coverage.coverage_run_id,
        target=provenance.target,
        validation_status=status,
        reasons=reasons,
        provenance_artifact_hash=provenance.artifact_hash,
        measured_execution_artifact_hash=measured_execution.artifact_hash,
        source_qualification_artifact_hash=source_qualification.artifact_hash,
        attestation_artifact_hash=attestation.artifact_hash,
        signature_verification_artifact_hash=(
            signature_verification.artifact_hash
        ),
        coverage_artifact_hash=coverage.artifact_hash,
        source_artifact_hash=telemetry_bundle.source.source_artifact_hash,
        coverage_manifest_hash=coverage.manifest_hash,
        source_qualification_policy_hash=source_policy_hash,
        trusted_public_key_policy_hash=trusted_policy_hash,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        policy_hash=execution_validation_policy_v2_hash(policy),
        artifact_hash="0" * 64,
    )
    return replace(
        provisional,
        artifact_hash=execution_validation_decision_v2_hash(provisional),
    )
