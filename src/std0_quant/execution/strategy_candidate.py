"""Deterministic strategy candidate to risk-gated OrderIntent bridge v1.

Research/shadow only. This module does not submit orders or enable LIVE
execution. It binds alpha provenance to the existing deterministic risk gate.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any

from std0_quant.execution.contracts import OrderIntent
from std0_quant.execution.portfolio import PortfolioState
from std0_quant.execution.risk import (
    RiskContext,
    RiskDecision,
    RiskLimits,
    RiskOrderIntent,
    RiskResult,
    evaluate_order_risk,
)


STRATEGY_ORDER_CANDIDATE_SCHEMA_V1 = "strategy_order_candidate_v1"
STRATEGY_RISK_ASSESSMENT_SCHEMA_V1 = "strategy_risk_assessment_v1"


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


def _sha256(value: Any) -> str:
    return hashlib.sha256(
        _canonical_json(value).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class StrategyOrderCandidate:
    candidate_id: str
    alpha_id: str
    alpha_version: str
    risk_policy_version: str
    condition_id: str
    outcome: str
    side: str
    qty: float
    limit_price: float
    time_in_force: str
    decision_ts_ms: float
    market_data_ts_ms: float
    schema_version: str = STRATEGY_ORDER_CANDIDATE_SCHEMA_V1

    def __post_init__(self) -> None:
        for name in (
            "candidate_id",
            "alpha_id",
            "alpha_version",
            "risk_policy_version",
            "condition_id",
            "outcome",
            "side",
            "time_in_force",
        ):
            object.__setattr__(
                self,
                name,
                _nonempty(getattr(self, name), name),
            )

        risk_intent = RiskOrderIntent(
            condition_id=self.condition_id,
            outcome=self.outcome,
            side=self.side,
            qty=self.qty,
            limit_price=self.limit_price,
        )
        object.__setattr__(self, "condition_id", risk_intent.condition_id)
        object.__setattr__(self, "outcome", risk_intent.outcome)
        object.__setattr__(self, "side", risk_intent.side)
        object.__setattr__(self, "qty", risk_intent.qty)
        object.__setattr__(self, "limit_price", risk_intent.limit_price)

        tif = self.time_in_force.upper()
        if tif not in {"GTC", "IOC", "FOK"}:
            raise ValueError("time_in_force must be GTC, IOC or FOK")
        object.__setattr__(self, "time_in_force", tif)

        decision_ts_ms = float(self.decision_ts_ms)
        market_data_ts_ms = float(self.market_data_ts_ms)

        if (
            not math.isfinite(decision_ts_ms)
            or not math.isfinite(market_data_ts_ms)
            or decision_ts_ms < 0
            or market_data_ts_ms < 0
        ):
            raise ValueError("timestamps must be nonnegative finite numbers")

        if market_data_ts_ms > decision_ts_ms:
            raise ValueError(
                "market_data_ts_ms cannot be after decision_ts_ms"
            )

        object.__setattr__(self, "decision_ts_ms", decision_ts_ms)
        object.__setattr__(self, "market_data_ts_ms", market_data_ts_ms)

        if self.schema_version != STRATEGY_ORDER_CANDIDATE_SCHEMA_V1:
            raise ValueError(
                "unsupported StrategyOrderCandidate schema_version"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "alpha_id": self.alpha_id,
            "alpha_version": self.alpha_version,
            "risk_policy_version": self.risk_policy_version,
            "condition_id": self.condition_id,
            "outcome": self.outcome,
            "side": self.side,
            "qty": self.qty,
            "limit_price": self.limit_price,
            "time_in_force": self.time_in_force,
            "decision_ts_ms": self.decision_ts_ms,
            "market_data_ts_ms": self.market_data_ts_ms,
            "schema_version": self.schema_version,
        }


def strategy_candidate_hash(
    candidate: StrategyOrderCandidate,
) -> str:
    return _sha256(candidate.to_dict())


def risk_limits_hash(limits: RiskLimits) -> str:
    if not isinstance(limits, RiskLimits):
        raise TypeError("limits must be RiskLimits")

    return _sha256(
        {
            "max_order_notional": limits.max_order_notional,
            "max_market_exposure": limits.max_market_exposure,
            "max_gross_exposure": limits.max_gross_exposure,
            "max_daily_loss": limits.max_daily_loss,
            "max_market_data_age_ms": limits.max_market_data_age_ms,
        }
    )


def risk_context_hash(context: RiskContext) -> str:
    if not isinstance(context, RiskContext):
        raise TypeError("context must be RiskContext")

    return _sha256(
        {
            "now_ts_ms": context.now_ts_ms,
            "market_data_ts_ms": context.market_data_ts_ms,
            "kill_switch": context.kill_switch,
            "estimated_fee_cost": context.estimated_fee_cost,
        }
    )


def portfolio_state_hash(portfolio: PortfolioState) -> str:
    if not isinstance(portfolio, PortfolioState):
        raise TypeError("portfolio must be PortfolioState")

    positions = []
    for key, position in sorted(
        portfolio.positions.items(),
        key=lambda row: (row[0][0], row[0][1]),
    ):
        positions.append(
            {
                "key_condition_id": key[0],
                "key_outcome": key[1],
                "condition_id": position.condition_id,
                "outcome": position.outcome,
                "qty": position.qty,
                "cost_basis": position.cost_basis,
            }
        )

    reserved_positions = [
        {
            "condition_id": key[0],
            "outcome": key[1],
            "qty": qty,
        }
        for key, qty in sorted(
            portfolio.reserved_positions.items(),
            key=lambda row: (row[0][0], row[0][1]),
        )
    ]

    return _sha256(
        {
            "cash": portfolio.cash,
            "reserved_cash": portfolio.reserved_cash,
            "realized_pnl": portfolio.realized_pnl,
            "positions": positions,
            "reserved_positions": reserved_positions,
        }
    )


@dataclass(frozen=True)
class StrategyRiskAssessment:
    candidate_hash: str
    risk_policy_version: str
    risk_limits_hash: str
    portfolio_hash: str
    risk_context_hash: str
    risk: RiskResult
    schema_version: str = STRATEGY_RISK_ASSESSMENT_SCHEMA_V1

    def __post_init__(self) -> None:
        for name in (
            "candidate_hash",
            "risk_policy_version",
            "risk_limits_hash",
            "portfolio_hash",
            "risk_context_hash",
        ):
            object.__setattr__(
                self,
                name,
                _nonempty(getattr(self, name), name),
            )

        if not isinstance(self.risk, RiskResult):
            raise TypeError("risk must be RiskResult")

        if self.schema_version != STRATEGY_RISK_ASSESSMENT_SCHEMA_V1:
            raise ValueError(
                "unsupported StrategyRiskAssessment schema_version"
            )


def assess_strategy_candidate(
    candidate: StrategyOrderCandidate,
    *,
    portfolio: PortfolioState,
    limits: RiskLimits,
    context: RiskContext,
) -> StrategyRiskAssessment:
    if not isinstance(candidate, StrategyOrderCandidate):
        raise TypeError("candidate must be StrategyOrderCandidate")
    if not isinstance(portfolio, PortfolioState):
        raise TypeError("portfolio must be PortfolioState")
    if not isinstance(limits, RiskLimits):
        raise TypeError("limits must be RiskLimits")
    if not isinstance(context, RiskContext):
        raise TypeError("context must be RiskContext")

    if context.now_ts_ms != candidate.decision_ts_ms:
        raise ValueError(
            "risk context now_ts_ms must match candidate decision_ts_ms"
        )
    if context.market_data_ts_ms != candidate.market_data_ts_ms:
        raise ValueError(
            "risk context market_data_ts_ms must match candidate"
        )

    risk_intent = RiskOrderIntent(
        condition_id=candidate.condition_id,
        outcome=candidate.outcome,
        side=candidate.side,
        qty=candidate.qty,
        limit_price=candidate.limit_price,
    )

    risk = evaluate_order_risk(
        portfolio=portfolio,
        intent=risk_intent,
        limits=limits,
        context=context,
    )

    return StrategyRiskAssessment(
        candidate_hash=strategy_candidate_hash(candidate),
        risk_policy_version=candidate.risk_policy_version,
        risk_limits_hash=risk_limits_hash(limits),
        portfolio_hash=portfolio_state_hash(portfolio),
        risk_context_hash=risk_context_hash(context),
        risk=risk,
    )


def build_order_intent(
    candidate: StrategyOrderCandidate,
    assessment: StrategyRiskAssessment,
    *,
    portfolio: PortfolioState,
    limits: RiskLimits,
    context: RiskContext,
) -> OrderIntent:
    if not isinstance(candidate, StrategyOrderCandidate):
        raise TypeError("candidate must be StrategyOrderCandidate")
    if not isinstance(assessment, StrategyRiskAssessment):
        raise TypeError("assessment must be StrategyRiskAssessment")

    if assessment.candidate_hash != strategy_candidate_hash(candidate):
        raise ValueError("candidate hash mismatch")

    if assessment.risk_policy_version != candidate.risk_policy_version:
        raise ValueError("risk policy version mismatch")

    if assessment.portfolio_hash != portfolio_state_hash(portfolio):
        raise ValueError("portfolio hash mismatch")

    if assessment.risk_limits_hash != risk_limits_hash(limits):
        raise ValueError("risk limits hash mismatch")

    if assessment.risk_context_hash != risk_context_hash(context):
        raise ValueError("risk context hash mismatch")

    if assessment.risk.decision != RiskDecision.ALLOW:
        raise ValueError("risk decision must be ALLOW")

    return OrderIntent(
        intent_id=candidate.candidate_id,
        condition_id=candidate.condition_id,
        outcome=candidate.outcome,
        side=candidate.side,
        qty=candidate.qty,
        limit_price=candidate.limit_price,
        time_in_force=candidate.time_in_force,
        decision_ts_ms=candidate.decision_ts_ms,
        market_data_ts_ms=candidate.market_data_ts_ms,
        strategy_id=candidate.alpha_id,
        strategy_version=candidate.alpha_version,
        risk_policy_version=candidate.risk_policy_version,
    )
