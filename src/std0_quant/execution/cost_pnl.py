"""Execution cost and fill-adjusted PnL model v1.

Research/simulation only. No live order submission.

Important:
- fees and rebates are explicit configuration inputs;
- no Polymarket fee schedule is hard-coded;
- PnL uses actual simulated fills only;
- unfilled quantity never receives hypothetical PnL;
- positive slippage means adverse execution.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

from std0_quant.execution.fill_model import Fill


_BPS = 10_000.0
_EPS = 1e-12


@dataclass(frozen=True)
class FeeSchedule:
    maker_fee_bps: float = 0.0
    taker_fee_bps: float = 0.0
    maker_rebate_bps: float = 0.0

    def __post_init__(self) -> None:
        for name in (
            "maker_fee_bps",
            "taker_fee_bps",
            "maker_rebate_bps",
        ):
            value = _nonnegative_finite(
                getattr(self, name),
                name,
            )
            object.__setattr__(self, name, value)


@dataclass(frozen=True)
class ExecutionCostSummary:
    side: str
    requested_qty: float
    filled_qty: float
    unfilled_qty: float
    fill_ratio: float
    average_fill_price: float | None
    reference_price: float
    gross_notional: float
    fee_cost: float
    rebate_credit: float
    net_fee_cost: float
    slippage_per_unit: float | None
    slippage_cost: float
    total_execution_cost: float


@dataclass(frozen=True)
class PnLSummary:
    side: str
    filled_qty: float
    average_fill_price: float | None
    mark_price: float
    gross_pnl: float
    net_fee_cost: float
    net_pnl: float


def summarize_execution(
    *,
    side: str,
    requested_qty: float,
    fills: tuple[Fill, ...] | list[Fill],
    reference_price: float,
    fee_schedule: FeeSchedule | None = None,
) -> ExecutionCostSummary:
    """Summarize realized execution quality from actual fills."""

    side = _checked_side(side)
    requested_qty = _positive_finite(
        requested_qty,
        "requested_qty",
    )
    reference_price = _positive_finite(
        reference_price,
        "reference_price",
    )
    fee_schedule = fee_schedule or FeeSchedule()

    checked_fills = tuple(fills)

    filled_qty = 0.0
    gross_notional = 0.0
    fee_cost = 0.0
    rebate_credit = 0.0

    for index, fill in enumerate(checked_fills):
        price = _positive_finite(
            fill.price,
            f"fills[{index}].price",
        )
        qty = _positive_finite(
            fill.qty,
            f"fills[{index}].qty",
        )

        if fill.liquidity not in {"maker", "taker"}:
            raise ValueError(
                f"unsupported liquidity type: {fill.liquidity}"
            )

        notional = price * qty

        filled_qty += qty
        gross_notional += notional

        if fill.liquidity == "maker":
            fee_cost += (
                notional
                * fee_schedule.maker_fee_bps
                / _BPS
            )
            rebate_credit += (
                notional
                * fee_schedule.maker_rebate_bps
                / _BPS
            )
        else:
            fee_cost += (
                notional
                * fee_schedule.taker_fee_bps
                / _BPS
            )

    if filled_qty > requested_qty + _EPS:
        raise ValueError(
            "filled quantity exceeds requested quantity"
        )

    unfilled_qty = max(
        0.0,
        requested_qty - filled_qty,
    )

    fill_ratio = filled_qty / requested_qty

    if filled_qty <= _EPS:
        average_fill_price = None
        slippage_per_unit = None
        slippage_cost = 0.0
    else:
        average_fill_price = (
            gross_notional / filled_qty
        )

        if side == "BUY":
            slippage_per_unit = (
                average_fill_price - reference_price
            )
        else:
            slippage_per_unit = (
                reference_price - average_fill_price
            )

        slippage_cost = (
            slippage_per_unit * filled_qty
        )

    net_fee_cost = fee_cost - rebate_credit

    total_execution_cost = (
        slippage_cost + net_fee_cost
    )

    return ExecutionCostSummary(
        side=side,
        requested_qty=requested_qty,
        filled_qty=filled_qty,
        unfilled_qty=unfilled_qty,
        fill_ratio=fill_ratio,
        average_fill_price=average_fill_price,
        reference_price=reference_price,
        gross_notional=gross_notional,
        fee_cost=fee_cost,
        rebate_credit=rebate_credit,
        net_fee_cost=net_fee_cost,
        slippage_per_unit=slippage_per_unit,
        slippage_cost=slippage_cost,
        total_execution_cost=total_execution_cost,
    )


def mark_to_market_pnl(
    *,
    execution: ExecutionCostSummary,
    mark_price: float,
) -> PnLSummary:
    """Compute fill-adjusted PnL against a mark or settlement price."""

    mark_price = _nonnegative_finite(
        mark_price,
        "mark_price",
    )

    if execution.filled_qty <= _EPS:
        gross_pnl = 0.0
    elif execution.side == "BUY":
        gross_pnl = (
            mark_price
            - float(execution.average_fill_price)
        ) * execution.filled_qty
    else:
        gross_pnl = (
            float(execution.average_fill_price)
            - mark_price
        ) * execution.filled_qty

    net_pnl = (
        gross_pnl - execution.net_fee_cost
    )

    return PnLSummary(
        side=execution.side,
        filled_qty=execution.filled_qty,
        average_fill_price=execution.average_fill_price,
        mark_price=mark_price,
        gross_pnl=gross_pnl,
        net_fee_cost=execution.net_fee_cost,
        net_pnl=net_pnl,
    )


def _checked_side(side: str) -> str:
    side = str(side).upper()

    if side not in {"BUY", "SELL"}:
        raise ValueError("side must be BUY or SELL")

    return side


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
