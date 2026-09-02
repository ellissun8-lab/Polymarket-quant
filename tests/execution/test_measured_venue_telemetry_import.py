import hashlib
import json

import pytest

from std0_quant.execution.contracts import (
    ORDER_EVENT_SCHEMA_V1,
    ORDER_INTENT_SCHEMA_V1,
)
from std0_quant.execution.measured_venue_execution import (
    MeasuredVenueExecutionObservation,
    MeasuredVenueExecutionSource,
)
from std0_quant.execution.measured_venue_telemetry_import import (
    MEASURED_VENUE_TELEMETRY_IMPORTER_V1,
    MEASURED_VENUE_TELEMETRY_JSONL_V1,
    MeasuredVenueTelemetryImport,
    MeasuredVenueTelemetryImportError,
    import_measured_venue_telemetry_jsonl,
)


def intent(**changes):
    row = {
        "intent_id": "intent-1",
        "condition_id": "condition-1",
        "outcome": "YES",
        "side": "BUY",
        "qty": 10.0,
        "limit_price": 0.42,
        "time_in_force": "GTC",
        "decision_ts_ms": 1000.0,
        "market_data_ts_ms": 999.0,
        "strategy_id": "alpha-a",
        "strategy_version": "1",
        "risk_policy_version": "risk-v1",
        "schema_version": ORDER_INTENT_SCHEMA_V1,
    }
    row.update(changes)
    return row


def event(**changes):
    row = {
        "event_id": "event-1",
        "intent_id": "intent-1",
        "event_type": "VENUE_ACK",
        "receive_ts_ms": 1002.0,
        "venue_ts_ms": 1001.0,
        "venue_order_id": "venue-order-1",
        "fill_qty": 0.0,
        "fill_price": None,
        "cumulative_filled_qty": 0.0,
        "remaining_qty": 10.0,
        "reason": None,
        "schema_version": ORDER_EVENT_SCHEMA_V1,
    }
    row.update(changes)
    return row


def record(**changes):
    row = {
        "schema_version": MEASURED_VENUE_TELEMETRY_JSONL_V1,
        "intent": intent(),
        "events": [event()],
    }
    row.update(changes)
    return row


def raw(*rows):
    return (
        "\n".join(
            json.dumps(
                row,
                sort_keys=True,
                separators=(",", ":"),
            )
            for row in rows
        )
        + "\n"
    ).encode("utf-8")


def load(payload, **changes):
    values = {
        "source_id": "venue-export",
        "source_version": "1",
        "source_run_id": "source-run-1",
    }
    values.update(changes)
    return import_measured_venue_telemetry_jsonl(
        payload,
        **values,
    )


def test_contract_symbols():
    assert (
        MEASURED_VENUE_TELEMETRY_JSONL_V1
        == "measured_venue_telemetry_jsonl_v1"
    )
    assert (
        MEASURED_VENUE_TELEMETRY_IMPORTER_V1
        == "measured_venue_telemetry_importer_v1"
    )
    assert MeasuredVenueTelemetryImport is not None


def test_valid_import_binds_exact_raw_bytes():
    payload = raw(record())
    result = load(payload)

    assert isinstance(result, MeasuredVenueTelemetryImport)
    assert isinstance(result.source, MeasuredVenueExecutionSource)
    assert result.source.source_id == "venue-export"
    assert result.source.source_version == "1"
    assert result.source.source_run_id == "source-run-1"
    assert (
        result.source.source_artifact_hash
        == hashlib.sha256(payload).hexdigest()
    )
    assert len(result.observations) == 1
    assert isinstance(
        result.observations[0],
        MeasuredVenueExecutionObservation,
    )
    assert result.observations[0].intent.intent_id == "intent-1"
    assert result.observations[0].events[0].event_id == "event-1"


@pytest.mark.parametrize(
    "payload",
    (
        b"",
        b"\xff",
        b"{}\n\n{}\n",
        b"{not-json}\n",
        b"[]\n",
    ),
)
def test_malformed_raw_input_fails_closed(payload):
    with pytest.raises(MeasuredVenueTelemetryImportError):
        load(payload)


def test_raw_input_must_be_bytes():
    with pytest.raises(MeasuredVenueTelemetryImportError):
        load("{}\n")


def test_record_fields_are_exact():
    row = record()
    row["live"] = True

    with pytest.raises(MeasuredVenueTelemetryImportError):
        load(raw(row))


