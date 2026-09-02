# Measured venue execution evidence v1.
#
# This module only validates and assembles externally collected venue
# telemetry. It does not submit orders, hold credentials, or promote factors.

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass, replace
from enum import Enum
import hashlib
import json
from typing import Any

from std0_quant.execution.contracts import (
    OrderEvent,
    OrderEventType,
    OrderIntent,
)
from std0_quant.execution.execution_validation import (
    BLOCKED,
    READY_FOR_POLICY_EVALUATION,
    ExecutionValidationArtifact,
    ExecutionValidationTarget,
    execution_validation_artifact_hash,
)
from std0_quant.execution.execution_validation_policy import (
    MEASURED_VENUE_EXECUTION,
)


MEASURED_VENUE_EXECUTION_ARTIFACT_SCHEMA_V1 = (
    "measured_venue_execution_artifact_v1"
)
MEASURED_VENUE_EXECUTION_ASSEMBLER_V1 = (
    "measured_venue_execution_assembler_v1"
)


def _nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    text = value.strip()
    if not text:
        raise ValueError(f"{name} must be non-empty")
    return text


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {
            field.name: _jsonable(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, dict):
        return {
            str(key): _jsonable(item)
            for key, item in sorted(
                value.items(),
                key=lambda pair: str(pair[0]),
            )
        }
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _jsonable(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


@dataclass(frozen=True)
class MeasuredVenueExecutionSource:
    source_id: str
    source_version: str
    source_run_id: str
    source_artifact_hash: str

    def __post_init__(self) -> None:
        for name in (
            "source_id",
            "source_version",
            "source_run_id",
            "source_artifact_hash",
        ):
            object.__setattr__(
                self,
                name,
                _nonempty(getattr(self, name), name),
            )


@dataclass(frozen=True)
class MeasuredVenueExecutionObservation:
    intent: OrderIntent
    events: tuple[OrderEvent, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.intent, OrderIntent):
            raise TypeError("intent must be OrderIntent")

        events = tuple(self.events)
        if not events:
            raise ValueError("measured observation requires events")

        for event in events:
            if not isinstance(event, OrderEvent):
                raise TypeError("events must contain OrderEvent")
            if event.intent_id != self.intent.intent_id:
                raise ValueError(
                    "measured event intent_id does not match observation intent"
                )

        object.__setattr__(self, "events", events)


@dataclass(frozen=True)
class MeasuredVenueExecutionArtifact:
    evidence_run_id: str
    target: ExecutionValidationTarget
    evidence_kind: str
    status: str
    reasons: tuple[str, ...]
    provenance_artifact_hash: str
    source: MeasuredVenueExecutionSource
    observations: tuple[MeasuredVenueExecutionObservation, ...]
    artifact_hash: str
    assembler_version: str = MEASURED_VENUE_EXECUTION_ASSEMBLER_V1
    schema_version: str = MEASURED_VENUE_EXECUTION_ARTIFACT_SCHEMA_V1

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "evidence_run_id",
            _nonempty(self.evidence_run_id, "evidence_run_id"),
        )

        if not isinstance(self.target, ExecutionValidationTarget):
            raise TypeError("target must be ExecutionValidationTarget")

        if self.evidence_kind != MEASURED_VENUE_EXECUTION:
            raise ValueError(
                "unsupported measured venue execution evidence_kind"
            )

        if self.status not in {
            READY_FOR_POLICY_EVALUATION,
            BLOCKED,
        }:
            raise ValueError(
                "unsupported measured venue execution status"
            )

        reasons = tuple(
            _nonempty(reason, "reason")
            for reason in self.reasons
        )
        object.__setattr__(self, "reasons", reasons)

        if (
            self.status == READY_FOR_POLICY_EVALUATION
            and reasons
        ):
            raise ValueError(
                "READY_FOR_POLICY_EVALUATION cannot contain reasons"
            )
        if self.status == BLOCKED and not reasons:
            raise ValueError("BLOCKED requires reasons")

        object.__setattr__(
            self,
            "provenance_artifact_hash",
            _nonempty(
                self.provenance_artifact_hash,
                "provenance_artifact_hash",
            ),
        )

        if not isinstance(
            self.source,
            MeasuredVenueExecutionSource,
        ):
            raise TypeError(
                "source must be MeasuredVenueExecutionSource"
            )

        observations = tuple(self.observations)
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

        object.__setattr__(
            self,
            "artifact_hash",
            _nonempty(self.artifact_hash, "artifact_hash"),
        )

        if (
            self.assembler_version
            != MEASURED_VENUE_EXECUTION_ASSEMBLER_V1
        ):
            raise ValueError("unsupported assembler_version")
        if (
            self.schema_version
            != MEASURED_VENUE_EXECUTION_ARTIFACT_SCHEMA_V1
        ):
            raise ValueError("unsupported schema_version")


def _artifact_payload(
    artifact: MeasuredVenueExecutionArtifact,
) -> dict[str, Any]:
    return {
        "target": artifact.target,
        "evidence_kind": artifact.evidence_kind,
        "status": artifact.status,
        "reasons": artifact.reasons,
        "provenance_artifact_hash": (
            artifact.provenance_artifact_hash
        ),
        "source": {
            "source_id": artifact.source.source_id,
            "source_version": artifact.source.source_version,
            "source_artifact_hash": (
                artifact.source.source_artifact_hash
            ),
        },
        "observations": artifact.observations,
        "assembler_version": artifact.assembler_version,
        "schema_version": artifact.schema_version,
    }


def measured_venue_execution_artifact_hash(
    artifact: MeasuredVenueExecutionArtifact,
) -> str:
    if not isinstance(
        artifact,
        MeasuredVenueExecutionArtifact,
    ):
        raise TypeError(
            "artifact must be MeasuredVenueExecutionArtifact"
        )

    payload = _canonical_json(
        _artifact_payload(artifact)
    )
    return hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()


def build_measured_venue_execution_artifact(
    provenance: ExecutionValidationArtifact,
    source: MeasuredVenueExecutionSource,
    observations: tuple[
        MeasuredVenueExecutionObservation,
        ...,
    ],
    *,
    evidence_run_id: str,
) -> MeasuredVenueExecutionArtifact:
    if not isinstance(
        provenance,
        ExecutionValidationArtifact,
    ):
        raise TypeError(
            "provenance must be ExecutionValidationArtifact"
        )
    if not isinstance(
        source,
        MeasuredVenueExecutionSource,
    ):
        raise TypeError(
            "source must be MeasuredVenueExecutionSource"
        )

    evidence_run_id = _nonempty(
        evidence_run_id,
        "evidence_run_id",
    )
    observations = tuple(observations)

    reasons: list[str] = []

    def add_reason(reason: str) -> None:
        if reason not in reasons:
            reasons.append(reason)

    if (
        execution_validation_artifact_hash(provenance)
        != provenance.artifact_hash
    ):
        add_reason(
            "EXECUTION_PROVENANCE_ARTIFACT_HASH_MISMATCH"
        )

    if provenance.status == BLOCKED:
        add_reason("EXECUTION_PROVENANCE_BLOCKED")
        for reason in provenance.reasons:
            add_reason(reason)
    elif provenance.status != READY_FOR_POLICY_EVALUATION:
        add_reason(
            "EXECUTION_PROVENANCE_STATUS_UNSUPPORTED"
        )

    if not observations:
        add_reason("OBSERVATIONS_EMPTY")

    seen_intent_ids: set[str] = set()
    seen_event_ids: set[str] = set()

    venue_types = {
        OrderEventType.VENUE_ACK,
        OrderEventType.PARTIAL_FILL,
        OrderEventType.FILLED,
        OrderEventType.CANCELLED,
        OrderEventType.REJECTED,
        OrderEventType.EXPIRED,
    }
    fill_types = {
        OrderEventType.PARTIAL_FILL,
        OrderEventType.FILLED,
    }

    for observation in observations:
        if not isinstance(
            observation,
            MeasuredVenueExecutionObservation,
        ):
            raise TypeError(
                "observations must contain "
                "MeasuredVenueExecutionObservation"
            )

        order_intent = observation.intent

        if order_intent.intent_id in seen_intent_ids:
            add_reason("DUPLICATE_INTENT_ID")
        seen_intent_ids.add(order_intent.intent_id)

        if (
            order_intent.strategy_id
            != provenance.target.alpha_id
        ):
            add_reason("OBSERVATION_ALPHA_ID_MISMATCH")
        if (
            order_intent.strategy_version
            != provenance.target.alpha_version
        ):
            add_reason(
                "OBSERVATION_ALPHA_VERSION_MISMATCH"
            )
        if (
            order_intent.risk_policy_version
            != provenance.target.risk_policy_version
        ):
            add_reason(
                "OBSERVATION_RISK_POLICY_VERSION_MISMATCH"
            )

        previous_receive_ts: float | None = None
        previous_cumulative = 0.0
        venue_order_id: str | None = None
        saw_measured_venue_event = False

        for event in observation.events:
            if event.event_id in seen_event_ids:
                add_reason("DUPLICATE_EVENT_ID")
            seen_event_ids.add(event.event_id)

            if (
                previous_receive_ts is not None
                and event.receive_ts_ms < previous_receive_ts
            ):
                add_reason(
                    "EVENT_RECEIVE_TIMELINE_NON_MONOTONIC"
                )
            previous_receive_ts = event.receive_ts_ms

            if event.reason == "SHADOW_SYNTHETIC_ACK":
                add_reason(
                    "SHADOW_SYNTHETIC_ACK_NOT_MEASURED"
                )

            if event.event_type in venue_types:
                if event.venue_ts_ms is None:
                    add_reason(
                        "VENUE_EVENT_TIMESTAMP_MISSING"
                    )
                else:
                    saw_measured_venue_event = True

                if event.venue_order_id is not None:
                    if venue_order_id is None:
                        venue_order_id = event.venue_order_id
                    elif (
                        event.venue_order_id
                        != venue_order_id
                    ):
                        add_reason(
                            "VENUE_ORDER_ID_MISMATCH"
                        )

            if (
                event.cumulative_filled_qty
                > order_intent.qty + 1e-9
            ):
                add_reason(
                    "CUMULATIVE_FILL_EXCEEDS_INTENT_QTY"
                )

            if (
                event.remaining_qty is not None
                and abs(
                    (
                        event.cumulative_filled_qty
                        + event.remaining_qty
                    )
                    - order_intent.qty
                )
                > 1e-9
            ):
                add_reason(
                    "FILL_ACCOUNTING_INCONSISTENT"
                )

            if event.event_type in fill_types:
                if event.venue_order_id is None:
                    add_reason("VENUE_ORDER_ID_MISSING")

                expected_cumulative = (
                    previous_cumulative
                    + event.fill_qty
                )

                if (
                    abs(
                        event.cumulative_filled_qty
                        - expected_cumulative
                    )
                    > 1e-9
                ):
                    add_reason(
                        "FILL_ACCOUNTING_INCONSISTENT"
                    )
            elif (
                abs(
                    event.cumulative_filled_qty
                    - previous_cumulative
                )
                > 1e-9
            ):
                add_reason(
                    "FILL_ACCOUNTING_INCONSISTENT"
                )

            previous_cumulative = max(
                previous_cumulative,
                event.cumulative_filled_qty,
            )

        if not saw_measured_venue_event:
            add_reason("MEASURED_VENUE_EVENT_MISSING")

    status = (
        BLOCKED
        if reasons
        else READY_FOR_POLICY_EVALUATION
    )

    provisional = MeasuredVenueExecutionArtifact(
        evidence_run_id=evidence_run_id,
        target=provenance.target,
        evidence_kind=MEASURED_VENUE_EXECUTION,
        status=status,
        reasons=tuple(reasons),
        provenance_artifact_hash=provenance.artifact_hash,
        source=source,
        observations=observations,
        artifact_hash="PENDING",
    )

    return replace(
        provisional,
        artifact_hash=(
            measured_venue_execution_artifact_hash(
                provisional
            )
        ),
    )
