# Offline measured-venue telemetry JSONL import adapter v1.
#
# This module only parses externally supplied raw bytes into deterministic
# measured-execution contracts. It does not contact a venue, hold
# credentials, submit orders, make validation decisions, or promote factors.

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from std0_quant.execution.contracts import (
    ORDER_EVENT_SCHEMA_V1,
    ORDER_INTENT_SCHEMA_V1,
    OrderEvent,
    OrderIntent,
)
from std0_quant.execution.measured_venue_execution import (
    MeasuredVenueExecutionObservation,
    MeasuredVenueExecutionSource,
)


MEASURED_VENUE_TELEMETRY_JSONL_V1 = (
    "measured_venue_telemetry_jsonl_v1"
)
MEASURED_VENUE_TELEMETRY_IMPORTER_V1 = (
    "measured_venue_telemetry_importer_v1"
)


class MeasuredVenueTelemetryImportError(ValueError):
    """Fail-closed measured telemetry import error."""


@dataclass(frozen=True)
class MeasuredVenueTelemetryImport:
    source: MeasuredVenueExecutionSource
    observations: tuple[
        MeasuredVenueExecutionObservation,
        ...,
    ]
    importer_version: str = MEASURED_VENUE_TELEMETRY_IMPORTER_V1

    def __post_init__(self) -> None:
        if not isinstance(
            self.source,
            MeasuredVenueExecutionSource,
        ):
            raise TypeError(
                "source must be MeasuredVenueExecutionSource"
            )

        observations = tuple(self.observations)
        if not observations:
            raise ValueError(
                "telemetry import requires observations"
            )

        for observation in observations:
            if not isinstance(
                observation,
                MeasuredVenueExecutionObservation,
            ):
                raise TypeError(
                    "observations must contain "
                    "MeasuredVenueExecutionObservation"
                )

        object.__setattr__(
            self,
            "observations",
            observations,
        )

        if (
            self.importer_version
            != MEASURED_VENUE_TELEMETRY_IMPORTER_V1
        ):
            raise ValueError(
                "unsupported importer_version"
            )


_RECORD_KEYS = {
    "schema_version",
    "intent",
    "events",
}

_INTENT_KEYS = {
    "intent_id",
    "condition_id",
    "outcome",
    "side",
    "qty",
    "limit_price",
    "time_in_force",
    "decision_ts_ms",
    "market_data_ts_ms",
    "strategy_id",
    "strategy_version",
    "risk_policy_version",
    "schema_version",
}

_EVENT_KEYS = {
    "event_id",
    "intent_id",
    "event_type",
    "receive_ts_ms",
    "venue_ts_ms",
    "venue_order_id",
    "fill_qty",
    "fill_price",
    "cumulative_filled_qty",
    "remaining_qty",
    "reason",
    "schema_version",
}

_SHADOW_REASONS = {
    "SHADOW_SYNTHETIC_ACK",
    "SHADOW_ONLY",
}


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    row: dict[str, Any] = {}

    for key, value in pairs:
        if key in row:
            raise MeasuredVenueTelemetryImportError(
                f"duplicate JSON key: {key}"
            )
        row[key] = value

    return row


def _is_json_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
    )


def _require_json_numeric_fields(
    row: dict[str, Any],
    *,
    required: tuple[str, ...],
    optional: tuple[str, ...],
    context: str,
) -> None:
    for name in required:
        if not _is_json_number(row.get(name)):
            raise MeasuredVenueTelemetryImportError(
                f"{context} field {name} must be a JSON number"
            )

    for name in optional:
        value = row.get(name)
        if value is not None and not _is_json_number(value):
            raise MeasuredVenueTelemetryImportError(
                f"{context} field {name} must be a JSON number or null"
            )


def _nonempty_source_string(
    value: Any,
    name: str,
) -> str:
    if not isinstance(value, str):
        raise MeasuredVenueTelemetryImportError(
            f"{name} must be a string"
        )

    text = value.strip()

    if not text:
        raise MeasuredVenueTelemetryImportError(
            f"{name} must be non-empty"
        )

    return text


def _decode_lines(raw_bytes: bytes) -> list[str]:
    if not isinstance(raw_bytes, bytes):
        raise MeasuredVenueTelemetryImportError(
            "raw telemetry input must be bytes"
        )

    if not raw_bytes:
        raise MeasuredVenueTelemetryImportError(
            "raw telemetry input must be non-empty"
        )

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MeasuredVenueTelemetryImportError(
            "raw telemetry input must be valid UTF-8"
        ) from exc

    lines = text.splitlines()

    if not lines:
        raise MeasuredVenueTelemetryImportError(
            "telemetry JSONL must contain records"
        )

    if any(not line.strip() for line in lines):
        raise MeasuredVenueTelemetryImportError(
            "telemetry JSONL cannot contain empty lines"
        )

    return lines


