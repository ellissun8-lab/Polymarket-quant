import pytest

from std0_quant.execution.cancel_fill_race import (
    AmbiguousCancelFillRace,
)
from std0_quant.execution.execution_timestamps import (
    CancelTimestamps,
    OrderTimestamps,
)
from std0_quant.execution.latency_model import (
    FixedLatencyModel,
    LatencyProfile,
)
from std0_quant.execution.order_state import (
    OrderStatus,
)
from std0_quant.execution.simulator import (
    ConfirmedTradeEvent,
    cancel_timestamps_from_latency,
    order_timestamps_from_latency,
    simulate_passive_order,
)


def order_times():
    return OrderTimestamps(
        order_send_ts_ms=1000,
        order_venue_arrival_ts_ms=1001,
        order_venue_accept_ts_ms=1002,
        order_ack_receive_ts_ms=1010,
    )


def test_event_driven_queue_to_partial_to_full_fill():
    result = simulate_passive_order(
        order_qty=5,
        order_price=0.55,
        displayed_qty_at_accept=10,
        order_timestamps=order_times(),
        trades=[
            ConfirmedTradeEvent(
                venue_ts_ms=1003,
                traded_qty=8,
            ),
            ConfirmedTradeEvent(
                venue_ts_ms=1004,
                traded_qty=4,
            ),
            ConfirmedTradeEvent(
                venue_ts_ms=1005,
                traded_qty=3,
            ),
        ],
    )

    assert result.final_status == OrderStatus.FILLED
    assert result.filled_qty == pytest.approx(5)
    assert result.remaining_qty == pytest.approx(0)
    assert result.queue_ahead_qty == pytest.approx(0)
    assert result.average_fill_price == pytest.approx(0.55)


def test_fill_can_happen_before_client_receives_order_ack():
    result = simulate_passive_order(
        order_qty=2,
        order_price=0.55,
        displayed_qty_at_accept=0,
        order_timestamps=order_times(),
        trades=[
            ConfirmedTradeEvent(
                venue_ts_ms=1003,
                traded_qty=2,
            ),
        ],
    )

    assert 1003 < result.order_timestamps.order_ack_receive_ts_ms
    assert result.final_status == OrderStatus.FILLED


def test_cancel_effective_before_trade_prevents_fill():
    cancel = CancelTimestamps(
        cancel_send_ts_ms=1003,
        cancel_venue_arrival_ts_ms=1004,
        cancel_effective_ts_ms=1005,
        cancel_ack_receive_ts_ms=1010,
    )

    result = simulate_passive_order(
        order_qty=5,
        order_price=0.55,
        displayed_qty_at_accept=0,
        order_timestamps=order_times(),
        trades=[
            ConfirmedTradeEvent(
                venue_ts_ms=1006,
                traded_qty=5,
            ),
        ],
        cancel_timestamps=cancel,
    )

    assert result.final_status == OrderStatus.CANCELLED
    assert result.filled_qty == pytest.approx(0)
    assert result.remaining_qty == pytest.approx(5)


def test_trade_before_cancel_effective_fills_then_cancels_remainder():
    cancel = CancelTimestamps(
        cancel_send_ts_ms=1003,
        cancel_venue_arrival_ts_ms=1004,
        cancel_effective_ts_ms=1006,
        cancel_ack_receive_ts_ms=1010,
    )

    result = simulate_passive_order(
        order_qty=5,
        order_price=0.55,
        displayed_qty_at_accept=0,
        order_timestamps=order_times(),
        trades=[
            ConfirmedTradeEvent(
                venue_ts_ms=1005,
                traded_qty=2,
            ),
        ],
        cancel_timestamps=cancel,
    )

    assert result.final_status == OrderStatus.CANCELLED
    assert result.filled_qty == pytest.approx(2)
    assert result.remaining_qty == pytest.approx(3)


def test_trade_can_complete_before_cancel_effective():
    cancel = CancelTimestamps(
        cancel_send_ts_ms=1003,
        cancel_venue_arrival_ts_ms=1004,
        cancel_effective_ts_ms=1006,
        cancel_ack_receive_ts_ms=1010,
    )

    result = simulate_passive_order(
        order_qty=5,
        order_price=0.55,
        displayed_qty_at_accept=0,
        order_timestamps=order_times(),
        trades=[
            ConfirmedTradeEvent(
                venue_ts_ms=1005,
                traded_qty=5,
            ),
        ],
        cancel_timestamps=cancel,
    )

    assert result.final_status == OrderStatus.FILLED
    assert result.filled_qty == pytest.approx(5)


