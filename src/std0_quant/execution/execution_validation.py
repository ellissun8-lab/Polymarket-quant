"""Deterministic execution-validation provenance evidence v1.

This layer validates factor-to-alpha-to-strategy-to-SHADOW provenance.
READY_FOR_POLICY_EVALUATION is not execution validation PASS and does not
imply production eligibility or registry promotion.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from typing import Any

from std0_quant.execution.batch_shadow_runner import batch_shadow_artifact_hash
from std0_quant.execution.clodds_mapping import CLODDS_MAPPING_VERSION_V1
from std0_quant.execution.clodds_shadow_protocol import (
    AUDITED_CLODDS_COMMIT_V1,
    CLODDS_SHADOW_PROTOCOL_V1,
)
from std0_quant.execution.strategy_candidate import (
    StrategyOrderCandidate,
    strategy_candidate_hash,
)
from std0_quant.execution.strategy_shadow_run import (
    StrategyShadowRunArtifact,
    strategy_shadow_run_artifact_hash,
)
from std0_quant.research.alpha_factory import (
    AlphaCandidateSpec,
    AlphaFactoryArtifact,
    READY_FOR_GOVERNANCE_REVIEW,
    alpha_factory_artifact_hash,
)


EXECUTION_VALIDATION_ARTIFACT_SCHEMA_V1 = "execution_validation_artifact_v1"
EXECUTION_VALIDATOR_VERSION_V1 = "execution_validation_provenance_v1"

READY_FOR_POLICY_EVALUATION = "READY_FOR_POLICY_EVALUATION"
BLOCKED = "BLOCKED"


def _nonempty(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


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


@dataclass(frozen=True)
class ExecutionValidationTarget:
    factor_id: str
    factor_version: str
    definition_hash: str
    alpha_id: str
    alpha_version: str
    risk_policy_version: str

    def __post_init__(self) -> None:
        for name in (
            "factor_id",
            "factor_version",
            "definition_hash",
            "alpha_id",
            "alpha_version",
            "risk_policy_version",
        ):
            object.__setattr__(
                self,
                name,
                _nonempty(getattr(self, name), name),
            )


@dataclass(frozen=True)
class ExecutionValidationArtifact:
    execution_run_id: str
    target: ExecutionValidationTarget
    status: str
    reasons: tuple[str, ...]
    alpha_factory_artifact_hash: str
    strategy_candidate_hash: str
    strategy_shadow_artifact_hash: str
    strategy_shadow_run_id: str
    protocol_version: str
    clodds_commit: str
    mapping_version: str
    artifact_hash: str
    validator_version: str = EXECUTION_VALIDATOR_VERSION_V1
    schema_version: str = EXECUTION_VALIDATION_ARTIFACT_SCHEMA_V1

    def __post_init__(self) -> None:
        _nonempty(self.execution_run_id, "execution_run_id")

        if not isinstance(self.target, ExecutionValidationTarget):
            raise TypeError("target must be ExecutionValidationTarget")

        for name in (
            "alpha_factory_artifact_hash",
            "strategy_candidate_hash",
            "strategy_shadow_artifact_hash",
            "strategy_shadow_run_id",
            "protocol_version",
            "clodds_commit",
            "mapping_version",
            "artifact_hash",
        ):
            _nonempty(getattr(self, name), name)

        if self.status not in {
            READY_FOR_POLICY_EVALUATION,
            BLOCKED,
        }:
            raise ValueError("unsupported execution validation status")

        reasons = tuple(
            _nonempty(reason, "reason")
            for reason in self.reasons
        )
        object.__setattr__(self, "reasons", reasons)

        if self.status == READY_FOR_POLICY_EVALUATION and reasons:
            raise ValueError(
                "READY_FOR_POLICY_EVALUATION cannot contain reasons"
            )
        if self.status == BLOCKED and not reasons:
            raise ValueError("BLOCKED requires reasons")

        if self.protocol_version != CLODDS_SHADOW_PROTOCOL_V1:
            raise ValueError("unsupported shadow protocol")
        if self.clodds_commit != AUDITED_CLODDS_COMMIT_V1:
            raise ValueError("unsupported audited Clodds commit")
        if self.mapping_version != CLODDS_MAPPING_VERSION_V1:
            raise ValueError("unsupported mapping version")
        if self.validator_version != EXECUTION_VALIDATOR_VERSION_V1:
            raise ValueError("unsupported validator_version")
        if self.schema_version != EXECUTION_VALIDATION_ARTIFACT_SCHEMA_V1:
            raise ValueError("unsupported schema_version")


def _artifact_payload(
    artifact: ExecutionValidationArtifact,
) -> dict[str, Any]:
    return {
        "target": artifact.target,
        "status": artifact.status,
        "reasons": artifact.reasons,
        "alpha_factory_artifact_hash": (
            artifact.alpha_factory_artifact_hash
        ),
        "strategy_candidate_hash": artifact.strategy_candidate_hash,
        "strategy_shadow_artifact_hash": (
            artifact.strategy_shadow_artifact_hash
        ),
        "protocol_version": artifact.protocol_version,
        "clodds_commit": artifact.clodds_commit,
        "mapping_version": artifact.mapping_version,
        "validator_version": artifact.validator_version,
        "schema_version": artifact.schema_version,
    }


def execution_validation_artifact_hash(
    artifact: ExecutionValidationArtifact,
) -> str:
    return hashlib.sha256(
        _canonical_json(_artifact_payload(artifact)).encode("utf-8")
    ).hexdigest()


def build_execution_validation_artifact(
    target: ExecutionValidationTarget,
    spec: AlphaCandidateSpec,
    alpha: AlphaFactoryArtifact,
    candidate: StrategyOrderCandidate,
    shadow: StrategyShadowRunArtifact,
    *,
    execution_run_id: str,
) -> ExecutionValidationArtifact:
    if not isinstance(target, ExecutionValidationTarget):
        raise TypeError("target must be ExecutionValidationTarget")
    if not isinstance(spec, AlphaCandidateSpec):
        raise TypeError("spec must be AlphaCandidateSpec")
    if not isinstance(alpha, AlphaFactoryArtifact):
        raise TypeError("alpha must be AlphaFactoryArtifact")
    if not isinstance(candidate, StrategyOrderCandidate):
        raise TypeError("candidate must be StrategyOrderCandidate")
    if not isinstance(shadow, StrategyShadowRunArtifact):
        raise TypeError("shadow must be StrategyShadowRunArtifact")

    execution_run_id = _nonempty(
        execution_run_id,
        "execution_run_id",
    )

    reasons: list[str] = []

    def add_reason(reason: str) -> None:
        if reason not in reasons:
            reasons.append(reason)

    bindings = [
        row
        for row in spec.factor_bindings
        if (
            row.factor_id == target.factor_id
            and row.factor_version == target.factor_version
        )
    ]

    if not bindings:
        add_reason("TARGET_FACTOR_NOT_BOUND")
    else:
        binding = bindings[0]
        if binding.definition_hash != target.definition_hash:
            add_reason(
                "TARGET_FACTOR_DEFINITION_HASH_MISMATCH"
            )

    if spec.alpha_id != target.alpha_id:
        add_reason("ALPHA_ID_MISMATCH")
    if spec.alpha_version != target.alpha_version:
        add_reason("ALPHA_VERSION_MISMATCH")
    if spec.risk_policy_version != target.risk_policy_version:
        add_reason("RISK_POLICY_VERSION_MISMATCH")

    if alpha.alpha_id != spec.alpha_id:
        add_reason("ALPHA_FACTORY_ALPHA_ID_MISMATCH")
    if alpha.alpha_version != spec.alpha_version:
        add_reason("ALPHA_FACTORY_ALPHA_VERSION_MISMATCH")
    if alpha.risk_policy_version != spec.risk_policy_version:
        add_reason("ALPHA_FACTORY_RISK_POLICY_VERSION_MISMATCH")

    if alpha.alpha_id != target.alpha_id:
        add_reason("ALPHA_FACTORY_TARGET_ALPHA_ID_MISMATCH")
    if alpha.alpha_version != target.alpha_version:
        add_reason("ALPHA_FACTORY_TARGET_ALPHA_VERSION_MISMATCH")
    if alpha.risk_policy_version != target.risk_policy_version:
        add_reason(
            "ALPHA_FACTORY_TARGET_RISK_POLICY_VERSION_MISMATCH"
        )

    if alpha.status != READY_FOR_GOVERNANCE_REVIEW:
        add_reason("ALPHA_FACTORY_NOT_READY")

    if alpha_factory_artifact_hash(alpha) != alpha.artifact_hash:
        add_reason("ALPHA_FACTORY_ARTIFACT_HASH_MISMATCH")

    factor_evidence = [
        row
        for row in alpha.factor_evidence
        if (
            row.factor_id == target.factor_id
            and row.factor_version == target.factor_version
        )
    ]

    if not factor_evidence:
        add_reason("TARGET_FACTOR_EVIDENCE_NOT_FOUND")
    else:
        if len(factor_evidence) != 1:
            add_reason("TARGET_FACTOR_EVIDENCE_DUPLICATE")
        evidence = factor_evidence[0]
        if evidence.definition_hash != target.definition_hash:
            add_reason(
                "TARGET_FACTOR_EVIDENCE_DEFINITION_HASH_MISMATCH"
            )
        if evidence.registry_status not in {
            "VALIDATED",
            "PRODUCTION_ELIGIBLE",
        }:
            add_reason("TARGET_FACTOR_REGISTRY_NOT_VALIDATED")
        if evidence.research_validation_status != "PASS":
            add_reason("TARGET_FACTOR_RESEARCH_NOT_PASS")
        if evidence.temporal_integrity != "PASS":
            add_reason("TARGET_FACTOR_TEMPORAL_NOT_PASS")
        if evidence.validation_evidence_bundle_hash is None:
            add_reason("TARGET_FACTOR_EVIDENCE_BUNDLE_MISSING")

    if alpha.n_shadow_total <= 0:
        add_reason("ALPHA_FACTORY_SHADOW_EMPTY")
    if (
        alpha.n_shadow_fail != 0
        or alpha.n_shadow_pass != alpha.n_shadow_total
    ):
        add_reason("ALPHA_FACTORY_SHADOW_HAS_FAILURES")

    if candidate.alpha_id != target.alpha_id:
        add_reason("CANDIDATE_ALPHA_ID_MISMATCH")
    if candidate.alpha_version != target.alpha_version:
        add_reason("CANDIDATE_ALPHA_VERSION_MISMATCH")
    if candidate.risk_policy_version != target.risk_policy_version:
        add_reason("CANDIDATE_RISK_POLICY_VERSION_MISMATCH")

    candidate_hash = strategy_candidate_hash(candidate)

    if shadow.candidate_hash != candidate_hash:
        add_reason("STRATEGY_SHADOW_CANDIDATE_HASH_MISMATCH")

    if strategy_shadow_run_artifact_hash(shadow) != shadow.artifact_hash:
        add_reason("STRATEGY_SHADOW_ARTIFACT_HASH_MISMATCH")

    if shadow.status != "SHADOW_PASS":
        add_reason("STRATEGY_SHADOW_NOT_PASS")

    if shadow.shadow_artifact is None:
        add_reason("BATCH_SHADOW_ARTIFACT_MISSING")
    elif (
        batch_shadow_artifact_hash(shadow.shadow_artifact)
        != shadow.shadow_artifact.artifact_hash
    ):
        add_reason("BATCH_SHADOW_ARTIFACT_HASH_MISMATCH")

    if shadow.risk_policy_version != target.risk_policy_version:
        add_reason(
            "STRATEGY_SHADOW_RISK_POLICY_VERSION_MISMATCH"
        )

    if not shadow.risk_assessment.risk.allowed:
        add_reason("STRATEGY_SHADOW_RISK_NOT_ALLOW")

    if shadow.order_intent is None:
        add_reason("STRATEGY_SHADOW_ORDER_INTENT_MISSING")
    else:
        intent = shadow.order_intent
        expected_projection = (
            candidate.candidate_id,
            candidate.condition_id,
            candidate.outcome,
            _jsonable(candidate.side),
            candidate.qty,
            candidate.limit_price,
            _jsonable(candidate.time_in_force),
            candidate.decision_ts_ms,
            candidate.market_data_ts_ms,
            candidate.alpha_id,
            candidate.alpha_version,
            candidate.risk_policy_version,
        )
        actual_projection = (
            intent.intent_id,
            intent.condition_id,
            intent.outcome,
            _jsonable(intent.side),
            intent.qty,
            intent.limit_price,
            _jsonable(intent.time_in_force),
            intent.decision_ts_ms,
            intent.market_data_ts_ms,
            intent.strategy_id,
            intent.strategy_version,
            intent.risk_policy_version,
        )
        if actual_projection != expected_projection:
            add_reason("ORDER_INTENT_CANDIDATE_PROJECTION_MISMATCH")

        if intent.strategy_id != target.alpha_id:
            add_reason("ORDER_INTENT_ALPHA_ID_MISMATCH")
        if intent.strategy_version != target.alpha_version:
            add_reason("ORDER_INTENT_ALPHA_VERSION_MISMATCH")
        if intent.risk_policy_version != target.risk_policy_version:
            add_reason("ORDER_INTENT_RISK_POLICY_VERSION_MISMATCH")

        if shadow.shadow_artifact is not None:
            if any(
                item.request.intent != intent
                for item in shadow.shadow_artifact.items
            ):
                add_reason("BATCH_SHADOW_ORDER_INTENT_MISMATCH")

    if shadow.protocol_version != CLODDS_SHADOW_PROTOCOL_V1:
        add_reason("SHADOW_PROTOCOL_VERSION_MISMATCH")
    if shadow.clodds_commit != AUDITED_CLODDS_COMMIT_V1:
        add_reason("SHADOW_CLODDS_COMMIT_MISMATCH")
    if shadow.mapping_version != CLODDS_MAPPING_VERSION_V1:
        add_reason("SHADOW_MAPPING_VERSION_MISMATCH")

    status = (
        BLOCKED
        if reasons
        else READY_FOR_POLICY_EVALUATION
    )

    provisional = ExecutionValidationArtifact(
        execution_run_id=execution_run_id,
        target=target,
        status=status,
        reasons=tuple(reasons),
        alpha_factory_artifact_hash=alpha.artifact_hash,
        strategy_candidate_hash=candidate_hash,
        strategy_shadow_artifact_hash=shadow.artifact_hash,
        strategy_shadow_run_id=shadow.run_id,
        protocol_version=shadow.protocol_version,
        clodds_commit=shadow.clodds_commit,
        mapping_version=shadow.mapping_version,
        artifact_hash="PENDING",
    )

    return ExecutionValidationArtifact(
        execution_run_id=provisional.execution_run_id,
        target=provisional.target,
        status=provisional.status,
        reasons=provisional.reasons,
        alpha_factory_artifact_hash=(
            provisional.alpha_factory_artifact_hash
        ),
        strategy_candidate_hash=(
            provisional.strategy_candidate_hash
        ),
        strategy_shadow_artifact_hash=(
            provisional.strategy_shadow_artifact_hash
        ),
        strategy_shadow_run_id=(
            provisional.strategy_shadow_run_id
        ),
        protocol_version=provisional.protocol_version,
        clodds_commit=provisional.clodds_commit,
        mapping_version=provisional.mapping_version,
        artifact_hash=execution_validation_artifact_hash(
            provisional
        ),
    )
