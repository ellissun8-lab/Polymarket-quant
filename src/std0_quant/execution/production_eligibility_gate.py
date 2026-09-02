# Deterministic production-eligibility governance gate v1.
#
# This module decides whether existing immutable governance evidence is
# sufficient to authorize a later registry promotion. It does not perform
# registry transitions, submit orders, hold credentials, or enable LIVE.

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass, replace
from enum import Enum
import hashlib
import json
from typing import Any

from std0_quant.execution.execution_validation import (
    ExecutionValidationTarget,
)
from std0_quant.execution.execution_validation_policy import (
    ExecutionValidationDecision,
    ExecutionValidationPolicy,
    execution_validation_decision_hash,
    execution_validation_policy_hash,
)
from std0_quant.research.factors.contracts import (
    FactorStatus,
    ValidationStatus,
)
from std0_quant.research.factors.registry import (
    FactorRegistryRecord,
    FactorTransition,
    PromotionEvidence,
    registry_record_hash,
)


PRODUCTION_ELIGIBILITY_GATE_V1 = "production_eligibility_gate_v1"
PRODUCTION_ELIGIBILITY_DECISION_SCHEMA_V1 = (
    "production_eligibility_decision_v1"
)

ELIGIBLE = "ELIGIBLE"
BLOCKED = "BLOCKED"


