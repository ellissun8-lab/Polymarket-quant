"""Deterministic strategy-to-SHADOW orchestration v1.

This module binds a StrategyOrderCandidate, deterministic risk evidence,
OrderIntent, and Batch SHADOW evidence into one auditable artifact.

SHADOW_PASS is execution-path evidence only. It is not execution validation
PASS and does not imply registry promotion or production eligibility.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from typing import Any

from std0_quant.execution.batch_shadow_runner import (
    BatchShadowArtifact,
    BatchShadowRequest,
    run_shadow_batch,
)
from std0_quant.execution.clodds_mapping import CLODDS_MAPPING_VERSION_V1
from std0_quant.execution.clodds_shadow_protocol import (
    AUDITED_CLODDS_COMMIT_V1,
    CLODDS_SHADOW_PROTOCOL_V1,
)
from std0_quant.execution.contracts import OrderIntent
from std0_quant.execution.portfolio import PortfolioState
from std0_quant.execution.risk import RiskContext, RiskLimits
from std0_quant.execution.strategy_candidate import (
    StrategyOrderCandidate,
    StrategyRiskAssessment,
    assess_strategy_candidate,
    build_order_intent,
)


STRATEGY_SHADOW_RUN_SCHEMA_V1 = "strategy_shadow_run_artifact_v1"
STRATEGY_SHADOW_RUNNER_VERSION_V1 = "strategy_shadow_run_v1"
STRATEGY_SHADOW_MODE_V1 = "SHADOW"


_VALID_STATUSES = {
    "RISK_REJECTED",
    "SHADOW_PASS",
    "SHADOW_FAIL",
}


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
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
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
class StrategyShadowRunArtifact:
    run_id: str
    status: str
    candidate_hash: str
    risk_policy_version: str
    portfolio_hash: str
    risk_limits_hash: str
    risk_context_hash: str
    risk_assessment: StrategyRiskAssessment
    order_intent: OrderIntent | None
    shadow_run_id: str | None
    shadow_artifact: BatchShadowArtifact | None
    artifact_hash: str
    runner_version: str = STRATEGY_SHADOW_RUNNER_VERSION_V1
    mode: str = STRATEGY_SHADOW_MODE_V1
    protocol_version: str = CLODDS_SHADOW_PROTOCOL_V1
    clodds_commit: str = AUDITED_CLODDS_COMMIT_V1
    mapping_version: str = CLODDS_MAPPING_VERSION_V1
    schema_version: str = STRATEGY_SHADOW_RUN_SCHEMA_V1

    def __post_init__(self) -> None:
        _nonempty(self.run_id, "run_id")
        _nonempty(self.candidate_hash, "candidate_hash")
        _nonempty(self.risk_policy_version, "risk_policy_version")
        _nonempty(self.portfolio_hash, "portfolio_hash")
        _nonempty(self.risk_limits_hash, "risk_limits_hash")
        _nonempty(self.risk_context_hash, "risk_context_hash")
        _nonempty(self.artifact_hash, "artifact_hash")

        if self.status not in _VALID_STATUSES:
            raise ValueError("unsupported strategy shadow status")

        if not isinstance(self.risk_assessment, StrategyRiskAssessment):
            raise TypeError("risk_assessment must be StrategyRiskAssessment")

        if self.candidate_hash != self.risk_assessment.candidate_hash:
            raise ValueError("candidate hash mismatch")
        if self.risk_policy_version != self.risk_assessment.risk_policy_version:
            raise ValueError("risk policy version mismatch")
        if self.portfolio_hash != self.risk_assessment.portfolio_hash:
            raise ValueError("portfolio hash mismatch")
        if self.risk_limits_hash != self.risk_assessment.risk_limits_hash:
            raise ValueError("risk limits hash mismatch")
        if self.risk_context_hash != self.risk_assessment.risk_context_hash:
            raise ValueError("risk context hash mismatch")

        if self.runner_version != STRATEGY_SHADOW_RUNNER_VERSION_V1:
            raise ValueError("unsupported runner_version")
        if self.mode != STRATEGY_SHADOW_MODE_V1:
            raise ValueError("strategy shadow mode must be SHADOW")
        if self.protocol_version != CLODDS_SHADOW_PROTOCOL_V1:
            raise ValueError("unsupported shadow protocol")
        if self.clodds_commit != AUDITED_CLODDS_COMMIT_V1:
            raise ValueError("unsupported audited Clodds commit")
        if self.mapping_version != CLODDS_MAPPING_VERSION_V1:
            raise ValueError("unsupported mapping version")
        if self.schema_version != STRATEGY_SHADOW_RUN_SCHEMA_V1:
            raise ValueError("unsupported schema_version")

        if self.status == "RISK_REJECTED":
            if self.order_intent is not None:
                raise ValueError("RISK_REJECTED cannot contain OrderIntent")
            if self.shadow_run_id is not None:
                raise ValueError("RISK_REJECTED cannot contain shadow_run_id")
            if self.shadow_artifact is not None:
                raise ValueError("RISK_REJECTED cannot contain shadow artifact")
        else:
            if not isinstance(self.order_intent, OrderIntent):
                raise TypeError("shadow result requires OrderIntent")
            if self.shadow_run_id is None:
                raise ValueError("shadow result requires shadow_run_id")
            _nonempty(self.shadow_run_id, "shadow_run_id")
            if not isinstance(self.shadow_artifact, BatchShadowArtifact):
                raise TypeError("shadow result requires BatchShadowArtifact")

            if self.shadow_artifact.run_id != self.shadow_run_id:
                raise ValueError("shadow run id mismatch")
            if self.shadow_artifact.n_total != 1:
                raise ValueError("strategy shadow v1 requires exactly one shadow item")

            if self.status == "SHADOW_PASS":
                if self.shadow_artifact.n_pass != 1 or self.shadow_artifact.n_fail != 0:
                    raise ValueError("SHADOW_PASS requires one PASS and zero FAIL")
            if self.status == "SHADOW_FAIL":
                if self.shadow_artifact.n_pass != 0 or self.shadow_artifact.n_fail != 1:
                    raise ValueError("SHADOW_FAIL requires zero PASS and one FAIL")


def _artifact_payload(artifact: StrategyShadowRunArtifact) -> dict[str, Any]:
    shadow = artifact.shadow_artifact

    return {
        "status": artifact.status,
        "candidate_hash": artifact.candidate_hash,
        "risk_policy_version": artifact.risk_policy_version,
        "portfolio_hash": artifact.portfolio_hash,
        "risk_limits_hash": artifact.risk_limits_hash,
        "risk_context_hash": artifact.risk_context_hash,
        "risk_assessment": artifact.risk_assessment,
        "order_intent": artifact.order_intent,
        "shadow_artifact": (
            None
            if shadow is None
            else {
                "artifact_hash": shadow.artifact_hash,
                "n_total": shadow.n_total,
                "n_pass": shadow.n_pass,
                "n_fail": shadow.n_fail,
                "runner_version": shadow.runner_version,
                "mode": shadow.mode,
                "protocol_version": shadow.protocol_version,
                "clodds_commit": shadow.clodds_commit,
                "mapping_version": shadow.mapping_version,
                "schema_version": shadow.schema_version,
            }
        ),
        "runner_version": artifact.runner_version,
        "mode": artifact.mode,
        "protocol_version": artifact.protocol_version,
        "clodds_commit": artifact.clodds_commit,
        "mapping_version": artifact.mapping_version,
        "schema_version": artifact.schema_version,
    }


def strategy_shadow_run_artifact_hash(
    artifact: StrategyShadowRunArtifact,
) -> str:
    return hashlib.sha256(
        _canonical_json(_artifact_payload(artifact)).encode("utf-8")
    ).hexdigest()


def _build_artifact(
    *,
    run_id: str,
    status: str,
    assessment: StrategyRiskAssessment,
    order_intent: OrderIntent | None,
    shadow_run_id: str | None,
    shadow_artifact: BatchShadowArtifact | None,
) -> StrategyShadowRunArtifact:
    provisional = StrategyShadowRunArtifact(
        run_id=run_id,
        status=status,
        candidate_hash=assessment.candidate_hash,
        risk_policy_version=assessment.risk_policy_version,
        portfolio_hash=assessment.portfolio_hash,
        risk_limits_hash=assessment.risk_limits_hash,
        risk_context_hash=assessment.risk_context_hash,
        risk_assessment=assessment,
        order_intent=order_intent,
        shadow_run_id=shadow_run_id,
        shadow_artifact=shadow_artifact,
        artifact_hash="PENDING",
    )

    return StrategyShadowRunArtifact(
        run_id=provisional.run_id,
        status=provisional.status,
        candidate_hash=provisional.candidate_hash,
        risk_policy_version=provisional.risk_policy_version,
        portfolio_hash=provisional.portfolio_hash,
        risk_limits_hash=provisional.risk_limits_hash,
        risk_context_hash=provisional.risk_context_hash,
        risk_assessment=provisional.risk_assessment,
        order_intent=provisional.order_intent,
        shadow_run_id=provisional.shadow_run_id,
        shadow_artifact=provisional.shadow_artifact,
        artifact_hash=strategy_shadow_run_artifact_hash(provisional),
    )


def run_strategy_shadow(
    candidate: StrategyOrderCandidate,
    *,
    portfolio: PortfolioState,
    limits: RiskLimits,
    context: RiskContext,
    market_condition_id: str,
    tokens: tuple[tuple[str, str], ...],
    post_only: bool,
    client: Any,
    run_id: str,
    shadow_run_id: str,
) -> StrategyShadowRunArtifact:
    run_id = _nonempty(run_id, "run_id")
    _nonempty(shadow_run_id, "shadow_run_id")

    assessment = assess_strategy_candidate(
        candidate,
        portfolio=portfolio,
        limits=limits,
        context=context,
    )

    if not assessment.risk.allowed:
        return _build_artifact(
            run_id=run_id,
            status="RISK_REJECTED",
            assessment=assessment,
            order_intent=None,
            shadow_run_id=None,
            shadow_artifact=None,
        )

    order_intent = build_order_intent(
        candidate,
        assessment,
        portfolio=portfolio,
        limits=limits,
        context=context,
    )

    request = BatchShadowRequest(
        intent=order_intent,
        market_condition_id=market_condition_id,
        tokens=tuple(tokens),
        post_only=post_only,
    )

    shadow = run_shadow_batch(
        (request,),
        client=client,
        run_id=shadow_run_id,
    )

    status = (
        "SHADOW_PASS"
        if shadow.n_pass == 1 and shadow.n_fail == 0
        else "SHADOW_FAIL"
    )

    return _build_artifact(
        run_id=run_id,
        status=status,
        assessment=assessment,
        order_intent=order_intent,
        shadow_run_id=shadow_run_id,
        shadow_artifact=shadow,
    )
