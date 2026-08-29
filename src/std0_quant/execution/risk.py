"""Deterministic portfolio risk gate v1.

Research/simulation only. No live order submission.

The gate is pure and side-effect free:
- it does not mutate PortfolioState;
- it does not reserve cash;
- it does not submit orders.

Every rejection is explicit and auditable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math

from std0_quant.execution.portfolio import PortfolioState


_EPS = 1e-12


class RiskDecision(str, Enum):
    ALLOW = "ALLOW"
    REJECT = "REJECT"


@dataclass(frozen=True)
class RiskLimits:
    max_order_notional: float
    max_market_exposure: float
    max_gross_exposure: float
    max_daily_loss: float
    max_market_data_age_ms: float

    def __post_init__(self) -> None:
        for name in (
            "max_order_notional",
            "max_market_exposure",
            "max_gross_exposure",
            "max_daily_loss",
            "max_market_data_age_ms",
        ):
            value = _nonnegative_finite(
                getattr(self, name),
                name,
            )
            object.__setattr__(self, name, value)


@dataclass(frozen=True)
class RiskOrderIntent:
    condition_id: str
    outcome: str
    side: str
    qty: float
    limit_price: float

    def __post_init__(self) -> None:
        condition_id = str(self.condition_id).strip()
        outcome = str(self.outcome).strip()
        side = str(self.side).upper()

        if not condition_id:
            raise ValueError(
                "condition_id must be non-empty"
            )

        if not outcome:
            raise ValueError(
                "outcome must be non-empty"
            )

        if side not in {"BUY", "SELL"}:
            raise ValueError(
                "side must be BUY or SELL"
            )

        object.__setattr__(
            self,
            "condition_id",
            condition_id,
        )
        object.__setattr__(
            self,
            "outcome",
            outcome,
        )
        object.__setattr__(
            self,
            "side",
            side,
        )
        object.__setattr__(
            self,
            "qty",
            _positive_finite(
                self.qty,
                "qty",
            ),
        )
        object.__setattr__(
            self,
            "limit_price",
            _positive_finite(
                self.limit_price,
                "limit_price",
            ),
        )

    @property
    def order_notional(self) -> float:
        return self.qty * self.limit_price


@dataclass(frozen=True)
class RiskContext:
    now_ts_ms: float
    market_data_ts_ms: float
    kill_switch: bool = False
    estimated_fee_cost: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "now_ts_ms",
            _nonnegative_finite(
                self.now_ts_ms,
                "now_ts_ms",
            ),
        )
        object.__setattr__(
            self,
            "market_data_ts_ms",
            _nonnegative_finite(
                self.market_data_ts_ms,
                "market_data_ts_ms",
            ),
        )
        object.__setattr__(
            self,
            "estimated_fee_cost",
            _nonnegative_finite(
                self.estimated_fee_cost,
                "estimated_fee_cost",
            ),
        )


@dataclass(frozen=True)
class RiskResult:
    decision: RiskDecision
    reasons: tuple[str, ...]
    order_notional: float
    cash_required: float
    market_exposure_before: float
    market_exposure_after: float
    gross_exposure_before: float
    gross_exposure_after: float

    @property
    def allowed(self) -> bool:
        return self.decision == RiskDecision.ALLOW


def evaluate_order_risk(
    *,
    portfolio: PortfolioState,
    intent: RiskOrderIntent,
    limits: RiskLimits,
    context: RiskContext,
) -> RiskResult:
    """Evaluate one proposed order without mutating portfolio state."""

    reasons: list[str] = []

    if context.kill_switch:
        reasons.append("KILL_SWITCH_ACTIVE")

    if (
        context.market_data_ts_ms
        > context.now_ts_ms + _EPS
    ):
        reasons.append("MARKET_DATA_FROM_FUTURE")
        market_data_age = None
    else:
        market_data_age = (
            context.now_ts_ms
            - context.market_data_ts_ms
        )

        if (
            market_data_age
            > limits.max_market_data_age_ms + _EPS
        ):
            reasons.append("STALE_MARKET_DATA")

    order_notional = intent.order_notional

    cash_required = (
        order_notional + context.estimated_fee_cost
        if intent.side == "BUY"
        else 0.0
    )

    if (
        order_notional
        > limits.max_order_notional + _EPS
    ):
        reasons.append("MAX_ORDER_NOTIONAL_EXCEEDED")

    market_exposure_before = _market_exposure(
        portfolio,
        intent.condition_id,
    )
    gross_exposure_before = (
        portfolio.gross_cost_exposure
    )

    if intent.side == "BUY":
        if (
            cash_required
            > portfolio.available_cash + _EPS
        ):
            reasons.append("INSUFFICIENT_AVAILABLE_CASH")

        market_exposure_after = (
            market_exposure_before
            + order_notional
        )
        gross_exposure_after = (
            gross_exposure_before
            + order_notional
        )

    else:
        position = portfolio.positions.get(
            (
                intent.condition_id,
                intent.outcome,
            )
        )

        position_qty = (
            position.qty
            if position is not None
            else 0.0
        )
        available_position_qty = (
            portfolio.available_position_qty(
                intent.condition_id,
                intent.outcome,
            )
        )
        position_cost_basis = (
            position.cost_basis
            if position is not None
            else 0.0
        )
        average_cost = (
            position.average_cost
            if (
                position is not None
                and position.average_cost is not None
            )
            else 0.0
        )

        if intent.qty > available_position_qty + _EPS:
            reasons.append("INSUFFICIENT_AVAILABLE_POSITION")

        removed_cost = min(
            position_cost_basis,
            average_cost * intent.qty,
        )

        market_exposure_after = max(
            0.0,
            market_exposure_before - removed_cost,
        )
        gross_exposure_after = max(
            0.0,
            gross_exposure_before - removed_cost,
        )

    if (
        market_exposure_after
        > limits.max_market_exposure + _EPS
    ):
        reasons.append("MAX_MARKET_EXPOSURE_EXCEEDED")

    if (
        gross_exposure_after
        > limits.max_gross_exposure + _EPS
    ):
        reasons.append("MAX_GROSS_EXPOSURE_EXCEEDED")

    if (
        portfolio.realized_pnl
        <= -limits.max_daily_loss - _EPS
    ):
        reasons.append("MAX_DAILY_LOSS_REACHED")

    decision = (
        RiskDecision.ALLOW
        if not reasons
        else RiskDecision.REJECT
    )

    return RiskResult(
        decision=decision,
        reasons=tuple(reasons),
        order_notional=order_notional,
        cash_required=cash_required,
        market_exposure_before=market_exposure_before,
        market_exposure_after=market_exposure_after,
        gross_exposure_before=gross_exposure_before,
        gross_exposure_after=gross_exposure_after,
    )


def _market_exposure(
    portfolio: PortfolioState,
    condition_id: str,
) -> float:
    return sum(
        position.cost_basis
        for (cid, _), position
        in portfolio.positions.items()
        if cid == condition_id
    )


def _positive_finite(
    value: float,
    name: str,
) -> float:
    value = float(value)

    if not math.isfinite(value) or value <= 0:
        raise ValueError(
            f"{name} must be finite and > 0"
        )

    return value


def _nonnegative_finite(
    value: float,
    name: str,
) -> float:
    value = float(value)

    if not math.isfinite(value) or value < 0:
        raise ValueError(
            f"{name} must be finite and >= 0"
        )

    return value