def _nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    text = value.strip()
    if not text:
        raise ValueError(f"{name} must be non-empty")
    return text


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {
            field.name: _jsonable(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, dict):
        return {
            str(key): _jsonable(item)
            for key, item in sorted(
                value.items(),
                key=lambda pair: str(pair[0]),
            )
        }
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _jsonable(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def promotion_evidence_hash(
    evidence: PromotionEvidence,
) -> str:
    if not isinstance(evidence, PromotionEvidence):
        raise TypeError("evidence must be PromotionEvidence")

    payload = _canonical_json(evidence)
    return hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class ProductionEligibilityDecision:
    gate_run_id: str
    target: ExecutionValidationTarget
    status: str
    reasons: tuple[str, ...]
    registry_record_hash: str
    promotion_evidence_hash: str
    execution_decision_artifact_hash: str
    execution_run_id: str
    provenance_run_id: str
    policy_id: str
    policy_version: str
    policy_hash: str
    artifact_hash: str
    gate_version: str = PRODUCTION_ELIGIBILITY_GATE_V1
    schema_version: str = PRODUCTION_ELIGIBILITY_DECISION_SCHEMA_V1

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "gate_run_id",
            _nonempty(self.gate_run_id, "gate_run_id"),
        )

        if not isinstance(self.target, ExecutionValidationTarget):
            raise TypeError("target must be ExecutionValidationTarget")

        if self.status not in {ELIGIBLE, BLOCKED}:
            raise ValueError(
                "unsupported production eligibility status"
            )

        reasons = tuple(
            _nonempty(reason, "reason")
            for reason in self.reasons
        )
        object.__setattr__(self, "reasons", reasons)

        if self.status == ELIGIBLE:
            raise ValueError(
                "ELIGIBLE is unreachable in production eligibility gate v1"
            )
        if self.status == BLOCKED and not reasons:
            raise ValueError("BLOCKED requires reasons")

        for name in (
            "registry_record_hash",
            "promotion_evidence_hash",
            "execution_decision_artifact_hash",
            "execution_run_id",
            "provenance_run_id",
            "policy_id",
            "policy_version",
            "policy_hash",
            "artifact_hash",
        ):
            object.__setattr__(
                self,
                name,
                _nonempty(getattr(self, name), name),
            )

        if self.gate_version != PRODUCTION_ELIGIBILITY_GATE_V1:
            raise ValueError("unsupported gate_version")
        if (
            self.schema_version
            != PRODUCTION_ELIGIBILITY_DECISION_SCHEMA_V1
        ):
            raise ValueError("unsupported schema_version")


def _decision_payload(
    decision: ProductionEligibilityDecision,
) -> dict[str, Any]:
    return {
        "target": decision.target,
        "status": decision.status,
        "reasons": decision.reasons,
        "registry_record_hash": decision.registry_record_hash,
        "promotion_evidence_hash": (
            decision.promotion_evidence_hash
        ),
        "execution_decision_artifact_hash": (
            decision.execution_decision_artifact_hash
        ),
        "execution_run_id": decision.execution_run_id,
        "provenance_run_id": decision.provenance_run_id,
        "policy_id": decision.policy_id,
        "policy_version": decision.policy_version,
        "policy_hash": decision.policy_hash,
        "gate_version": decision.gate_version,
        "schema_version": decision.schema_version,
    }


def production_eligibility_decision_hash(
    decision: ProductionEligibilityDecision,
) -> str:
    if not isinstance(
        decision,
        ProductionEligibilityDecision,
    ):
        raise TypeError(
            "decision must be ProductionEligibilityDecision"
        )

    payload = _canonical_json(
        _decision_payload(decision)
    )
    return hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()


def _valid_transition_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validated_transition_is_sound(
    transition: FactorTransition,
) -> bool:
    if not isinstance(transition, FactorTransition):
        return False

    if (
        transition.status_before != FactorStatus.CANDIDATE
        or transition.status_after != FactorStatus.VALIDATED
    ):
        return False

    if (
        transition.research_validation_status
        != ValidationStatus.PASS
        or transition.temporal_integrity
        != ValidationStatus.PASS
    ):
        return False

    required_strings = (
        transition.research_artifact_hash,
        transition.research_run_id,
        transition.decided_at,
        transition.research_policy_id,
        transition.research_policy_version,
        transition.research_policy_hash,
        transition.validation_evidence_bundle_hash,
    )

    return all(
        _valid_transition_string(value)
        for value in required_strings
    )


def _promotion_matches_transition(
    evidence: PromotionEvidence,
    transition: FactorTransition,
) -> bool:
    return all(
        (
            evidence.research_validation_status
            == transition.research_validation_status,
            evidence.temporal_integrity
            == transition.temporal_integrity,
            evidence.research_artifact_hash
            == transition.research_artifact_hash,
            evidence.research_run_id
            == transition.research_run_id,
            evidence.research_policy_id
            == transition.research_policy_id,
            evidence.research_policy_version
            == transition.research_policy_version,
            evidence.research_policy_hash
            == transition.research_policy_hash,
            evidence.research_validation_reasons
            == transition.research_validation_reasons,
            evidence.validation_evidence_bundle_hash
            == transition.validation_evidence_bundle_hash,
        )
    )


def evaluate_production_eligibility(
    record: FactorRegistryRecord,
    evidence: PromotionEvidence,
    decision: ExecutionValidationDecision,
    policy: ExecutionValidationPolicy,
    *,
    gate_run_id: str,
) -> ProductionEligibilityDecision:
    if not isinstance(record, FactorRegistryRecord):
        raise TypeError("record must be FactorRegistryRecord")
    if not isinstance(evidence, PromotionEvidence):
        raise TypeError("evidence must be PromotionEvidence")
    if not isinstance(decision, ExecutionValidationDecision):
        raise TypeError(
            "decision must be ExecutionValidationDecision"
        )
    if not isinstance(policy, ExecutionValidationPolicy):
        raise TypeError(
            "policy must be ExecutionValidationPolicy"
        )

    gate_run_id = _nonempty(
        gate_run_id,
        "gate_run_id",
    )

    reasons: list[str] = []

    def add_reason(reason: str) -> None:
        if reason not in reasons:
            reasons.append(reason)

    if record.status != FactorStatus.VALIDATED:
        add_reason("REGISTRY_NOT_VALIDATED")

    if (
        record.factor_id != decision.target.factor_id
        or record.factor_version
        != decision.target.factor_version
        or record.definition_hash
        != decision.target.definition_hash
    ):
        add_reason(
            "REGISTRY_TARGET_IDENTITY_MISMATCH"
        )

    if (
        execution_validation_decision_hash(decision)
        != decision.artifact_hash
    ):
        add_reason(
            "EXECUTION_DECISION_ARTIFACT_HASH_MISMATCH"
        )

    expected_policy_hash = (
        execution_validation_policy_hash(policy)
    )
    if (
        decision.policy_id != policy.policy_id
        or decision.policy_version != policy.version
        or decision.policy_hash != expected_policy_hash
    ):
        add_reason(
            "EXECUTION_POLICY_PROVENANCE_MISMATCH"
        )

    transition: FactorTransition | None = None

    if record.transitions:
        candidate_transition = record.transitions[-1]
        if isinstance(
            candidate_transition,
            FactorTransition,
        ):
            transition = candidate_transition

    if (
        transition is None
        or not _validated_transition_is_sound(
            transition
        )
    ):
        add_reason(
            "VALIDATED_TRANSITION_PROVENANCE_INVALID"
        )
    else:
        if not _promotion_matches_transition(
            evidence,
            transition,
        ):
            add_reason(
                "PROMOTION_EVIDENCE_MISMATCH"
            )

    if (
        evidence.execution_validation_status
        != decision.validation_status
        or evidence.execution_artifact_hash
        != decision.artifact_hash
        or evidence.execution_run_id
        != decision.execution_run_id
    ):
        add_reason("PROMOTION_EVIDENCE_MISMATCH")

    if (
        decision.validation_status
        != ValidationStatus.PASS
    ):
        add_reason(
            "EXECUTION_VALIDATION_NOT_PASS"
        )

    status = BLOCKED if reasons else ELIGIBLE

    provisional = ProductionEligibilityDecision(
        gate_run_id=gate_run_id,
        target=decision.target,
        status=status,
        reasons=tuple(reasons),
        registry_record_hash=registry_record_hash(
            record
        ),
        promotion_evidence_hash=promotion_evidence_hash(
            evidence
        ),
        execution_decision_artifact_hash=(
            decision.artifact_hash
        ),
        execution_run_id=decision.execution_run_id,
        provenance_run_id=decision.provenance_run_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        policy_hash=expected_policy_hash,
        artifact_hash="PENDING",
    )

    return replace(
        provisional,
        artifact_hash=(
            production_eligibility_decision_hash(
                provisional
            )
        ),
    )
