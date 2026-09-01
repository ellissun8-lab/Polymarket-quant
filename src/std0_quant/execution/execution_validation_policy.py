"""Deterministic execution-validation policy v1.

This layer converts execution-validation provenance evidence into a formal
ValidationStatus decision.

Important governance boundary:
- synthetic SHADOW evidence cannot produce execution PASS;
- deterministic simulation and assumed latency cannot produce execution PASS;
- PASS requires qualifying measured venue execution evidence;
- no such measured-evidence producer is wired into v1, so PASS is intentionally
  unreachable from evaluate_execution_validation_policy();
- this module does not mutate the Factor Registry or promote factors.
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
from std0_quant.research.factors.contracts import ValidationStatus
from std0_quant.storage import canonical_json


EXECUTION_VALIDATION_POLICY_SCHEMA_V1 = "execution_validation_policy_v1"
EXECUTION_VALIDATION_DECISION_SCHEMA_V1 = "execution_validation_decision_v1"

MEASURED_VENUE_EXECUTION = "MEASURED_VENUE_EXECUTION"


def _nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    text = value.strip()
    if not text:
        raise ValueError(f"{name} must be non-empty")
    return text


@dataclass(frozen=True)
class ExecutionValidationPolicy:
    policy_id: str
    version: str
    required_pass_evidence_kind: str
    schema_version: str = EXECUTION_VALIDATION_POLICY_SCHEMA_V1

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "policy_id",
            _nonempty(self.policy_id, "policy_id"),
        )
        object.__setattr__(
            self,
            "version",
            _nonempty(self.version, "version"),
        )
        object.__setattr__(
            self,
            "required_pass_evidence_kind",
            _nonempty(
                self.required_pass_evidence_kind,
                "required_pass_evidence_kind",
            ),
        )

        if self.required_pass_evidence_kind != MEASURED_VENUE_EXECUTION:
            raise ValueError(
                "execution PASS requires MEASURED_VENUE_EXECUTION evidence"
            )

        if self.schema_version != EXECUTION_VALIDATION_POLICY_SCHEMA_V1:
            raise ValueError(
                "unsupported ExecutionValidationPolicy schema_version"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def execution_validation_policy_hash(
    policy: ExecutionValidationPolicy,
) -> str:
    if not isinstance(policy, ExecutionValidationPolicy):
        raise TypeError("policy must be ExecutionValidationPolicy")

    payload = canonical_json(policy.to_dict())
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ExecutionValidationDecision:
    execution_run_id: str
    provenance_run_id: str
    target: ExecutionValidationTarget
    validation_status: ValidationStatus | str
    reasons: tuple[str, ...]
    provenance_artifact_hash: str
    policy_id: str
    policy_version: str
    policy_hash: str
    artifact_hash: str
    schema_version: str = EXECUTION_VALIDATION_DECISION_SCHEMA_V1

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "execution_run_id",
            _nonempty(self.execution_run_id, "execution_run_id"),
        )
        object.__setattr__(
            self,
            "provenance_run_id",
            _nonempty(self.provenance_run_id, "provenance_run_id"),
        )

        if not isinstance(self.target, ExecutionValidationTarget):
            raise TypeError("target must be ExecutionValidationTarget")

        try:
            status = ValidationStatus(self.validation_status)
        except ValueError as exc:
            raise ValueError("unsupported validation_status") from exc

        object.__setattr__(self, "validation_status", status)

        reasons = tuple(
            _nonempty(reason, "reason")
            for reason in self.reasons
        )
        object.__setattr__(self, "reasons", reasons)

        object.__setattr__(
            self,
            "provenance_artifact_hash",
            _nonempty(
                self.provenance_artifact_hash,
                "provenance_artifact_hash",
            ),
        )
        object.__setattr__(
            self,
            "policy_id",
            _nonempty(self.policy_id, "policy_id"),
        )
        object.__setattr__(
            self,
            "policy_version",
            _nonempty(self.policy_version, "policy_version"),
        )
        object.__setattr__(
            self,
            "policy_hash",
            _nonempty(self.policy_hash, "policy_hash"),
        )
        object.__setattr__(
            self,
            "artifact_hash",
            _nonempty(self.artifact_hash, "artifact_hash"),
        )

        if status == ValidationStatus.PASS:
            raise ValueError(
                "execution PASS is unreachable in policy v1 without "
                "qualifying measured venue execution evidence"
            )
        if status in {ValidationStatus.PENDING, ValidationStatus.FAIL} and not reasons:
            raise ValueError(
                "execution PENDING/FAIL requires reasons"
            )

        if self.schema_version != EXECUTION_VALIDATION_DECISION_SCHEMA_V1:
            raise ValueError(
                "unsupported ExecutionValidationDecision schema_version"
            )


def _decision_payload(
    decision: ExecutionValidationDecision,
) -> dict[str, Any]:
    return {
        "target": asdict(decision.target),
        "validation_status": decision.validation_status.value,
        "reasons": decision.reasons,
        "provenance_artifact_hash": (
            decision.provenance_artifact_hash
        ),
        "policy_id": decision.policy_id,
        "policy_version": decision.policy_version,
        "policy_hash": decision.policy_hash,
        "schema_version": decision.schema_version,
    }


def execution_validation_decision_hash(
    decision: ExecutionValidationDecision,
) -> str:
    if not isinstance(decision, ExecutionValidationDecision):
        raise TypeError(
            "decision must be ExecutionValidationDecision"
        )

    payload = canonical_json(_decision_payload(decision))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def evaluate_execution_validation_policy(
    provenance: ExecutionValidationArtifact,
    policy: ExecutionValidationPolicy,
    *,
    execution_run_id: str,
) -> ExecutionValidationDecision:
    if not isinstance(provenance, ExecutionValidationArtifact):
        raise TypeError(
            "provenance must be ExecutionValidationArtifact"
        )
    if not isinstance(policy, ExecutionValidationPolicy):
        raise TypeError(
            "policy must be ExecutionValidationPolicy"
        )

    execution_run_id = _nonempty(
        execution_run_id,
        "execution_run_id",
    )

    expected_provenance_hash = execution_validation_artifact_hash(
        provenance
    )

    if expected_provenance_hash != provenance.artifact_hash:
        status = ValidationStatus.FAIL
        reasons = (
            "PROVENANCE_ARTIFACT_HASH_MISMATCH",
        )
    elif provenance.status == BLOCKED:
        status = ValidationStatus.FAIL
        reasons = (
            "PROVENANCE_BLOCKED",
            *provenance.reasons,
        )
    elif provenance.status == READY_FOR_POLICY_EVALUATION:
        status = ValidationStatus.PENDING
        reasons = (
            "MEASURED_VENUE_EXECUTION_EVIDENCE_MISSING",
        )
    else:
        raise ValueError(
            "unsupported execution provenance status"
        )

    provisional = ExecutionValidationDecision(
        execution_run_id=execution_run_id,
        provenance_run_id=provenance.execution_run_id,
        target=provenance.target,
        validation_status=status,
        reasons=tuple(reasons),
        provenance_artifact_hash=provenance.artifact_hash,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        policy_hash=execution_validation_policy_hash(policy),
        artifact_hash="PENDING",
    )

    return replace(
        provisional,
        artifact_hash=execution_validation_decision_hash(
            provisional
        ),
    )
