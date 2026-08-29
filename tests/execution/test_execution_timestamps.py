import math

import pytest

from std0_quant.execution.execution_timestamps import (
    CancelTimestamps,
    FillTimestamps,
    OrderTimestamps,
    VenueRaceOrdering,
    compare_fill_vs_cancel,
)


def test_order_timestamp_chain():
    t = OrderTimestamps(
        order_send_ts_ms=1000,
        order_venue_arrival_ts_ms=1002,
        order_venue_accept_ts_ms=1003,
        order_ack_receive_ts_ms=1006,
    )

    assert t.order_send_ts_ms == 1000
    assert t.order_venue_accept_ts_ms == 1003
    assert t.order_ack_receive_ts_ms == 1006


def test_cancel_timestamp_chain():
    t = CancelTimestamps(
        cancel_send_ts_ms=2000,
        cancel_venue_arrival_ts_ms=2002,
        cancel_effective_ts_ms=2003,
        cancel_ack_receive_ts_ms=2008,
    )

    assert t.cancel_effective_ts_ms == 2003
    assert t.cancel_ack_receive_ts_ms == 2008


def test_fill_timestamp_chain():
    t = FillTimestamps(
        fill_venue_ts_ms=3000,
        fill_receive_ts_ms=3005,
    )

    assert t.fill_venue_ts_ms == 3000
    assert t.fill_receive_ts_ms == 3005


def test_fill_before_cancel_effective():
    result = compare_fill_vs_cancel(
        fill_venue_ts_ms=1003,
        cancel_effective_ts_ms=1004,
    )

    assert (
        result
        == VenueRaceOrdering.FILL_BEFORE_CANCEL
    )


def test_cancel_effective_before_fill():
    result = compare_fill_vs_cancel(
        fill_venue_ts_ms=1005,
        cancel_effective_ts_ms=1003,
    )

    assert (
        result
        == VenueRaceOrdering.CANCEL_BEFORE_FILL
    )


def test_equal_venue_timestamps_are_ambiguous():
    result = compare_fill_vs_cancel(
        fill_venue_ts_ms=1003,
        cancel_effective_ts_ms=1003,
    )

    assert (
        result
        == VenueRaceOrdering.AMBIGUOUS_SAME_TIMESTAMP
    )


def test_cancel_effective_time_not_client_ack_time_controls_race():
    cancel = CancelTimestamps(
        cancel_send_ts_ms=1000,
        cancel_venue_arrival_ts_ms=1002,
        cancel_effective_ts_ms=1003,
        cancel_ack_receive_ts_ms=1010,
    )

    fill = FillTimestamps(
        fill_venue_ts_ms=1005,
        fill_receive_ts_ms=1008,
    )

    # Fill happens before the client receives cancel ACK,
    # but after cancellation already became effective at venue.
    assert fill.fill_venue_ts_ms < cancel.cancel_ack_receive_ts_ms

    result = compare_fill_vs_cancel(
        fill_venue_ts_ms=fill.fill_venue_ts_ms,
        cancel_effective_ts_ms=cancel.cancel_effective_ts_ms,
    )

    assert (
        result
        == VenueRaceOrdering.CANCEL_BEFORE_FILL
    )


def test_order_ack_receive_need_not_precede_later_fill_receive():
    order = OrderTimestamps(
        order_send_ts_ms=1000,
        order_venue_arrival_ts_ms=1001,
        order_venue_accept_ts_ms=1002,
        order_ack_receive_ts_ms=1010,
    )

    fill = FillTimestamps(
        fill_venue_ts_ms=1003,
        fill_receive_ts_ms=1006,
    )

    # Venue accepted the order before the fill.
    assert (
        order.order_venue_accept_ts_ms
        < fill.fill_venue_ts_ms
    )

    # The fill report can reach us before the separate order ACK.
    assert (
        fill.fill_receive_ts_ms
        < order.order_ack_receive_ts_ms
    )


@pytest.mark.parametrize(
    "factory,kwargs",
    [
        (
            OrderTimestamps,
            {
                "order_send_ts_ms": 1000,
                "order_venue_arrival_ts_ms": 999,
                "order_venue_accept_ts_ms": 1001,
                "order_ack_receive_ts_ms": 1002,
            },
        ),
        (
            CancelTimestamps,
            {
                "cancel_send_ts_ms": 1000,
                "cancel_venue_arrival_ts_ms": 1001,
                "cancel_effective_ts_ms": 1003,
                "cancel_ack_receive_ts_ms": 1002,
            },
        ),
        (
            FillTimestamps,
            {
                "fill_venue_ts_ms": 1001,
                "fill_receive_ts_ms": 1000,
            },
        ),
    ],
)
def test_non_monotonic_chains_fail_closed(
    factory,
    kwargs,
):
    with pytest.raises(ValueError):
        factory(**kwargs)


@pytest.mark.parametrize(
    "bad",
    [
        -1,
        math.nan,
        math.inf,
        -math.inf,
    ],
)
def test_invalid_timestamp_fails_closed(bad):
    with pytest.raises(ValueError):
        compare_fill_vs_cancel(
            fill_venue_ts_ms=bad,
            cancel_effective_ts_ms=1000,
        )
