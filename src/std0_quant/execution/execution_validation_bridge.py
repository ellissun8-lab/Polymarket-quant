"""Bridge execution-validation decisions into registry promotion evidence.

Governance only. This adapter validates execution decision provenance and
preserves the existing research-validation provenance from a VALIDATED factor
record. It does not perform registry transitions or promote factors.
"""

from __future__ import annotations

from typing import Any

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
    PromotionEvidence,
)


def _nonempty_string(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    text = value.strip()
    if not text:
        raise ValueError(f"{name} must be non-empty")
    return text


def promotion_evidence_from_execution_decision(
    record: FactorRegistryRecord,
    decision: ExecutionValidationDecision,
    policy: ExecutionValidationPolicy,
    *,
    decided_at: str,
) -> PromotionEvidence:
    """Bind one execution decision to an already VALIDATED factor record.

    Research provenance is inherited only from the registry transition that
    established the current VALIDATED state. No registry mutation occurs here.
    """

    if not isinstance(record, FactorRegistryRecord):
        raise TypeError("record must be FactorRegistryRecord")
    if not isinstance(decision, ExecutionValidationDecision):
        raise TypeError("decision must be ExecutionValidationDecision")
    if not isinstance(policy, ExecutionValidationPolicy):
        raise TypeError("policy must be ExecutionValidationPolicy")

    decided_at = _nonempty_string(decided_at, "decided_at")

    if record.status != FactorStatus.VALIDATED:
        raise ValueError("execution governance bridge requires VALIDATED factor")

    if (
        decision.target.factor_id != record.factor_id
        or decision.target.factor_version != record.factor_version
        or decision.target.definition_hash != record.definition_hash
    ):
        raise ValueError(
            "execution decision target does not match registry factor identity"
        )

    if execution_validation_decision_hash(decision) != decision.artifact_hash:
        raise ValueError("execution validation decision artifact hash mismatch")

    expected_policy_hash = execution_validation_policy_hash(policy)
    if (
        decision.policy_id != policy.policy_id
        or decision.policy_version != policy.version
        or decision.policy_hash != expected_policy_hash
    ):
        raise ValueError("execution validation policy provenance mismatch")

    if not record.transitions:
        raise ValueError("VALIDATED factor requires validation transition history")

    transition = record.transitions[-1]

    if (
        transition.status_after != FactorStatus.VALIDATED
        or transition.status_before != FactorStatus.CANDIDATE
    ):
        raise ValueError(
            "current VALIDATED state is not backed by a validation transition"
        )

    if transition.research_validation_status != ValidationStatus.PASS:
        raise ValueError("VALIDATED transition requires research PASS")
    if transition.temporal_integrity != ValidationStatus.PASS:
        raise ValueError("VALIDATED transition requires temporal integrity PASS")

    research_policy_values = (
        transition.research_policy_id,
        transition.research_policy_version,
        transition.research_policy_hash,
    )
    if not all(
        isinstance(value, str) and value.strip()
        for value in research_policy_values
    ):
        raise ValueError(
            "VALIDATED transition requires complete research policy provenance"
        )

    if (
        not isinstance(transition.validation_evidence_bundle_hash, str)
        or not transition.validation_evidence_bundle_hash.strip()
    ):
        raise ValueError(
            "VALIDATED transition requires validation evidence bundle hash"
        )

    return PromotionEvidence(
        research_validation_status=transition.research_validation_status,
        temporal_integrity=transition.temporal_integrity,
        research_artifact_hash=transition.research_artifact_hash,
        research_run_id=transition.research_run_id,
        execution_validation_status=decision.validation_status,
        execution_artifact_hash=decision.artifact_hash,
        execution_run_id=decision.execution_run_id,
        research_policy_id=transition.research_policy_id,
        research_policy_version=transition.research_policy_version,
        research_policy_hash=transition.research_policy_hash,
        research_validation_reasons=transition.research_validation_reasons,
        validation_evidence_bundle_hash=(
            transition.validation_evidence_bundle_hash
        ),
        decided_at=decided_at,
    )
