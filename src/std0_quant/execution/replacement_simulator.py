"""Conservative cancel-then-replace simulator v1.

Research/simulation only. No live order submission.

Rules:
- old order is simulated through venue-effective cancellation;
- replacement is allowed only if the old order actually ends CANCELLED;
- conservative v1 waits for client receipt of cancel acknowledgement before
  sending the replacement;
- replacement quantity is explicit;
- replacement receives completely fresh FIFO priority;
- no old queue position or old fills leak into the replacement order.
"""

from __future__ import annotations

from dataclasses import dataclass

from std0_quant.execution.cancel_replace import (
    create_replacement_after_cancel,
)
from std0_quant.execution.execution_timestamps import (
    CancelTimestamps,
    OrderTimestamps,
)
from std0_quant.execution.order_state import (
    OrderStateMachine,
    OrderStatus,
)
from std0_quant.execution.simulator import (
    ConfirmedTradeEvent,
    PassiveSimulationResult,
    simulate_passive_order,
)


_EPS = 1e-12


@dataclass(frozen=True)
class CancelReplaceSimulationResult:
    old_order: PassiveSimulationResult
    replacement_order: PassiveSimulationResult


def simulate_cancel_then_replace(
    *,
    old_order_qty: float,
    old_order_price: float,
    old_displayed_qty_at_accept: float,
    old_order_timestamps: OrderTimestamps,
    old_trades: list[ConfirmedTradeEvent],
    cancel_timestamps: CancelTimestamps,
    replacement_price: float,
    replacement_qty: float,
    replacement_displayed_qty_at_accept: float,
    replacement_order_timestamps: OrderTimestamps,
    replacement_trades: list[ConfirmedTradeEvent],
) -> CancelReplaceSimulationResult:
    """Simulate confirmed cancel followed by a fresh passive replacement."""

    if (
        replacement_order_timestamps.order_send_ts_ms
        + _EPS
        < cancel_timestamps.cancel_ack_receive_ts_ms
    ):
        raise ValueError(
            "replacement send precedes client receipt of cancel acknowledgement"
        )

    old_result = simulate_passive_order(
        order_qty=old_order_qty,
        order_price=old_order_price,
        displayed_qty_at_accept=old_displayed_qty_at_accept,
        order_timestamps=old_order_timestamps,
        trades=old_trades,
        cancel_timestamps=cancel_timestamps,
    )

    if old_result.final_status != OrderStatus.CANCELLED:
        raise ValueError(
            "old order was not cancelled; replacement is not allowed"
        )

    # Reconstruct only the confirmed terminal state needed by the existing
    # cancel/replace contract.  This does not recreate queue history.
    old_terminal_state = OrderStateMachine(
        order_qty=old_result.order_qty,
        status=OrderStatus.CANCELLED,
        filled_qty=old_result.filled_qty,
        last_event_ts_ms=cancel_timestamps.cancel_effective_ts_ms,
    )

    replacement = create_replacement_after_cancel(
        old_order=old_terminal_state,
        new_price=replacement_price,
        new_qty=replacement_qty,
        displayed_qty_at_arrival=replacement_displayed_qty_at_accept,
    )

    # Explicit invariants: replacement is fresh and does not inherit state.
    if replacement.order.status != OrderStatus.NEW:
        raise AssertionError(
            "replacement must start NEW"
        )

    if replacement.order.filled_qty != 0:
        raise AssertionError(
            "replacement must start with zero fills"
        )

    if (
        abs(
            replacement.queue.queue_ahead_qty
            - float(replacement_displayed_qty_at_accept)
        )
        > _EPS
    ):
        raise AssertionError(
            "replacement must receive fresh queue priority"
        )

    replacement_result = simulate_passive_order(
        order_qty=replacement.order.order_qty,
        order_price=replacement.price,
        displayed_qty_at_accept=replacement.queue.queue_ahead_qty,
        order_timestamps=replacement_order_timestamps,
        trades=replacement_trades,
    )

    return CancelReplaceSimulationResult(
        old_order=old_result,
        replacement_order=replacement_result,
    )