def _parse_record(
    line: str,
    *,
    line_number: int,
) -> tuple[OrderIntent, tuple[OrderEvent, ...]]:
    try:
        row = json.loads(
            line,
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except json.JSONDecodeError as exc:
        raise MeasuredVenueTelemetryImportError(
            f"invalid JSON on line {line_number}"
        ) from exc

    if not isinstance(row, dict):
        raise MeasuredVenueTelemetryImportError(
            f"line {line_number} must be a JSON object"
        )

    if set(row) != _RECORD_KEYS:
        raise MeasuredVenueTelemetryImportError(
            f"line {line_number} fields do not match "
            "measured telemetry schema v1"
        )

    if (
        row.get("schema_version")
        != MEASURED_VENUE_TELEMETRY_JSONL_V1
    ):
        raise MeasuredVenueTelemetryImportError(
            f"line {line_number} has unsupported schema_version"
        )

    intent_row = row.get("intent")

    if not isinstance(intent_row, dict):
        raise MeasuredVenueTelemetryImportError(
            f"line {line_number} intent must be a JSON object"
        )

    if set(intent_row) != _INTENT_KEYS:
        raise MeasuredVenueTelemetryImportError(
            f"line {line_number} intent fields do not match "
            "OrderIntent v1"
        )

    if intent_row.get("schema_version") != ORDER_INTENT_SCHEMA_V1:
        raise MeasuredVenueTelemetryImportError(
            f"line {line_number} has unsupported OrderIntent schema"
        )

    _require_json_numeric_fields(
        intent_row,
        required=(
            "qty",
            "limit_price",
            "decision_ts_ms",
            "market_data_ts_ms",
        ),
        optional=(),
        context=f"line {line_number} intent",
    )

    try:
        intent = OrderIntent.from_dict(intent_row)
    except (TypeError, ValueError) as exc:
        raise MeasuredVenueTelemetryImportError(
            f"line {line_number} contains invalid OrderIntent: {exc}"
        ) from exc

    event_rows = row.get("events")

    if not isinstance(event_rows, list) or not event_rows:
        raise MeasuredVenueTelemetryImportError(
            f"line {line_number} events must be a non-empty list"
        )

    events: list[OrderEvent] = []

    for event_index, event_row in enumerate(event_rows):
        if not isinstance(event_row, dict):
            raise MeasuredVenueTelemetryImportError(
                f"line {line_number} event {event_index} "
                "must be a JSON object"
            )

        if set(event_row) != _EVENT_KEYS:
            raise MeasuredVenueTelemetryImportError(
                f"line {line_number} event {event_index} fields "
                "do not match OrderEvent v1"
            )

        if event_row.get("schema_version") != ORDER_EVENT_SCHEMA_V1:
            raise MeasuredVenueTelemetryImportError(
                f"line {line_number} event {event_index} has "
                "unsupported OrderEvent schema"
            )

        _require_json_numeric_fields(
            event_row,
            required=(
                "receive_ts_ms",
                "fill_qty",
                "cumulative_filled_qty",
            ),
            optional=(
                "venue_ts_ms",
                "fill_price",
                "remaining_qty",
            ),
            context=(
                f"line {line_number} event {event_index}"
            ),
        )

        if event_row.get("reason") in _SHADOW_REASONS:
            raise MeasuredVenueTelemetryImportError(
                f"line {line_number} event {event_index} "
                "contains SHADOW telemetry"
            )

        try:
            event = OrderEvent(**event_row)
        except (TypeError, ValueError) as exc:
            raise MeasuredVenueTelemetryImportError(
                f"line {line_number} event {event_index} "
                f"is invalid: {exc}"
            ) from exc

        if event.intent_id != intent.intent_id:
            raise MeasuredVenueTelemetryImportError(
                f"line {line_number} event {event_index} intent_id "
                "does not match parent intent"
            )

        events.append(event)

    return intent, tuple(events)


def import_measured_venue_telemetry_jsonl(
    raw_bytes: bytes,
    *,
    source_id: str,
    source_version: str,
    source_run_id: str,
) -> MeasuredVenueTelemetryImport:
    if not isinstance(raw_bytes, bytes):
        raise MeasuredVenueTelemetryImportError(
            "raw telemetry input must be bytes"
        )

    source_id = _nonempty_source_string(
        source_id,
        "source_id",
    )
    source_version = _nonempty_source_string(
        source_version,
        "source_version",
    )
    source_run_id = _nonempty_source_string(
        source_run_id,
        "source_run_id",
    )

    source_artifact_hash = hashlib.sha256(
        raw_bytes
    ).hexdigest()

    lines = _decode_lines(raw_bytes)

    observations: list[
        MeasuredVenueExecutionObservation
    ] = []
    seen_intent_ids: set[str] = set()
    seen_event_ids: set[str] = set()

    for line_number, line in enumerate(lines, start=1):
        intent, events = _parse_record(
            line,
            line_number=line_number,
        )

        if intent.intent_id in seen_intent_ids:
            raise MeasuredVenueTelemetryImportError(
                "duplicate intent_id in telemetry import"
            )
        seen_intent_ids.add(intent.intent_id)

        for event in events:
            if event.event_id in seen_event_ids:
                raise MeasuredVenueTelemetryImportError(
                    "duplicate event_id in telemetry import"
                )
            seen_event_ids.add(event.event_id)

        try:
            observation = MeasuredVenueExecutionObservation(
                intent=intent,
                events=events,
            )
        except (TypeError, ValueError) as exc:
            raise MeasuredVenueTelemetryImportError(
                f"invalid measured observation on line "
                f"{line_number}: {exc}"
            ) from exc

        observations.append(observation)

    try:
        source = MeasuredVenueExecutionSource(
            source_id=source_id,
            source_version=source_version,
            source_run_id=source_run_id,
            source_artifact_hash=source_artifact_hash,
        )

        return MeasuredVenueTelemetryImport(
            source=source,
            observations=tuple(observations),
        )
    except (TypeError, ValueError) as exc:
        raise MeasuredVenueTelemetryImportError(
            f"invalid telemetry import result: {exc}"
        ) from exc
