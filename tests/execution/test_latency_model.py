import math

import pytest

from std0_quant.execution.latency_model import (
    FixedLatencyModel,
    LatencyProfile,
    LatencyTimeline,
)


def test_fixed_latency_timeline_exact_stage_order():
    profile = LatencyProfile(
        market_data_ms=2,
        feature_compute_ms=3,
        decision_compute_ms=4,
        order_send_ms=1,
        outbound_network_ms=5,
        venue_processing_ms=2,
        ack_network_ms=6,
    )
    model = FixedLatencyModel(profile)

    t = model.timeline(1000)

    assert t.market_event_ts_ms == pytest.approx(1000)
    assert t.receive_ts_ms == pytest.approx(1002)
    assert t.feature_ready_ts_ms == pytest.approx(1005)
    assert t.decision_ts_ms == pytest.approx(1009)
    assert t.order_send_ts_ms == pytest.approx(1010)
    assert t.venue_arrival_ts_ms == pytest.approx(1015)
    assert t.venue_ack_ts_ms == pytest.approx(1023)


def test_profile_total_latency_matches_timeline():
    profile = LatencyProfile(
        market_data_ms=1.5,
        feature_compute_ms=0.5,
        decision_compute_ms=2,
        order_send_ms=1,
        outbound_network_ms=7,
        venue_processing_ms=3,
        ack_network_ms=8,
    )
    model = FixedLatencyModel(profile)
    t = model.timeline(5000)

    assert profile.event_to_arrival_ms == pytest.approx(
        12
    )
    assert profile.event_to_ack_ms == pytest.approx(
        23
    )
    assert t.event_to_arrival_ms == pytest.approx(
        profile.event_to_arrival_ms
    )
    assert t.event_to_ack_ms == pytest.approx(
        profile.event_to_ack_ms
    )


def test_zero_latency_profile_is_valid():
    profile = LatencyProfile(
        market_data_ms=0,
        feature_compute_ms=0,
        decision_compute_ms=0,
        order_send_ms=0,
        outbound_network_ms=0,
    )
    t = FixedLatencyModel(profile).timeline(100)

    assert t.market_event_ts_ms == pytest.approx(100)
    assert t.venue_arrival_ts_ms == pytest.approx(100)
    assert t.venue_ack_ts_ms == pytest.approx(100)


def test_outbound_arrival_helper():
    profile = LatencyProfile(
        market_data_ms=0,
        feature_compute_ms=0,
        decision_compute_ms=0,
        order_send_ms=0,
        outbound_network_ms=7.5,
    )
    model = FixedLatencyModel(profile)

    assert model.venue_arrival_from_send(
        1000
    ) == pytest.approx(1007.5)


def test_ack_helper():
    profile = LatencyProfile(
        market_data_ms=0,
        feature_compute_ms=0,
        decision_compute_ms=0,
        order_send_ms=0,
        outbound_network_ms=1,
        venue_processing_ms=2,
        ack_network_ms=3,
    )
    model = FixedLatencyModel(profile)

    assert model.venue_ack_from_arrival(
        1000
    ) == pytest.approx(1005)


@pytest.mark.parametrize(
    "field",
    [
        "market_data_ms",
        "feature_compute_ms",
        "decision_compute_ms",
        "order_send_ms",
        "outbound_network_ms",
        "venue_processing_ms",
        "ack_network_ms",
    ],
)
def test_negative_profile_component_fails_closed(field):
    values = {
        "market_data_ms": 0,
        "feature_compute_ms": 0,
        "decision_compute_ms": 0,
        "order_send_ms": 0,
        "outbound_network_ms": 0,
        "venue_processing_ms": 0,
        "ack_network_ms": 0,
    }
    values[field] = -1

    with pytest.raises(ValueError):
        LatencyProfile(**values)


@pytest.mark.parametrize(
    "bad",
    [
        math.nan,
        math.inf,
        -math.inf,
    ],
)
def test_nonfinite_profile_component_fails_closed(bad):
    with pytest.raises(ValueError):
        LatencyProfile(
            market_data_ms=bad,
            feature_compute_ms=0,
            decision_compute_ms=0,
            order_send_ms=0,
            outbound_network_ms=0,
        )


def test_non_monotonic_manual_timeline_fails_closed():
    with pytest.raises(ValueError):
        LatencyTimeline(
            market_event_ts_ms=1000,
            receive_ts_ms=1002,
            feature_ready_ts_ms=1001,
            decision_ts_ms=1003,
            order_send_ts_ms=1004,
            venue_arrival_ts_ms=1005,
            venue_ack_ts_ms=1006,
        )


def test_negative_event_timestamp_fails_closed():
    profile = LatencyProfile(
        market_data_ms=1,
        feature_compute_ms=1,
        decision_compute_ms=1,
        order_send_ms=1,
        outbound_network_ms=1,
    )

    with pytest.raises(ValueError):
        FixedLatencyModel(profile).timeline(-1)
