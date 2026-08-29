import pytest

from std0_quant.execution.contracts import (
    ORDER_EVENT_SCHEMA_V1,
    ORDER_INTENT_SCHEMA_V1,
    OrderEvent,
    OrderEventType,
    OrderIntent,
    OrderSide,
    TimeInForce,
)


def intent():
    return OrderIntent(
        intent_id="intent-1",
        condition_id="m1",
        outcome="Up",
        side="BUY",
        qty=10,
        limit_price=0.50,
        time_in_force="GTC",
        decision_ts_ms=1001,
        market_data_ts_ms=1000,
        strategy_id="std0_candidate",
        strategy_version="v1",
        risk_policy_version="risk_v1",
    )


def test_order_intent_is_normalized_and_versioned():
    row = intent()

    assert row.side == OrderSide.BUY
    assert row.time_in_force == TimeInForce.GTC
    assert row.schema_version == ORDER_INTENT_SCHEMA_V1
    assert row.order_notional == pytest.approx(5)


def test_order_intent_json_roundtrip_is_exact():
    original = intent()
    recovered = OrderIntent.from_json(
        original.to_json()
    )

    assert recovered == original


def test_market_data_cannot_come_from_future():
    with pytest.raises(ValueError):
        OrderIntent(
            intent_id="i",
            condition_id="m1",
            outcome="Up",
            side="BUY",
            qty=1,
            limit_price=0.5,
            time_in_force="GTC",
            decision_ts_ms=1000,
            market_data_ts_ms=1001,
            strategy_id="s",
            strategy_version="v1",
            risk_policy_version="r1",
        )


def test_invalid_intent_schema_fails_closed():
    row = intent().to_dict()
    row["schema_version"] = "unknown"

    with pytest.raises(ValueError):
        OrderIntent.from_dict(row)


def test_partial_fill_event_contract():
    event = OrderEvent(
        event_id="e1",
        intent_id="intent-1",
        event_type="PARTIAL_FILL",
        venue_ts_ms=1003,
        receive_ts_ms=1005,
        venue_order_id="venue-1",
        fill_qty=2,
        fill_price=0.50,
        cumulative_filled_qty=2,
        remaining_qty=8,
    )

    assert event.event_type == OrderEventType.PARTIAL_FILL
    assert event.schema_version == ORDER_EVENT_SCHEMA_V1


def test_filled_event_requires_zero_remaining():
    with pytest.raises(ValueError):
        OrderEvent(
            event_id="e1",
            intent_id="intent-1",
            event_type="FILLED",
            venue_ts_ms=1003,
            receive_ts_ms=1005,
            fill_qty=10,
            fill_price=0.50,
            cumulative_filled_qty=10,
            remaining_qty=1,
        )


def test_non_fill_event_cannot_smuggle_fill():
    with pytest.raises(ValueError):
        OrderEvent(
            event_id="e1",
            intent_id="intent-1",
            event_type="VENUE_ACK",
            receive_ts_ms=1005,
            fill_qty=1,
            fill_price=0.50,
        )


def test_order_event_json_roundtrip():
    original = OrderEvent(
        event_id="e1",
        intent_id="intent-1",
        event_type="FILLED",
        venue_ts_ms=1003,
        receive_ts_ms=1005,
        venue_order_id="venue-1",
        fill_qty=10,
        fill_price=0.50,
        cumulative_filled_qty=10,
        remaining_qty=0,
    )

    assert (
        OrderEvent.from_json(original.to_json())
        == original
    )


@pytest.mark.parametrize(
    "tif",
    [
        "INVALID",
        "",
    ],
)
def test_invalid_time_in_force_fails_closed(tif):
    with pytest.raises(ValueError):
        OrderIntent(
            intent_id="i",
            condition_id="m1",
            outcome="Up",
            side="BUY",
            qty=1,
            limit_price=0.50,
            time_in_force=tif,
            decision_ts_ms=1001,
            market_data_ts_ms=1000,
            strategy_id="s",
            strategy_version="v1",
            risk_policy_version="r1",
        )


def test_venue_and_receive_timestamps_are_kept_separate():
    event = OrderEvent(
        event_id="e1",
        intent_id="intent-1",
        event_type="VENUE_ACK",
        venue_ts_ms=1002,
        receive_ts_ms=1010,
    )

    assert event.venue_ts_ms == pytest.approx(1002)
    assert event.receive_ts_ms == pytest.approx(1010)
