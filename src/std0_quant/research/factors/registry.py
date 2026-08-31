"""Deterministic Factor Registry promotion state machine v1.

Research governance only. Registry transitions are immutable and fail closed.
Production eligibility requires separate execution-validation evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from .contracts import FactorStatus, ValidationStatus


@dataclass(frozen=True)
class PromotionEvidence:
    research_validation_status: ValidationStatus | str
    temporal_integrity: ValidationStatus | str
    research_artifact_hash: str
    research_run_id: str
    execution_validation_status: ValidationStatus | str | None = None
    execution_artifact_hash: str | None = None
    execution_run_id: str | None = None
    research_policy_id: str | None = None
    research_policy_version: str | None = None
    research_policy_hash: str | None = None
    research_validation_reasons: tuple[str, ...] = ()
    validation_evidence_bundle_hash: str | None = None
    decided_at: str = ""

    def __post_init__(self) -> None:
        try:
            research = ValidationStatus(self.research_validation_status)
        except ValueError as exc:
            raise ValueError("unsupported research_validation_status") from exc
        try:
            temporal = ValidationStatus(self.temporal_integrity)
        except ValueError as exc:
            raise ValueError("unsupported temporal_integrity") from exc

        execution = self.execution_validation_status
        if execution is not None:
            try:
                execution = ValidationStatus(execution)
            except ValueError as exc:
                raise ValueError("unsupported execution_validation_status") from exc

        object.__setattr__(self, "research_validation_status", research)
        object.__setattr__(self, "temporal_integrity", temporal)
        object.__setattr__(self, "execution_validation_status", execution)
        object.__setattr__(self, "research_artifact_hash", _nonempty(self.research_artifact_hash, "research_artifact_hash"))
        object.__setattr__(self, "research_run_id", _nonempty(self.research_run_id, "research_run_id"))
        object.__setattr__(self, "decided_at", _nonempty(self.decided_at, "decided_at"))

        if self.execution_artifact_hash is not None:
            object.__setattr__(self, "execution_artifact_hash", _nonempty(self.execution_artifact_hash, "execution_artifact_hash"))
        if self.execution_run_id is not None:
            object.__setattr__(self, "execution_run_id", _nonempty(self.execution_run_id, "execution_run_id"))

        policy_values = (
            self.research_policy_id,
            self.research_policy_version,
            self.research_policy_hash,
        )
        if any(value is not None for value in policy_values):
            if not all(value is not None for value in policy_values):
                raise ValueError("research policy provenance must be complete")
            object.__setattr__(self, "research_policy_id", _nonempty(self.research_policy_id, "research_policy_id"))
            object.__setattr__(self, "research_policy_version", _nonempty(self.research_policy_version, "research_policy_version"))
            object.__setattr__(self, "research_policy_hash", _nonempty(self.research_policy_hash, "research_policy_hash"))

        reasons = tuple(_nonempty(value, "research_validation_reason") for value in self.research_validation_reasons)
        if reasons and self.research_policy_id is None:
            raise ValueError("research validation reasons require policy provenance")
        object.__setattr__(self, "research_validation_reasons", reasons)

        if self.validation_evidence_bundle_hash is not None:
            object.__setattr__(
                self,
                "validation_evidence_bundle_hash",
                _nonempty(
                    self.validation_evidence_bundle_hash,
                    "validation_evidence_bundle_hash",
                ),
            )


@dataclass(frozen=True)
class FactorTransition:
    status_before: FactorStatus
    status_after: FactorStatus
    research_validation_status: ValidationStatus
    temporal_integrity: ValidationStatus
    research_artifact_hash: str
    research_run_id: str
    execution_validation_status: ValidationStatus | None
    execution_artifact_hash: str | None
    execution_run_id: str | None
    decided_at: str
    research_policy_id: str | None = None
    research_policy_version: str | None = None
    research_policy_hash: str | None = None
    research_validation_reasons: tuple[str, ...] = ()
    validation_evidence_bundle_hash: str | None = None


@dataclass(frozen=True)
class FactorRegistryRecord:
    factor_id: str
    factor_version: str
    definition_hash: str
    status: FactorStatus | str
    created_by: str
    created_at: str
    transitions: tuple[FactorTransition, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "factor_id", _nonempty(self.factor_id, "factor_id"))
        object.__setattr__(self, "factor_version", _nonempty(self.factor_version, "factor_version"))
        object.__setattr__(self, "definition_hash", _nonempty(self.definition_hash, "definition_hash"))
        object.__setattr__(self, "created_by", _nonempty(self.created_by, "created_by"))
        object.__setattr__(self, "created_at", _nonempty(self.created_at, "created_at"))
        try:
            status = FactorStatus(self.status)
        except ValueError as exc:
            raise ValueError("unsupported factor status") from exc
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "transitions", tuple(self.transitions))


def promote_factor(
    record: FactorRegistryRecord,
    target_status: FactorStatus | str,
    evidence: PromotionEvidence,
) -> FactorRegistryRecord:
    """Return a new immutable registry record after an allowed transition."""

    try:
        target = FactorStatus(target_status)
    except ValueError as exc:
        raise ValueError("unsupported target factor status") from exc

    before = record.status
    if target == before:
        raise ValueError("factor status transition must change status")

    if target == FactorStatus.DEPRECATED:
        if before == FactorStatus.DEPRECATED:
            raise ValueError("DEPRECATED is terminal")
    elif before == FactorStatus.CANDIDATE and target == FactorStatus.VALIDATED:
        if evidence.research_validation_status != ValidationStatus.PASS:
            raise ValueError("CANDIDATE -> VALIDATED requires research PASS")
        if evidence.temporal_integrity != ValidationStatus.PASS:
            raise ValueError("CANDIDATE -> VALIDATED requires temporal integrity PASS")
    elif before == FactorStatus.CANDIDATE and target == FactorStatus.REJECTED:
        if evidence.research_validation_status != ValidationStatus.FAIL:
            raise ValueError("CANDIDATE -> REJECTED requires research FAIL")
    elif before == FactorStatus.VALIDATED and target == FactorStatus.PRODUCTION_ELIGIBLE:
        if evidence.execution_validation_status != ValidationStatus.PASS:
            raise ValueError("VALIDATED -> PRODUCTION_ELIGIBLE requires execution PASS")
        if not evidence.execution_artifact_hash or not evidence.execution_run_id:
            raise ValueError("execution PASS requires execution artifact hash and run id")
    else:
        raise ValueError(f"forbidden factor transition: {before.value} -> {target.value}")

    transition = FactorTransition(
        status_before=before,
        status_after=target,
        research_validation_status=evidence.research_validation_status,
        temporal_integrity=evidence.temporal_integrity,
        research_artifact_hash=evidence.research_artifact_hash,
        research_run_id=evidence.research_run_id,
        execution_validation_status=evidence.execution_validation_status,
        execution_artifact_hash=evidence.execution_artifact_hash,
        execution_run_id=evidence.execution_run_id,
        decided_at=evidence.decided_at,
        research_policy_id=evidence.research_policy_id,
        research_policy_version=evidence.research_policy_version,
        research_policy_hash=evidence.research_policy_hash,
        research_validation_reasons=evidence.research_validation_reasons,
        validation_evidence_bundle_hash=evidence.validation_evidence_bundle_hash,
    )
    return replace(
        record,
        status=target,
        transitions=record.transitions + (transition,),
    )


def _nonempty(value: Any, name: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{name} must be non-empty")
    return text

# --- Factor Registry persistence v1 ---

import hashlib
import json
from pathlib import Path

from std0_quant.audit.prospective import atomic_json
from std0_quant.storage import canonical_json

from .contracts import FACTOR_REGISTRY_SCHEMA_V1


def _transition_to_dict(row: FactorTransition) -> dict[str, Any]:
    payload = {
        "status_before": row.status_before.value,
        "status_after": row.status_after.value,
        "research_validation_status": row.research_validation_status.value,
        "temporal_integrity": row.temporal_integrity.value,
        "research_artifact_hash": row.research_artifact_hash,
        "research_run_id": row.research_run_id,
        "execution_validation_status": (
            row.execution_validation_status.value
            if row.execution_validation_status is not None
            else None
        ),
        "execution_artifact_hash": row.execution_artifact_hash,
        "execution_run_id": row.execution_run_id,
        "decided_at": row.decided_at,
    }
    if row.research_policy_id is not None:
        payload["research_policy_id"] = row.research_policy_id
        payload["research_policy_version"] = row.research_policy_version
        payload["research_policy_hash"] = row.research_policy_hash
        payload["research_validation_reasons"] = list(row.research_validation_reasons)
    if row.validation_evidence_bundle_hash is not None:
        payload["validation_evidence_bundle_hash"] = (
            row.validation_evidence_bundle_hash
        )
    return payload


def _record_to_dict(record: FactorRegistryRecord) -> dict[str, Any]:
    return {
        "factor_id": record.factor_id,
        "factor_version": record.factor_version,
        "definition_hash": record.definition_hash,
        "status": record.status.value,
        "created_by": record.created_by,
        "created_at": record.created_at,
        "transitions": [_transition_to_dict(row) for row in record.transitions],
    }


def registry_record_hash(record: FactorRegistryRecord) -> str:
    payload = canonical_json(_record_to_dict(record))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_registry_record(
    path: Path | str,
    record: FactorRegistryRecord,
) -> Path:
    target = Path(path)
    payload = {
        "schema_version": FACTOR_REGISTRY_SCHEMA_V1,
        "record_hash": registry_record_hash(record),
        "record": _record_to_dict(record),
    }
    return atomic_json(target, payload)


def _transition_from_dict(row: dict[str, Any]) -> FactorTransition:
    execution = row.get("execution_validation_status")
    return FactorTransition(
        status_before=FactorStatus(row["status_before"]),
        status_after=FactorStatus(row["status_after"]),
        research_validation_status=ValidationStatus(
            row["research_validation_status"]
        ),
        temporal_integrity=ValidationStatus(row["temporal_integrity"]),
        research_artifact_hash=_nonempty(
            row["research_artifact_hash"],
            "research_artifact_hash",
        ),
        research_run_id=_nonempty(
            row["research_run_id"],
            "research_run_id",
        ),
        execution_validation_status=(
            ValidationStatus(execution) if execution is not None else None
        ),
        execution_artifact_hash=row.get("execution_artifact_hash"),
        execution_run_id=row.get("execution_run_id"),
        decided_at=_nonempty(row["decided_at"], "decided_at"),
        research_policy_id=row.get("research_policy_id"),
        research_policy_version=row.get("research_policy_version"),
        research_policy_hash=row.get("research_policy_hash"),
        research_validation_reasons=tuple(row.get("research_validation_reasons", ())),
        validation_evidence_bundle_hash=row.get(
            "validation_evidence_bundle_hash"
        ),
    )


def _record_from_dict(row: dict[str, Any]) -> FactorRegistryRecord:
    return FactorRegistryRecord(
        factor_id=row["factor_id"],
        factor_version=row["factor_version"],
        definition_hash=row["definition_hash"],
        status=FactorStatus(row["status"]),
        created_by=row["created_by"],
        created_at=row["created_at"],
        transitions=tuple(
            _transition_from_dict(item)
            for item in row.get("transitions", [])
        ),
    )


def load_registry_record(path: Path | str) -> FactorRegistryRecord:
    target = Path(path)
    payload = json.loads(target.read_text(encoding="utf-8"))

    if payload.get("schema_version") != FACTOR_REGISTRY_SCHEMA_V1:
        raise ValueError("unsupported factor registry schema_version")

    row = payload.get("record")
    if not isinstance(row, dict):
        raise ValueError("factor registry record missing")

    record = _record_from_dict(row)
    expected = payload.get("record_hash")
    actual = registry_record_hash(record)
    if not isinstance(expected, str) or expected != actual:
        raise ValueError("factor registry hash mismatch")

    return record
