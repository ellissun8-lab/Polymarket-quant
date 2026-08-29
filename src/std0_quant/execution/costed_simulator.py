"""Execution simulator + cost/PnL integration v1.

Research/simulation only. No live order submission.

This layer composes existing deterministic simulators with:
- actual-fill VWAP;
- explicit maker/taker fees;
- maker rebates;
- slippage versus a supplied reference price;
- fill-adjusted mark-to-market / settlement PnL.

No PnL is assigned to unfilled quantity.
"""

from __future__ import annotations

from dataclasses import dataclass

from std0_quant.execution.cost_pnl import (
    ExecutionCostSummary,
    FeeSchedule,
    PnLSummary,
    mark_to_market_pnl,
    summarize_execution,
)
from std0_quant.execution.execution_timestamps import (
    CancelTimestamps,
    OrderTimestamps,
)
from std0_quant.execution.simulator import (
    ConfirmedTradeEvent,
    PassiveSimulationResult,
    simulate_passive_order,
)
from std0_quant.execution.taker_simulator import (
    AggressiveSimulationResult,
    AggressiveTIF,
    simulate_aggressive_order,
)


@dataclass(frozen=True)
class CostedPassiveResult:
    simulation: PassiveSimulationResult
    execution: ExecutionCostSummary
    pnl: PnLSummary


@dataclass(frozen=True)
class CostedAggressiveResult:
    simulation: AggressiveSimulationResult
    execution: ExecutionCostSummary
    pnl: PnLSummary


def simulate_passive_with_costs(
    *,
    side: str,
    order_qty: float,
    order_price: float,
    displayed_qty_at_accept: float,
    order_timestamps: OrderTimestamps,
    trades: list[ConfirmedTradeEvent],
    reference_price: float,
    mark_price: float,
    fee_schedule: FeeSchedule | None = None,
    cancel_timestamps: CancelTimestamps | None = None,
) -> CostedPassiveResult:
    simulation = simulate_passive_order(
        order_qty=order_qty,
        order_price=order_price,
        displayed_qty_at_accept=displayed_qty_at_accept,
        order_timestamps=order_timestamps,
        trades=trades,
        cancel_timestamps=cancel_timestamps,
    )

    execution = summarize_execution(
        side=side,
        requested_qty=order_qty,
        fills=simulation.fills,
        reference_price=reference_price,
        fee_schedule=fee_schedule,
    )

    pnl = mark_to_market_pnl(
        execution=execution,
        mark_price=mark_price,
    )

    return CostedPassiveResult(
        simulation=simulation,
        execution=execution,
        pnl=pnl,
    )


def simulate_aggressive_with_costs(
    *,
    side: str,
    tif: AggressiveTIF | str,
    order_qty: float,
    levels: list[tuple[float, float]],
    order_timestamps: OrderTimestamps,
    reference_price: float,
    mark_price: float,
    fee_schedule: FeeSchedule | None = None,
) -> CostedAggressiveResult:
    simulation = simulate_aggressive_order(
        side=side,
        tif=tif,
        order_qty=order_qty,
        levels=levels,
        order_timestamps=order_timestamps,
    )

    execution = summarize_execution(
        side=side,
        requested_qty=order_qty,
        fills=simulation.fills,
        reference_price=reference_price,
        fee_schedule=fee_schedule,
    )

    pnl = mark_to_market_pnl(
        execution=execution,
        mark_price=mark_price,
    )

    return CostedAggressiveResult(
        simulation=simulation,
        execution=execution,
        pnl=pnl,
    )
