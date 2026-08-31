"""Deterministic Alpha Factory evidence orchestration v1.

Research/governance only. This module assembles already-produced research and
SHADOW evidence. It does not promote registry state, manufacture execution
validation, use credentials, or place orders.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Iterable

from std0_quant.execution.batch_shadow_runner import BatchShadowArtifact
from std0_quant.research.factors.contracts import FactorStatus, ValidationStatus
from std0_quant.research.factors.registry import FactorRegistryRecord
from std0_quant.research.factors.validator import ValidationDecision


ALPHA_CANDIDATE_SPEC_SCHEMA_V1 = "alpha_candidate_spec_v1"
ALPHA_FACTORY_ARTIFACT_SCHEMA_V1 = "alpha_factory_artifact_v1"
ALPHA_FACTORY_VERSION_V1 = "alpha_factory_v1"
READY_FOR_GOVERNANCE_REVIEW = "READY_FOR_GOVERNANCE_REVIEW"
BLOCKED = "BLOCKED"


def _nonempty(value: Any, name: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{name} must be non-empty")
    return text


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


@dataclass(frozen=True)
class AlphaFactorBinding:
    factor_id: str
    factor_version: str
    definition_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "factor_id", _nonempty(self.factor_id, "factor_id"))
        object.__setattr__(
            self,
            "factor_version",
            _nonempty(self.factor_version, "factor_version"),
        )
        object.__setattr__(
            self,
            "definition_hash",
            _nonempty(self.definition_hash, "definition_hash"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "factor_id": self.factor_id,
            "factor_version": self.factor_version,
            "definition_hash": self.definition_hash,
        }


@dataclass(frozen=True)
class AlphaCandidateSpec:
    alpha_id: str
    alpha_version: str
    factor_bindings: tuple[AlphaFactorBinding, ...]
    risk_policy_version: str
    created_by: str
    created_at: str
    schema_version: str = ALPHA_CANDIDATE_SPEC_SCHEMA_V1

    def __post_init__(self) -> None:
        for name in (
            "alpha_id",
            "alpha_version",
            "risk_policy_version",
            "created_by",
            "created_at",
        ):
            object.__setattr__(self, name, _nonempty(getattr(self, name), name))

        bindings = tuple(self.factor_bindings)
        if not bindings:
            raise ValueError("factor_bindings must be non-empty")
        if not all(isinstance(row, AlphaFactorBinding) for row in bindings):
            raise TypeError("factor_bindings must contain AlphaFactorBinding")

        identities = [(row.factor_id, row.factor_version) for row in bindings]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate factor binding")

        object.__setattr__(self, "factor_bindings", bindings)

        if self.schema_version != ALPHA_CANDIDATE_SPEC_SCHEMA_V1:
            raise ValueError("unsupported AlphaCandidateSpec schema_version")

    def to_dict(self) -> dict[str, Any]:
        return {
            "alpha_id": self.alpha_id,
            "alpha_version": self.alpha_version,
            "factor_bindings": [row.to_dict() for row in self.factor_bindings],
            "risk_policy_version": self.risk_policy_version,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "schema_version": self.schema_version,
        }


@dataclass(frozen=True)
class AlphaResearchEvidence:
    factor_id: str
    factor_version: str
    definition_hash: str
    registry_status: str
    research_validation_status: str
    temporal_integrity: str
    research_artifact_hash: str
    research_run_id: str
    policy_id: str
    policy_version: str
    policy_hash: str
    validation_evidence_bundle_hash: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "factor_id": self.factor_id,
            "factor_version": self.factor_version,
            "definition_hash": self.definition_hash,
            "registry_status": self.registry_status,
            "research_validation_status": self.research_validation_status,
            "temporal_integrity": self.temporal_integrity,
            "research_artifact_hash": self.research_artifact_hash,
            "research_run_id": self.research_run_id,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "policy_hash": self.policy_hash,
            "validation_evidence_bundle_hash": self.validation_evidence_bundle_hash,
        }


@dataclass(frozen=True)
class AlphaFactoryArtifact:
    alpha_run_id: str
    alpha_id: str
    alpha_version: str
    risk_policy_version: str
    status: str
    reasons: tuple[str, ...]
    factor_evidence: tuple[AlphaResearchEvidence, ...]
    shadow_artifact_hash: str
    shadow_run_id: str
    n_shadow_total: int
    n_shadow_pass: int
    n_shadow_fail: int
    artifact_hash: str
    factory_version: str = ALPHA_FACTORY_VERSION_V1
    schema_version: str = ALPHA_FACTORY_ARTIFACT_SCHEMA_V1

    def __post_init__(self) -> None:
        for name in (
            "alpha_run_id",
            "alpha_id",
            "alpha_version",
            "risk_policy_version",
            "shadow_artifact_hash",
            "shadow_run_id",
            "artifact_hash",
        ):
            object.__setattr__(self, name, _nonempty(getattr(self, name), name))

        if self.status not in {READY_FOR_GOVERNANCE_REVIEW, BLOCKED}:
            raise ValueError("unsupported Alpha Factory status")

        object.__setattr__(self, "reasons", tuple(self.reasons))
        object.__setattr__(self, "factor_evidence", tuple(self.factor_evidence))

        if self.status == READY_FOR_GOVERNANCE_REVIEW and self.reasons:
            raise ValueError("READY artifact cannot contain blocking reasons")
        if self.status == BLOCKED and not self.reasons:
            raise ValueError("BLOCKED artifact requires reasons")
        if self.factory_version != ALPHA_FACTORY_VERSION_V1:
            raise ValueError("unsupported factory_version")
        if self.schema_version != ALPHA_FACTORY_ARTIFACT_SCHEMA_V1:
            raise ValueError("unsupported schema_version")

    def to_dict(self) -> dict[str, Any]:
        return {
            "alpha_run_id": self.alpha_run_id,
            "alpha_id": self.alpha_id,
            "alpha_version": self.alpha_version,
            "risk_policy_version": self.risk_policy_version,
            "status": self.status,
            "reasons": list(self.reasons),
            "factor_evidence": [row.to_dict() for row in self.factor_evidence],
            "shadow_artifact_hash": self.shadow_artifact_hash,
            "shadow_run_id": self.shadow_run_id,
            "n_shadow_total": self.n_shadow_total,
            "n_shadow_pass": self.n_shadow_pass,
            "n_shadow_fail": self.n_shadow_fail,
            "artifact_hash": self.artifact_hash,
            "factory_version": self.factory_version,
            "schema_version": self.schema_version,
        }


def _artifact_payload(artifact: AlphaFactoryArtifact) -> dict[str, Any]:
    row = artifact.to_dict()
    row.pop("alpha_run_id")
    row.pop("artifact_hash")
    return row


def alpha_factory_artifact_hash(artifact: AlphaFactoryArtifact) -> str:
    return hashlib.sha256(
        _canonical_json(_artifact_payload(artifact)).encode("utf-8")
    ).hexdigest()


def _unique_by_identity(rows: Iterable[Any], name: str) -> dict[tuple[str, str], Any]:
    result: dict[tuple[str, str], Any] = {}
    for row in rows:
        key = (row.factor_id, row.factor_version)
        if key in result:
            raise ValueError(f"duplicate {name} factor identity")
        result[key] = row
    return result


def build_alpha_factory_artifact(
    spec: AlphaCandidateSpec,
    registry_records: Iterable[FactorRegistryRecord],
    decisions: Iterable[ValidationDecision],
    shadow: BatchShadowArtifact,
    *,
    alpha_run_id: str,
) -> AlphaFactoryArtifact:
    if not isinstance(spec, AlphaCandidateSpec):
        raise TypeError("spec must be AlphaCandidateSpec")
    if not isinstance(shadow, BatchShadowArtifact):
        raise TypeError("shadow must be BatchShadowArtifact")

    alpha_run_id = _nonempty(alpha_run_id, "alpha_run_id")

    records = tuple(registry_records)
    decisions = tuple(decisions)

    if not all(isinstance(row, FactorRegistryRecord) for row in records):
        raise TypeError("registry_records must contain FactorRegistryRecord")
    if not all(isinstance(row, ValidationDecision) for row in decisions):
        raise TypeError("decisions must contain ValidationDecision")

    record_map = _unique_by_identity(records, "registry")
    decision_map = _unique_by_identity(decisions, "validation")

    reasons: list[str] = []
    evidence: list[AlphaResearchEvidence] = []

    def add_reason(reason: str) -> None:
        if reason not in reasons:
            reasons.append(reason)

    for binding in spec.factor_bindings:
        key = (binding.factor_id, binding.factor_version)
        record = record_map.get(key)
        decision = decision_map.get(key)

        if record is None:
            add_reason("REGISTRY_RECORD_NOT_FOUND")
        else:
            if record.definition_hash != binding.definition_hash:
                add_reason("FACTOR_DEFINITION_HASH_MISMATCH")
            if record.status not in {
                FactorStatus.VALIDATED,
                FactorStatus.PRODUCTION_ELIGIBLE,
            }:
                add_reason("FACTOR_STATUS_NOT_VALIDATED")

        if decision is None:
            add_reason("VALIDATION_DECISION_NOT_FOUND")
        else:
            if decision.research_validation_status != ValidationStatus.PASS:
                add_reason("RESEARCH_VALIDATION_NOT_PASS")
            if decision.temporal_integrity != ValidationStatus.PASS:
                add_reason("TEMPORAL_INTEGRITY_NOT_PASS")
            if decision.validation_evidence_bundle_hash is None:
                add_reason("VALIDATION_EVIDENCE_BUNDLE_HASH_MISSING")

        if record is not None and decision is not None:
            evidence.append(
                AlphaResearchEvidence(
                    factor_id=binding.factor_id,
                    factor_version=binding.factor_version,
                    definition_hash=binding.definition_hash,
                    registry_status=record.status.value,
                    research_validation_status=decision.research_validation_status.value,
                    temporal_integrity=decision.temporal_integrity.value,
                    research_artifact_hash=decision.research_artifact_hash,
                    research_run_id=decision.research_run_id,
                    policy_id=decision.policy_id,
                    policy_version=decision.policy_version,
                    policy_hash=decision.policy_hash,
                    validation_evidence_bundle_hash=decision.validation_evidence_bundle_hash,
                )
            )

    if shadow.n_total <= 0:
        add_reason("SHADOW_EMPTY")
    if shadow.n_fail != 0:
        add_reason("SHADOW_HAS_FAILURES")

    for item in shadow.items:
        intent = item.request.intent
        if intent.strategy_id != spec.alpha_id:
            add_reason("SHADOW_STRATEGY_ID_MISMATCH")
        if intent.strategy_version != spec.alpha_version:
            add_reason("SHADOW_STRATEGY_VERSION_MISMATCH")
        if intent.risk_policy_version != spec.risk_policy_version:
            add_reason("SHADOW_RISK_POLICY_VERSION_MISMATCH")

    status = BLOCKED if reasons else READY_FOR_GOVERNANCE_REVIEW

    provisional = AlphaFactoryArtifact(
        alpha_run_id=alpha_run_id,
        alpha_id=spec.alpha_id,
        alpha_version=spec.alpha_version,
        risk_policy_version=spec.risk_policy_version,
        status=status,
        reasons=tuple(reasons),
        factor_evidence=tuple(evidence),
        shadow_artifact_hash=shadow.artifact_hash,
        shadow_run_id=shadow.run_id,
        n_shadow_total=shadow.n_total,
        n_shadow_pass=shadow.n_pass,
        n_shadow_fail=shadow.n_fail,
        artifact_hash="PENDING",
    )

    return AlphaFactoryArtifact(
        alpha_run_id=provisional.alpha_run_id,
        alpha_id=provisional.alpha_id,
        alpha_version=provisional.alpha_version,
        risk_policy_version=provisional.risk_policy_version,
        status=provisional.status,
        reasons=provisional.reasons,
        factor_evidence=provisional.factor_evidence,
        shadow_artifact_hash=provisional.shadow_artifact_hash,
        shadow_run_id=provisional.shadow_run_id,
        n_shadow_total=provisional.n_shadow_total,
        n_shadow_pass=provisional.n_shadow_pass,
        n_shadow_fail=provisional.n_shadow_fail,
        artifact_hash=alpha_factory_artifact_hash(provisional),
    )