def test_record_schema_is_exact():
    with pytest.raises(MeasuredVenueTelemetryImportError):
        load(raw(record(schema_version="other")))


def test_intent_requires_explicit_complete_v1_fields():
    row = record()
    del row["intent"]["schema_version"]

    with pytest.raises(MeasuredVenueTelemetryImportError):
        load(raw(row))


def test_event_requires_explicit_complete_v1_fields():
    row = record()
    del row["events"][0]["schema_version"]

    with pytest.raises(MeasuredVenueTelemetryImportError):
        load(raw(row))


def test_events_must_be_nonempty():
    with pytest.raises(MeasuredVenueTelemetryImportError):
        load(raw(record(events=[])))


def test_event_intent_id_must_match_parent_intent():
    with pytest.raises(MeasuredVenueTelemetryImportError):
        load(
            raw(
                record(
                    events=[
                        event(intent_id="other-intent"),
                    ]
                )
            )
        )


def test_duplicate_intent_ids_fail_closed():
    with pytest.raises(MeasuredVenueTelemetryImportError):
        load(raw(record(), record()))


def test_duplicate_event_ids_fail_closed():
    second = record(
        intent=intent(intent_id="intent-2"),
        events=[
            event(
                intent_id="intent-2",
                venue_order_id="venue-order-2",
            ),
        ],
    )

    with pytest.raises(MeasuredVenueTelemetryImportError):
        load(raw(record(), second))


@pytest.mark.parametrize(
    "reason",
    ("SHADOW_SYNTHETIC_ACK", "SHADOW_ONLY"),
)
def test_known_shadow_markers_fail_closed(reason):
    with pytest.raises(MeasuredVenueTelemetryImportError):
        load(
            raw(
                record(
                    events=[
                        event(reason=reason),
                    ]
                )
            )
        )


def test_clodds_shadow_protocol_shape_is_rejected():
    payload = raw(
        {
            "protocol_version": "clodds_shadow_jsonl_v1",
            "mode": "SHADOW",
            "intent": intent(),
            "event": event(),
        }
    )

    with pytest.raises(MeasuredVenueTelemetryImportError):
        load(payload)


def test_record_order_is_preserved():
    second = record(
        intent=intent(intent_id="intent-2"),
        events=[
            event(
                event_id="event-2",
                intent_id="intent-2",
                venue_order_id="venue-order-2",
            ),
        ],
    )

    result = load(raw(record(), second))

    assert tuple(
        item.intent.intent_id
        for item in result.observations
    ) == ("intent-1", "intent-2")


@pytest.mark.parametrize(
    "field",
    ("source_id", "source_version", "source_run_id"),
)
def test_source_metadata_must_be_nonempty(field):
    payload = raw(record())

    with pytest.raises(MeasuredVenueTelemetryImportError):
        load(payload, **{field: ""})


def test_duplicate_top_level_json_key_fails_closed():
    payload = raw(record()).replace(
        b"\"schema_version\":\"measured_venue_telemetry_jsonl_v1\"",
        b"\"schema_version\":\"evil\",\"schema_version\":\"measured_venue_telemetry_jsonl_v1\"",
        1,
    )

    with pytest.raises(MeasuredVenueTelemetryImportError):
        load(payload)


def test_duplicate_nested_intent_json_key_fails_closed():
    payload = raw(record()).replace(
        b"\"strategy_id\":\"alpha-a\"",
        b"\"strategy_id\":\"evil\",\"strategy_id\":\"alpha-a\"",
        1,
    )

    with pytest.raises(MeasuredVenueTelemetryImportError):
        load(payload)


def test_duplicate_nested_event_json_key_fails_closed():
    payload = raw(record()).replace(
        b"\"event_id\":\"event-1\"",
        b"\"event_id\":\"evil\",\"event_id\":\"event-1\"",
        1,
    )

    with pytest.raises(MeasuredVenueTelemetryImportError):
        load(payload)


def test_numeric_string_in_intent_fails_closed():
    payload = raw(record()).replace(
        b"\"qty\":10.0",
        b"\"qty\":\"10.0\"",
        1,
    )

    with pytest.raises(MeasuredVenueTelemetryImportError):
        load(payload)


def test_boolean_numeric_event_field_fails_closed():
    payload = raw(record()).replace(
        b"\"receive_ts_ms\":1002.0",
        b"\"receive_ts_ms\":true",
        1,
    )

    with pytest.raises(MeasuredVenueTelemetryImportError):
        load(payload)