def test_trade_equal_cancel_effective_fails_closed():
    cancel = CancelTimestamps(
        cancel_send_ts_ms=1003,
        cancel_venue_arrival_ts_ms=1004,
        cancel_effective_ts_ms=1005,
        cancel_ack_receive_ts_ms=1010,
    )

    with pytest.raises(AmbiguousCancelFillRace):
        simulate_passive_order(
            order_qty=5,
            order_price=0.55,
            displayed_qty_at_accept=0,
            order_timestamps=order_times(),
            trades=[
                ConfirmedTradeEvent(
                    venue_ts_ms=1005,
                    traded_qty=5,
                ),
            ],
            cancel_timestamps=cancel,
        )


def test_trade_equal_order_acceptance_fails_closed():
    with pytest.raises(ValueError):
        simulate_passive_order(
            order_qty=5,
            order_price=0.55,
            displayed_qty_at_accept=0,
            order_timestamps=order_times(),
            trades=[
                ConfirmedTradeEvent(
                    venue_ts_ms=1002,
                    traded_qty=5,
                ),
            ],
        )


def test_trade_before_order_acceptance_fails_closed():
    with pytest.raises(ValueError):
        simulate_passive_order(
            order_qty=5,
            order_price=0.55,
            displayed_qty_at_accept=0,
            order_timestamps=order_times(),
            trades=[
                ConfirmedTradeEvent(
                    venue_ts_ms=1001,
                    traded_qty=5,
                ),
            ],
        )


def test_latency_model_builds_order_execution_timestamps():
    latency = FixedLatencyModel(
        LatencyProfile(
            market_data_ms=2,
            feature_compute_ms=3,
            decision_compute_ms=4,
            order_send_ms=1,
            outbound_network_ms=5,
            venue_processing_ms=2,
            ack_network_ms=6,
        )
    )

    t = order_timestamps_from_latency(
        market_event_ts_ms=1000,
        latency=latency,
    )

    assert t.order_send_ts_ms == pytest.approx(1010)
    assert t.order_venue_arrival_ts_ms == pytest.approx(1015)
    assert t.order_venue_accept_ts_ms == pytest.approx(1017)
    assert t.order_ack_receive_ts_ms == pytest.approx(1023)


def test_latency_model_builds_cancel_execution_timestamps():
    latency = FixedLatencyModel(
        LatencyProfile(
            market_data_ms=0,
            feature_compute_ms=0,
            decision_compute_ms=0,
            order_send_ms=0,
            outbound_network_ms=5,
            venue_processing_ms=2,
            ack_network_ms=6,
        )
    )

    t = cancel_timestamps_from_latency(
        cancel_send_ts_ms=2000,
        latency=latency,
    )

    assert t.cancel_venue_arrival_ts_ms == pytest.approx(2005)
    assert t.cancel_effective_ts_ms == pytest.approx(2007)
    assert t.cancel_ack_receive_ts_ms == pytest.approx(2013)


def test_unexplained_book_cancel_is_not_used_by_simulator():
    result = simulate_passive_order(
        order_qty=5,
        order_price=0.55,
        displayed_qty_at_accept=10,
        order_timestamps=order_times(),
        trades=[
            ConfirmedTradeEvent(
                venue_ts_ms=1003,
                traded_qty=6,
            ),
        ],
    )

    # Only confirmed trade depletes queue.
    assert result.queue_ahead_qty == pytest.approx(4)
    assert result.filled_qty == pytest.approx(0)


def test_event_log_is_monotonic():
    result = simulate_passive_order(
        order_qty=5,
        order_price=0.55,
        displayed_qty_at_accept=1,
        order_timestamps=order_times(),
        trades=[
            ConfirmedTradeEvent(
                venue_ts_ms=1004,
                traded_qty=2,
            ),
            ConfirmedTradeEvent(
                venue_ts_ms=1005,
                traded_qty=2,
            ),
        ],
    )

    timestamps = [
        event.event_ts_ms
        for event in result.events
    ]

    assert timestamps == sorted(timestamps)
