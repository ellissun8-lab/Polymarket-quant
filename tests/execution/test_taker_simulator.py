import pytest

from std0_quant.execution.execution_timestamps import (
    OrderTimestamps,
)
from std0_quant.execution.order_state import (
    OrderStatus,
)
from std0_quant.execution.taker_simulator import (
    AggressiveTIF,
    simulate_aggressive_order,
)


def order_times():
    return OrderTimestamps(
        order_send_ts_ms=1000,
        order_venue_arrival_ts_ms=1001,
        order_venue_accept_ts_ms=1002,
        order_ack_receive_ts_ms=1010,
    )


def test_ioc_full_fill():
    result = simulate_aggressive_order(
        side="BUY",
        tif="IOC",
        order_qty=5,
        levels=[
            (0.50, 2),
            (0.51, 3),
        ],
        order_timestamps=order_times(),
    )

    assert result.final_status == OrderStatus.FILLED
    assert result.filled_qty == pytest.approx(5)
    assert result.remaining_qty == pytest.approx(0)
    assert result.tif == AggressiveTIF.IOC


def test_ioc_partial_fill_expires_remainder():
    result = simulate_aggressive_order(
        side="BUY",
        tif="IOC",
        order_qty=10,
        levels=[
            (0.50, 2),
            (0.51, 3),
        ],
        order_timestamps=order_times(),
    )

    assert result.final_status == OrderStatus.EXPIRED
    assert result.filled_qty == pytest.approx(5)
    assert result.remaining_qty == pytest.approx(5)
    assert result.average_fill_price == pytest.approx(
        (0.50 * 2 + 0.51 * 3) / 5
    )


def test_fok_full_fill():
    result = simulate_aggressive_order(
        side="SELL",
        tif="FOK",
        order_qty=4,
        levels=[
            (0.60, 1),
            (0.59, 3),
        ],
        order_timestamps=order_times(),
    )

    assert result.final_status == OrderStatus.FILLED
    assert result.filled_qty == pytest.approx(4)
    assert result.remaining_qty == pytest.approx(0)


def test_fok_insufficient_liquidity_has_zero_fill():
    result = simulate_aggressive_order(
        side="BUY",
        tif="FOK",
        order_qty=10,
        levels=[
            (0.50, 2),
            (0.51, 3),
        ],
        order_timestamps=order_times(),
    )

    assert result.final_status == OrderStatus.EXPIRED
    assert result.filled_qty == pytest.approx(0)
    assert result.remaining_qty == pytest.approx(10)
    assert result.fills == ()


def test_fills_happen_at_venue_accept_before_client_ack():
    result = simulate_aggressive_order(
        side="BUY",
        tif="IOC",
        order_qty=1,
        levels=[
            (0.50, 1),
        ],
        order_timestamps=order_times(),
    )

    fill_events = [
        event
        for event in result.events
        if event.event_type == "TAKER_FILL"
    ]

    assert fill_events
    assert fill_events[0].event_ts_ms == pytest.approx(1002)
    assert (
        fill_events[0].event_ts_ms
        < result.order_timestamps.order_ack_receive_ts_ms
    )


def test_ioc_zero_visible_liquidity_expires_unfilled():
    result = simulate_aggressive_order(
        side="BUY",
        tif="IOC",
        order_qty=2,
        levels=[
            (0.50, 0),
        ],
        order_timestamps=order_times(),
    )

    assert result.final_status == OrderStatus.EXPIRED
    assert result.filled_qty == pytest.approx(0)
    assert result.remaining_qty == pytest.approx(2)


def test_invalid_tif_fails_closed():
    with pytest.raises(ValueError):
        simulate_aggressive_order(
            side="BUY",
            tif="GTC",
            order_qty=1,
            levels=[(0.50, 1)],
            order_timestamps=order_times(),
        )


def test_bad_book_ordering_still_fails_closed():
    with pytest.raises(ValueError):
        simulate_aggressive_order(
            side="BUY",
            tif="IOC",
            order_qty=1,
            levels=[
                (0.51, 1),
                (0.50, 1),
            ],
            order_timestamps=order_times(),
        )


def test_expired_is_terminal():
    result = simulate_aggressive_order(
        side="BUY",
        tif="IOC",
        order_qty=2,
        levels=[],
        order_timestamps=order_times(),
    )

    assert result.final_status == OrderStatus.EXPIRED


def test_event_log_is_monotonic():
    result = simulate_aggressive_order(
        side="BUY",
        tif="IOC",
        order_qty=5,
        levels=[
            (0.50, 2),
            (0.51, 1),
        ],
        order_timestamps=order_times(),
    )

    timestamps = [
        event.event_ts_ms
        for event in result.events
    ]

    assert timestamps == sorted(timestamps)
