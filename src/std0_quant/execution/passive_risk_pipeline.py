"""Risk-gated passive maker execution pipeline v1.

Research/simulation only. No live order submission.

Pipeline:
OrderIntent
→ deterministic risk gate
→ reserve BUY cash or SELL inventory
→ passive FIFO execution simulator
→ actual maker fills
→ fee/rebate accounting
→ portfolio update
→ terminal order releases its remaining reservation

If the simulated order remains resting/partially filled, its unused
reservation remains active.  No unfilled quantity receives fake PnL.
"""

from __future__ import annotations

from dataclasses import dataclass

from std0_quant.execution.cost_pnl import FeeSchedule
from std0_quant.execution.costed_simulator import (
    CostedPassiveResult,
    simulate_passive_with_costs,
)
from std0_quant.execution.execution_timestamps import (
    CancelTimestamps,
    OrderTimestamps,
)
from std0_quant.execution.order_state import OrderStatus
from std0_quant.execution.portfolio import PortfolioState
from std0_quant.execution.risk import (
    RiskContext,
    RiskLimits,
    RiskOrderIntent,
    RiskResult,
    evaluate_order_risk,
)
from std0_quant.execution.simulator import ConfirmedTradeEvent


_BPS = 10_000.0
_EPS = 1e-12

_TERMINAL = {
    OrderStatus.CANCELLED,
    OrderStatus.FILLED,
    OrderStatus.EXPIRED,
    OrderStatus.REJECTED,
}


@dataclass(frozen=True)
class RiskGatedPassiveResult:
    risk: RiskResult
    execution: CostedPassiveResult | None
    reservation_active: bool
    reserved_cash_remaining: float
    reserved_position_remaining: float
    portfolio_cash_after: float
    portfolio_reserved_cash_after: float
    portfolio_realized_pnl_after: float


def execute_passive_with_risk(
    *,
    portfolio: PortfolioState,
    intent: RiskOrderIntent,
    displayed_qty_at_accept: float,
    order_timestamps: OrderTimestamps,
    trades: list[ConfirmedTradeEvent],
    reference_price: float,
    mark_price: float,
    limits: RiskLimits,
    context: RiskContext,
    fee_schedule: FeeSchedule | None = None,
    cancel_timestamps: CancelTimestamps | None = None,
) -> RiskGatedPassiveResult:
    """Risk-check and simulate one passive maker order."""

    fee_schedule = fee_schedule or FeeSchedule()

    # Conservative BUY reservation:
    # count maker fee, do not assume rebate will be received.
    estimated_fee_cost = (
        intent.order_notional
        * fee_schedule.maker_fee_bps
        / _BPS
        if intent.side == "BUY"
        else 0.0
    )

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
        return RiskGatedPassiveResult(
            risk=risk,
            execution=None,
            reservation_active=False,
            reserved_cash_remaining=0.0,
            reserved_position_remaining=0.0,
            portfolio_cash_after=portfolio.cash,
            portfolio_reserved_cash_after=portfolio.reserved_cash,
            portfolio_realized_pnl_after=portfolio.realized_pnl,
        )

    remaining_cash_reservation = 0.0
    remaining_position_reservation = 0.0

    if intent.side == "BUY":
        remaining_cash_reservation = risk.cash_required
        portfolio.reserve_buy_cash(
            remaining_cash_reservation
        )
    else:
        remaining_position_reservation = intent.qty
        portfolio.reserve_sell_qty(
            intent.condition_id,
            intent.outcome,
            remaining_position_reservation,
        )

    try:
        execution = simulate_passive_with_costs(
            side=intent.side,
            order_qty=intent.qty,
            order_price=intent.limit_price,
            displayed_qty_at_accept=displayed_qty_at_accept,
            order_timestamps=order_timestamps,
            trades=trades,
            reference_price=reference_price,
            mark_price=mark_price,
            fee_schedule=fee_schedule,
            cancel_timestamps=cancel_timestamps,
        )

        for fill in execution.simulation.fills:
            if fill.liquidity != "maker":
                raise AssertionError(
                    "passive pipeline received non-maker fill"
                )

            notional = fill.price * fill.qty
            fee_cost = (
                notional
                * fee_schedule.maker_fee_bps
                / _BPS
            )
            rebate_credit = (
                notional
                * fee_schedule.maker_rebate_bps
                / _BPS
            )

            if intent.side == "BUY":
                net_cash_cost = (
                    notional
                    + fee_cost
                    - rebate_credit
                )

                if (
                    net_cash_cost
                    > remaining_cash_reservation + _EPS
                ):
                    raise AssertionError(
                        "maker BUY fill exceeds this "
                        "order's reserved cash"
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

                remaining_cash_reservation = max(
                    0.0,
                    remaining_cash_reservation
                    - net_cash_cost,
                )

            else:
                if (
                    fill.qty
                    > remaining_position_reservation + _EPS
                ):
                    raise AssertionError(
                        "maker SELL fill exceeds this "
                        "order's reserved position"
                    )

                # Consumed shares are no longer reserved because
                # they are now actually sold.
                portfolio.release_reserved_sell_qty(
                    intent.condition_id,
                    intent.outcome,
                    fill.qty,
                )

                remaining_position_reservation = max(
                    0.0,
                    remaining_position_reservation
                    - fill.qty,
                )

                portfolio.apply_sell_fill(
                    condition_id=intent.condition_id,
                    outcome=intent.outcome,
                    qty=fill.qty,
                    price=fill.price,
                    fee_cost=fee_cost,
                    rebate_credit=rebate_credit,
                )

        terminal = (
            execution.simulation.final_status
            in _TERMINAL
        )

        if terminal:
            if (
                intent.side == "BUY"
                and remaining_cash_reservation > _EPS
            ):
                portfolio.release_reserved_cash(
                    remaining_cash_reservation
                )
                remaining_cash_reservation = 0.0

            if (
                intent.side == "SELL"
                and remaining_position_reservation > _EPS
            ):
                portfolio.release_reserved_sell_qty(
                    intent.condition_id,
                    intent.outcome,
                    remaining_position_reservation,
                )
                remaining_position_reservation = 0.0

        reservation_active = (
            not terminal
            and (
                remaining_cash_reservation > _EPS
                or remaining_position_reservation > _EPS
            )
        )

        return RiskGatedPassiveResult(
            risk=risk,
            execution=execution,
            reservation_active=reservation_active,
            reserved_cash_remaining=remaining_cash_reservation,
            reserved_position_remaining=remaining_position_reservation,
            portfolio_cash_after=portfolio.cash,
            portfolio_reserved_cash_after=portfolio.reserved_cash,
            portfolio_realized_pnl_after=portfolio.realized_pnl,
        )

    except Exception:
        # Roll back only unused reservation owned by this order.
        # Already-applied fills remain actual simulated fills.
        if (
            intent.side == "BUY"
            and remaining_cash_reservation > _EPS
        ):
            portfolio.release_reserved_cash(
                remaining_cash_reservation
            )

        if (
            intent.side == "SELL"
            and remaining_position_reservation > _EPS
        ):
            portfolio.release_reserved_sell_qty(
                intent.condition_id,
                intent.outcome,
                remaining_position_reservation,
            )

        raise
