import pytest

from std0_quant.execution.execution_timestamps import (
    CancelTimestamps,
    OrderTimestamps,
)
from std0_quant.execution.order_state import (
    OrderStatus,
)
from std0_quant.execution.simulator import (
    ConfirmedTradeEvent,
)
from std0_quant.execution.replacement_simulator import (
    simulate_cancel_then_replace,
)


def old_times():
    return OrderTimestamps(
        order_send_ts_ms=1000,
        order_venue_arrival_ts_ms=1001,
        order_venue_accept_ts_ms=1002,
        order_ack_receive_ts_ms=1004,
    )


def cancel_times():
    return CancelTimestamps(
        cancel_send_ts_ms=1005,
        cancel_venue_arrival_ts_ms=1006,
        cancel_effective_ts_ms=1007,
        cancel_ack_receive_ts_ms=1010,
    )


def replacement_times():
    return OrderTimestamps(
        order_send_ts_ms=1010,
        order_venue_arrival_ts_ms=1011,
        order_venue_accept_ts_ms=1012,
        order_ack_receive_ts_ms=1014,
    )


def test_partial_old_fill_then_cancel_then_replacement_fill():
    result = simulate_cancel_then_replace(
        old_order_qty=5,
        old_order_price=0.55,
        old_displayed_qty_at_accept=0,
        old_order_timestamps=old_times(),
        old_trades=[
            ConfirmedTradeEvent(
                venue_ts_ms=1006,
                traded_qty=2,
            ),
        ],
        cancel_timestamps=cancel_times(),
        replacement_price=0.56,
        replacement_qty=3,
        replacement_displayed_qty_at_accept=0,
        replacement_order_timestamps=replacement_times(),
        replacement_trades=[
            ConfirmedTradeEvent(
                venue_ts_ms=1013,
                traded_qty=3,
            ),
        ],
    )

    assert result.old_order.final_status == OrderStatus.CANCELLED
    assert result.old_order.filled_qty == pytest.approx(2)
    assert result.old_order.remaining_qty == pytest.approx(3)

    assert (
        result.replacement_order.final_status
        == OrderStatus.FILLED
    )
    assert result.replacement_order.filled_qty == pytest.approx(3)


def test_replacement_send_before_cancel_ack_fails_closed():
    early_replacement = OrderTimestamps(
        order_send_ts_ms=1009,
        order_venue_arrival_ts_ms=1010,
        order_venue_accept_ts_ms=1011,
        order_ack_receive_ts_ms=1013,
    )

    with pytest.raises(ValueError):
        simulate_cancel_then_replace(
            old_order_qty=5,
            old_order_price=0.55,
            old_displayed_qty_at_accept=0,
            old_order_timestamps=old_times(),
            old_trades=[],
            cancel_timestamps=cancel_times(),
            replacement_price=0.56,
            replacement_qty=3,
            replacement_displayed_qty_at_accept=0,
            replacement_order_timestamps=early_replacement,
            replacement_trades=[],
        )


def test_no_replacement_if_old_order_fills_before_cancel():
    with pytest.raises(ValueError):
        simulate_cancel_then_replace(
            old_order_qty=5,
            old_order_price=0.55,
            old_displayed_qty_at_accept=0,
            old_order_timestamps=old_times(),
            old_trades=[
                ConfirmedTradeEvent(
                    venue_ts_ms=1006,
                    traded_qty=5,
                ),
            ],
            cancel_timestamps=cancel_times(),
            replacement_price=0.56,
            replacement_qty=3,
            replacement_displayed_qty_at_accept=0,
            replacement_order_timestamps=replacement_times(),
            replacement_trades=[],
        )


def test_replacement_gets_fresh_queue_priority():
    result = simulate_cancel_then_replace(
        old_order_qty=5,
        old_order_price=0.55,
        old_displayed_qty_at_accept=20,
        old_order_timestamps=old_times(),
        old_trades=[],
        cancel_timestamps=cancel_times(),
        replacement_price=0.56,
        replacement_qty=3,
        replacement_displayed_qty_at_accept=7,
        replacement_order_timestamps=replacement_times(),
        replacement_trades=[
            ConfirmedTradeEvent(
                venue_ts_ms=1013,
                traded_qty=4,
            ),
        ],
    )

    assert result.old_order.final_status == OrderStatus.CANCELLED

    # New order starts behind 7 displayed units.
    # A trade of 4 only reduces the new queue to 3.
    assert (
        result.replacement_order.queue_ahead_qty
        == pytest.approx(3)
    )
    assert result.replacement_order.filled_qty == pytest.approx(0)


def test_replacement_quantity_is_explicit_not_old_remaining():
    result = simulate_cancel_then_replace(
        old_order_qty=5,
        old_order_price=0.55,
        old_displayed_qty_at_accept=0,
        old_order_timestamps=old_times(),
        old_trades=[
            ConfirmedTradeEvent(
                venue_ts_ms=1006,
                traded_qty=2,
            ),
        ],
        cancel_timestamps=cancel_times(),
        replacement_price=0.56,
        replacement_qty=1,
        replacement_displayed_qty_at_accept=0,
        replacement_order_timestamps=replacement_times(),
        replacement_trades=[
            ConfirmedTradeEvent(
                venue_ts_ms=1013,
                traded_qty=1,
            ),
        ],
    )

    assert result.old_order.remaining_qty == pytest.approx(3)
    assert result.replacement_order.order_qty == pytest.approx(1)
    assert result.replacement_order.filled_qty == pytest.approx(1)


def test_replacement_event_log_uses_new_timeline():
    result = simulate_cancel_then_replace(
        old_order_qty=5,
        old_order_price=0.55,
        old_displayed_qty_at_accept=0,
        old_order_timestamps=old_times(),
        old_trades=[],
        cancel_timestamps=cancel_times(),
        replacement_price=0.56,
        replacement_qty=2,
        replacement_displayed_qty_at_accept=0,
        replacement_order_timestamps=replacement_times(),
        replacement_trades=[],
    )

    replacement_events = result.replacement_order.events

    assert replacement_events[0].event_ts_ms == pytest.approx(1010)
    assert replacement_events[1].event_ts_ms == pytest.approx(1012)
