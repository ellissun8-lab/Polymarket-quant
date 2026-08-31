"""Bridge deterministic validation decisions into registry promotion evidence.

Research governance only. This adapter preserves validation provenance and does
not perform registry transitions itself.
"""

from __future__ import annotations

from .registry import PromotionEvidence
from .validator import ValidationDecision


def promotion_evidence_from_decision(
    decision: ValidationDecision,
    *,
    decided_at: str,
) -> PromotionEvidence:
    """Convert one immutable validation decision into promotion evidence."""

    return PromotionEvidence(
        research_validation_status=decision.research_validation_status,
        temporal_integrity=decision.temporal_integrity,
        research_artifact_hash=decision.research_artifact_hash,
        research_run_id=decision.research_run_id,
        research_policy_id=decision.policy_id,
        research_policy_version=decision.policy_version,
        research_policy_hash=decision.policy_hash,
        research_validation_reasons=decision.reasons,
        validation_evidence_bundle_hash=(
            decision.validation_evidence_bundle_hash
        ),
        decided_at=decided_at,
    )
