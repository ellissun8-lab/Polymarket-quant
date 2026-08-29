"""Risk-gated aggressive execution pipeline v1.

Research/simulation only. No live order submission.

Pipeline:
OrderIntent
→ deterministic risk gate
→ reserve BUY capital
→ aggressive IOC/FOK simulator
→ actual-fill cost accounting
→ portfolio update
→ release unused reservation

A rejected order must not mutate portfolio state.
"""

from __future__ import annotations

from dataclasses import dataclass

from std0_quant.execution.cost_pnl import FeeSchedule
from std0_quant.execution.costed_simulator import (
    CostedAggressiveResult,
    simulate_aggressive_with_costs,
)
from std0_quant.execution.execution_timestamps import OrderTimestamps
from std0_quant.execution.portfolio import PortfolioState
from std0_quant.execution.risk import (
    RiskContext,
    RiskLimits,
    RiskOrderIntent,
    RiskResult,
    evaluate_order_risk,
)
from std0_quant.execution.taker_simulator import AggressiveTIF


_BPS = 10_000.0
_EPS = 1e-12


@dataclass(frozen=True)
class RiskGatedExecutionResult:
    risk: RiskResult
    execution: CostedAggressiveResult | None
    portfolio_cash_after: float
    portfolio_reserved_cash_after: float
    portfolio_realized_pnl_after: float


def execute_aggressive_with_risk(
    *,
    portfolio: PortfolioState,
    intent: RiskOrderIntent,
    tif: AggressiveTIF | str,
    levels: list[tuple[float, float]],
    order_timestamps: OrderTimestamps,
    reference_price: float,
    mark_price: float,
    limits: RiskLimits,
    context: RiskContext,
    fee_schedule: FeeSchedule | None = None,
) -> RiskGatedExecutionResult:
    """Evaluate risk, simulate execution, then apply actual fills."""

    fee_schedule = fee_schedule or FeeSchedule()

    if intent.side == "BUY":
        estimated_fee_cost = (
            intent.order_notional
            * fee_schedule.taker_fee_bps
            / _BPS
        )
    else:
        estimated_fee_cost = 0.0

    effective_context = RiskContext(
        now_ts_ms=context.now_ts_ms,
        market_data_ts_ms=context.market_data_ts_ms,
        kill_switch=context.kill_switch,
        estimated_fee_cost=estimated_fee_cost,
    )

    risk = evaluate_order_risk(
        portfolio=portfolio,
        intent=intent,
        limits=limits,
        context=effective_context,
    )

    if not risk.allowed:
        return RiskGatedExecutionResult(
            risk=risk,
            execution=None,
            portfolio_cash_after=portfolio.cash,
            portfolio_reserved_cash_after=portfolio.reserved_cash,
            portfolio_realized_pnl_after=portfolio.realized_pnl,
        )

    reserved_for_order = 0.0
    remaining_order_reservation = 0.0

    if intent.side == "BUY":
        reserved_for_order = risk.cash_required
        remaining_order_reservation = reserved_for_order
        portfolio.reserve_buy_cash(reserved_for_order)

    # A RiskOrderIntent is a LIMIT intent. Execution may consume only
    # venue-visible liquidity at or better than the stated limit price.
    if intent.side == "BUY":
        executable_levels = [
            (price, qty)
            for price, qty in levels
            if float(price) <= intent.limit_price + _EPS
        ]
    else:
        executable_levels = [
            (price, qty)
            for price, qty in levels
            if float(price) + _EPS >= intent.limit_price
        ]

    try:
        execution = simulate_aggressive_with_costs(
            side=intent.side,
            tif=tif,
            order_qty=intent.qty,
            levels=executable_levels,
            order_timestamps=order_timestamps,
            reference_price=reference_price,
            mark_price=mark_price,
            fee_schedule=fee_schedule,
        )

        fills = execution.simulation.fills

        for fill in fills:
            if intent.side == "BUY":
                if fill.liquidity == "maker":
                    fee_bps = fee_schedule.maker_fee_bps
                    rebate_bps = fee_schedule.maker_rebate_bps
                else:
                    fee_bps = fee_schedule.taker_fee_bps
                    rebate_bps = 0.0

                notional = fill.price * fill.qty
                fee_cost = notional * fee_bps / _BPS
                rebate_credit = notional * rebate_bps / _BPS

                net_cash_cost = (
                    notional + fee_cost - rebate_credit
                )

                if (
                    net_cash_cost
                    > remaining_order_reservation + _EPS
                ):
                    raise AssertionError(
                        "actual BUY fill cash cost exceeds "
                        "this order's reserved capital"
                    )

                portfolio.apply_buy_fill(
                    condition_id=intent.condition_id,
                    outcome=intent.outcome,
                    qty=fill.qty,
                    price=fill.price,
                    fee_cost=fee_cost,
                    rebate_credit=rebate_credit,
                    consume_reserved_cash=True,
                )

                remaining_order_reservation = max(
                    0.0,
                    remaining_order_reservation - net_cash_cost,
                )

            else:
                if fill.liquidity == "maker":
                    fee_bps = fee_schedule.maker_fee_bps
                    rebate_bps = fee_schedule.maker_rebate_bps
                else:
                    fee_bps = fee_schedule.taker_fee_bps
                    rebate_bps = 0.0

                notional = fill.price * fill.qty
                fee_cost = notional * fee_bps / _BPS
                rebate_credit = notional * rebate_bps / _BPS

                portfolio.apply_sell_fill(
                    condition_id=intent.condition_id,
                    outcome=intent.outcome,
                    qty=fill.qty,
                    price=fill.price,
                    fee_cost=fee_cost,
                    rebate_credit=rebate_credit,
                )

        if (
            intent.side == "BUY"
            and remaining_order_reservation > _EPS
        ):
            portfolio.release_reserved_cash(
                remaining_order_reservation
            )
            remaining_order_reservation = 0.0

        return RiskGatedExecutionResult(
            risk=risk,
            execution=execution,
            portfolio_cash_after=portfolio.cash,
            portfolio_reserved_cash_after=portfolio.reserved_cash,
            portfolio_realized_pnl_after=portfolio.realized_pnl,
        )

    except Exception:
        if (
            intent.side == "BUY"
            and remaining_order_reservation > _EPS
        ):
            portfolio.release_reserved_cash(
                remaining_order_reservation
            )
        raise
