"""Deterministic execution-governance bridge v2.

Governance only. This additive bridge binds a validated factor-registry
snapshot to one ExecutionValidationDecisionV2 and its exact policy. It does
not mutate the registry, produce legacy PromotionEvidence, determine
production eligibility, submit orders, hold credentials, or enable LIVE.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from typing import Any

from std0_quant.execution.execution_validation import ExecutionValidationTarget
from std0_quant.execution.execution_validation_policy_v2 import (
    ExecutionValidationDecisionV2,
    ExecutionValidationPolicyV2,
    execution_validation_decision_v2_hash,
    execution_validation_policy_v2_hash,
)
from std0_quant.research.factors.contracts import FactorStatus, ValidationStatus
from std0_quant.research.factors.registry import (
    FactorRegistryRecord,
    registry_record_hash,
)
from std0_quant.storage import canonical_json


EXECUTION_GOVERNANCE_BRIDGE_V2 = "execution_governance_bridge_v2"
EXECUTION_GOVERNANCE_BRIDGE_ARTIFACT_SCHEMA_V2 = (
    "execution_governance_bridge_artifact_v2"
)

ACCEPTED = "ACCEPTED"
BLOCKED = "BLOCKED"


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
class ExecutionGovernanceBridgeArtifactV2:
    bridge_run_id: str
    target: ExecutionValidationTarget
    status: str
    reasons: tuple[str, ...]
    execution_validation_status: ValidationStatus | str
    registry_record_hash: str
    research_artifact_hash: str | None
    research_run_id: str | None
    research_policy_id: str | None
    research_policy_version: str | None
    research_policy_hash: str | None
    validation_evidence_bundle_hash: str | None
    execution_decision_artifact_hash: str
    execution_run_id: str
    provenance_run_id: str
    measured_evidence_run_id: str
    qualification_run_id: str
    attestation_run_id: str
    verification_run_id: str
    coverage_run_id: str
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
    bridge_version: str = EXECUTION_GOVERNANCE_BRIDGE_V2
    schema_version: str = EXECUTION_GOVERNANCE_BRIDGE_ARTIFACT_SCHEMA_V2

    def __post_init__(self) -> None:
        for name in (
            "bridge_run_id",
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

        for name in (
            "research_artifact_hash",
            "research_run_id",
            "research_policy_id",
            "research_policy_version",
            "research_policy_hash",
            "validation_evidence_bundle_hash",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _nonempty(value, name))

        if not isinstance(self.target, ExecutionValidationTarget):
            raise TypeError("target must be ExecutionValidationTarget")

        if self.status not in {ACCEPTED, BLOCKED}:
            raise ValueError("unsupported execution governance bridge status")

        reasons = tuple(_nonempty(reason, "reason") for reason in self.reasons)
        object.__setattr__(self, "reasons", reasons)

        if self.status == ACCEPTED and reasons:
            raise ValueError("ACCEPTED cannot contain reasons")
        if self.status == BLOCKED and not reasons:
            raise ValueError("BLOCKED requires reasons")

        research_provenance = (
            self.research_artifact_hash,
            self.research_run_id,
            self.research_policy_id,
            self.research_policy_version,
            self.research_policy_hash,
            self.validation_evidence_bundle_hash,
        )
        if self.status == ACCEPTED and any(
            value is None for value in research_provenance
        ):
            raise ValueError(
                "ACCEPTED requires complete research validation provenance"
            )

        try:
            validation_status = ValidationStatus(self.execution_validation_status)
        except ValueError as exc:
            raise ValueError("unsupported execution_validation_status") from exc
        object.__setattr__(
            self,
            "execution_validation_status",
            validation_status,
        )

        if self.status == ACCEPTED and validation_status != ValidationStatus.PASS:
            raise ValueError("ACCEPTED requires execution validation PASS")

        for name in (
            "registry_record_hash",
            "execution_decision_artifact_hash",
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

        if self.bridge_version != EXECUTION_GOVERNANCE_BRIDGE_V2:
            raise ValueError("unsupported bridge_version")
        if self.schema_version != EXECUTION_GOVERNANCE_BRIDGE_ARTIFACT_SCHEMA_V2:
            raise ValueError("unsupported schema_version")


def _artifact_payload(
    artifact: ExecutionGovernanceBridgeArtifactV2,
) -> dict[str, Any]:
    return {
        "bridge_run_id": artifact.bridge_run_id,
        "target": asdict(artifact.target),
        "status": artifact.status,
        "reasons": artifact.reasons,
        "execution_validation_status": artifact.execution_validation_status.value,
        "registry_record_hash": artifact.registry_record_hash,
        "research_artifact_hash": artifact.research_artifact_hash,
        "research_run_id": artifact.research_run_id,
        "research_policy_id": artifact.research_policy_id,
        "research_policy_version": artifact.research_policy_version,
        "research_policy_hash": artifact.research_policy_hash,
        "validation_evidence_bundle_hash": artifact.validation_evidence_bundle_hash,
        "execution_decision_artifact_hash": artifact.execution_decision_artifact_hash,
        "execution_run_id": artifact.execution_run_id,
        "provenance_run_id": artifact.provenance_run_id,
        "measured_evidence_run_id": artifact.measured_evidence_run_id,
        "qualification_run_id": artifact.qualification_run_id,
        "attestation_run_id": artifact.attestation_run_id,
        "verification_run_id": artifact.verification_run_id,
        "coverage_run_id": artifact.coverage_run_id,
        "provenance_artifact_hash": artifact.provenance_artifact_hash,
        "measured_execution_artifact_hash": artifact.measured_execution_artifact_hash,
        "source_qualification_artifact_hash": artifact.source_qualification_artifact_hash,
        "attestation_artifact_hash": artifact.attestation_artifact_hash,
        "signature_verification_artifact_hash": (
            artifact.signature_verification_artifact_hash
        ),
        "coverage_artifact_hash": artifact.coverage_artifact_hash,
        "source_artifact_hash": artifact.source_artifact_hash,
        "coverage_manifest_hash": artifact.coverage_manifest_hash,
        "source_qualification_policy_hash": (
            artifact.source_qualification_policy_hash
        ),
        "trusted_public_key_policy_hash": (
            artifact.trusted_public_key_policy_hash
        ),
        "policy_id": artifact.policy_id,
        "policy_version": artifact.policy_version,
        "policy_hash": artifact.policy_hash,
        "bridge_version": artifact.bridge_version,
        "schema_version": artifact.schema_version,
    }


def execution_governance_bridge_artifact_v2_hash(
    artifact: ExecutionGovernanceBridgeArtifactV2,
) -> str:
    if not isinstance(artifact, ExecutionGovernanceBridgeArtifactV2):
        raise TypeError(
            "artifact must be ExecutionGovernanceBridgeArtifactV2"
        )
    return hashlib.sha256(
        canonical_json(_artifact_payload(artifact)).encode("utf-8")
    ).hexdigest()


def evaluate_execution_governance_bridge_v2(
    record: FactorRegistryRecord,
    decision: ExecutionValidationDecisionV2,
    policy: ExecutionValidationPolicyV2,
    *,
    bridge_run_id: str,
) -> ExecutionGovernanceBridgeArtifactV2:
    if not isinstance(record, FactorRegistryRecord):
        raise TypeError("record must be FactorRegistryRecord")
    if not isinstance(decision, ExecutionValidationDecisionV2):
        raise TypeError("decision must be ExecutionValidationDecisionV2")
    if not isinstance(policy, ExecutionValidationPolicyV2):
        raise TypeError("policy must be ExecutionValidationPolicyV2")

    bridge_run_id = _nonempty(bridge_run_id, "bridge_run_id")
    reasons: list[str] = []

    def add(reason: str) -> None:
        if reason not in reasons:
            reasons.append(reason)

    if record.status != FactorStatus.VALIDATED:
        add("REGISTRY_NOT_VALIDATED")

    if (
        record.factor_id != decision.target.factor_id
        or record.factor_version != decision.target.factor_version
        or record.definition_hash != decision.target.definition_hash
    ):
        add("EXECUTION_TARGET_REGISTRY_IDENTITY_MISMATCH")

    if execution_validation_decision_v2_hash(decision) != decision.artifact_hash:
        add("EXECUTION_VALIDATION_DECISION_V2_HASH_MISMATCH")

    expected_policy_hash = execution_validation_policy_v2_hash(policy)
    if (
        decision.policy_id != policy.policy_id
        or decision.policy_version != policy.version
        or decision.policy_hash != expected_policy_hash
    ):
        add("EXECUTION_VALIDATION_POLICY_V2_PROVENANCE_MISMATCH")

    if (
        decision.source_qualification_policy_hash
        != policy.source_qualification_policy_hash
    ):
        add("SOURCE_QUALIFICATION_POLICY_HASH_MISMATCH")

    if (
        decision.trusted_public_key_policy_hash
        != policy.trusted_public_key_policy_hash
    ):
        add("TRUSTED_PUBLIC_KEY_POLICY_HASH_MISMATCH")

    transition = record.transitions[-1] if record.transitions else None
    if transition is None:
        add("VALIDATION_TRANSITION_MISSING")
    else:

        if (
            transition.status_before != FactorStatus.CANDIDATE
            or transition.status_after != FactorStatus.VALIDATED
        ):
            add("VALIDATION_TRANSITION_INVALID")

        if transition.research_validation_status != ValidationStatus.PASS:
            add("RESEARCH_VALIDATION_NOT_PASS")

        if transition.temporal_integrity != ValidationStatus.PASS:
            add("TEMPORAL_INTEGRITY_NOT_PASS")

        research_policy_values = (
            transition.research_policy_id,
            transition.research_policy_version,
            transition.research_policy_hash,
        )
        if not all(
            isinstance(value, str) and value.strip()
            for value in research_policy_values
        ):
            add("RESEARCH_POLICY_PROVENANCE_INCOMPLETE")

        if (
            not isinstance(transition.validation_evidence_bundle_hash, str)
            or not transition.validation_evidence_bundle_hash.strip()
        ):
            add("VALIDATION_EVIDENCE_BUNDLE_MISSING")

    if decision.validation_status != ValidationStatus.PASS:
        add("EXECUTION_VALIDATION_V2_NOT_PASS")
        for reason in decision.reasons:
            add(reason)

    status = BLOCKED if reasons else ACCEPTED

    provisional = ExecutionGovernanceBridgeArtifactV2(
        bridge_run_id=bridge_run_id,
        target=decision.target,
        status=status,
        reasons=tuple(reasons),
        execution_validation_status=decision.validation_status,
        registry_record_hash=registry_record_hash(record),
        research_artifact_hash=(
            transition.research_artifact_hash if transition is not None else None
        ),
        research_run_id=(
            transition.research_run_id if transition is not None else None
        ),
        research_policy_id=(
            transition.research_policy_id if transition is not None else None
        ),
        research_policy_version=(
            transition.research_policy_version if transition is not None else None
        ),
        research_policy_hash=(
            transition.research_policy_hash if transition is not None else None
        ),
        validation_evidence_bundle_hash=(
            transition.validation_evidence_bundle_hash
            if transition is not None
            else None
        ),
        execution_decision_artifact_hash=decision.artifact_hash,
        execution_run_id=decision.execution_run_id,
        provenance_run_id=decision.provenance_run_id,
        measured_evidence_run_id=decision.measured_evidence_run_id,
        qualification_run_id=decision.qualification_run_id,
        attestation_run_id=decision.attestation_run_id,
        verification_run_id=decision.verification_run_id,
        coverage_run_id=decision.coverage_run_id,
        provenance_artifact_hash=decision.provenance_artifact_hash,
        measured_execution_artifact_hash=decision.measured_execution_artifact_hash,
        source_qualification_artifact_hash=(
            decision.source_qualification_artifact_hash
        ),
        attestation_artifact_hash=decision.attestation_artifact_hash,
        signature_verification_artifact_hash=(
            decision.signature_verification_artifact_hash
        ),
        coverage_artifact_hash=decision.coverage_artifact_hash,
        source_artifact_hash=decision.source_artifact_hash,
        coverage_manifest_hash=decision.coverage_manifest_hash,
        source_qualification_policy_hash=(
            decision.source_qualification_policy_hash
        ),
        trusted_public_key_policy_hash=(
            decision.trusted_public_key_policy_hash
        ),
        policy_id=decision.policy_id,
        policy_version=decision.policy_version,
        policy_hash=decision.policy_hash,
        artifact_hash="0" * 64,
    )

    return ExecutionGovernanceBridgeArtifactV2(
        **{
            **provisional.__dict__,
            "artifact_hash": execution_governance_bridge_artifact_v2_hash(
                provisional
            ),
        }
    )


def verify_execution_governance_bridge_artifact_v2(
    artifact: ExecutionGovernanceBridgeArtifactV2,
    record: FactorRegistryRecord,
    decision: ExecutionValidationDecisionV2,
    policy: ExecutionValidationPolicyV2,
) -> tuple[str, ...]:
    if not isinstance(artifact, ExecutionGovernanceBridgeArtifactV2):
        raise TypeError(
            "artifact must be ExecutionGovernanceBridgeArtifactV2"
        )
    if not isinstance(record, FactorRegistryRecord):
        raise TypeError("record must be FactorRegistryRecord")
    if not isinstance(decision, ExecutionValidationDecisionV2):
        raise TypeError("decision must be ExecutionValidationDecisionV2")
    if not isinstance(policy, ExecutionValidationPolicyV2):
        raise TypeError("policy must be ExecutionValidationPolicyV2")

    reasons: list[str] = []

    def add(reason: str) -> None:
        if reason not in reasons:
            reasons.append(reason)

    if (
        execution_governance_bridge_artifact_v2_hash(artifact)
        != artifact.artifact_hash
    ):
        add("EXECUTION_GOVERNANCE_BRIDGE_V2_HASH_MISMATCH")

    expected = evaluate_execution_governance_bridge_v2(
        record,
        decision,
        policy,
        bridge_run_id=artifact.bridge_run_id,
    )
    if artifact != expected:
        add("EXECUTION_GOVERNANCE_BRIDGE_V2_SEMANTICS_MISMATCH")

    return tuple(reasons)
