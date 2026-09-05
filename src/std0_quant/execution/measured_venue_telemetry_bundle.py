# Deterministic measured-venue telemetry bundle contract v2.
#
# Additive to telemetry import v1. No venue networking, credentials,
# order submission, execution PASS, promotion, or LIVE capability.

from __future__ import annotations

import hashlib
import json
from typing import Iterable


MEASURED_VENUE_TELEMETRY_BUNDLE_JSONL_V2 = (
    "measured_venue_telemetry_bundle_jsonl_v2"
)

COVERAGE_MODE_ALL_TARGET_INTENTS_IN_WINDOW = (
    "ALL_TARGET_INTENTS_IN_WINDOW"
)

FILTERING_MODE_NONE = "NONE"


def measured_venue_intent_ids_hash(
    intent_ids: Iterable[str],
) -> str:
    checked: list[str] = []

    for intent_id in intent_ids:
        if not isinstance(intent_id, str):
            raise TypeError("intent_id must be a string")

        text = intent_id.strip()
        if not text:
            raise ValueError("intent_id must be non-empty")

        checked.append(text)

    if len(checked) != len(set(checked)):
        raise ValueError("duplicate intent_id")

    payload = json.dumps(
        sorted(checked),
        separators=(",", ":"),
        ensure_ascii=False,
    )

    return hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()


from dataclasses import dataclass, replace
import math

from std0_quant.execution.execution_validation import (
    ExecutionValidationTarget,
)


MEASURED_VENUE_COVERAGE_MANIFEST_SCHEMA_V1 = (
    "measured_venue_coverage_manifest_v1"
)


def _is_sha256_hex(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(ch in "0123456789abcdef" for ch in value)
    )


@dataclass(frozen=True)
class MeasuredVenueCoverageManifest:
    target: ExecutionValidationTarget
    window_start_ms: float
    window_end_ms: float
    coverage_mode: str
    filtering_mode: str
    declared_intent_count: int
    declared_intent_ids_hash: str
    require_terminal_lifecycle: bool
    schema_version: str = MEASURED_VENUE_COVERAGE_MANIFEST_SCHEMA_V1

    def __post_init__(self) -> None:
        if not isinstance(self.target, ExecutionValidationTarget):
            raise TypeError(
                "target must be ExecutionValidationTarget"
            )

        for name in (
            "window_start_ms",
            "window_end_ms",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
            ):
                raise TypeError(
                    f"{name} must be a number"
                )

            value = float(value)
            if not math.isfinite(value) or value < 0:
                raise ValueError(
                    f"{name} must be finite and nonnegative"
                )

            object.__setattr__(self, name, value)

        if self.window_end_ms <= self.window_start_ms:
            raise ValueError(
                "window_end_ms must be after window_start_ms"
            )

        if (
            self.coverage_mode
            != COVERAGE_MODE_ALL_TARGET_INTENTS_IN_WINDOW
        ):
            raise ValueError(
                "unsupported coverage_mode"
            )

        if self.filtering_mode != FILTERING_MODE_NONE:
            raise ValueError(
                "coverage manifest requires filtering_mode NONE"
            )

        if (
            not isinstance(self.declared_intent_count, int)
            or isinstance(self.declared_intent_count, bool)
            or self.declared_intent_count <= 0
        ):
            raise ValueError(
                "declared_intent_count must be a positive integer"
            )

        if not _is_sha256_hex(
            self.declared_intent_ids_hash
        ):
            raise ValueError(
                "declared_intent_ids_hash must be lowercase sha256 hex"
            )

        if self.require_terminal_lifecycle is not True:
            raise ValueError(
                "coverage manifest requires terminal lifecycle"
            )

        if (
            self.schema_version
            != MEASURED_VENUE_COVERAGE_MANIFEST_SCHEMA_V1
        ):
            raise ValueError(
                "unsupported coverage manifest schema_version"
            )


from dataclasses import fields

from std0_quant.execution.measured_venue_execution import (
    MeasuredVenueExecutionObservation,
    MeasuredVenueExecutionSource,
)
from std0_quant.execution.measured_venue_telemetry_import import (
    _decode_lines,
    _nonempty_source_string,
    _parse_record,
    _reject_duplicate_json_keys,
)


@dataclass(frozen=True)
class MeasuredVenueTelemetryBundleImport:
    source: MeasuredVenueExecutionSource
    manifest: MeasuredVenueCoverageManifest
    observations: tuple[
        MeasuredVenueExecutionObservation,
        ...,
    ]


def import_measured_venue_telemetry_bundle_jsonl(
    raw_bytes: bytes,
    *,
    source_id: str,
    source_version: str,
    source_run_id: str,
) -> MeasuredVenueTelemetryBundleImport:
    if not isinstance(raw_bytes, bytes):
        raise TypeError(
            "raw telemetry bundle input must be bytes"
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

    lines = _decode_lines(raw_bytes)
    if len(lines) < 2:
        raise ValueError(
            "telemetry bundle requires manifest and observations"
        )

    manifest_row = json.loads(
        lines[0],
        object_pairs_hook=_reject_duplicate_json_keys,
    )

    if not isinstance(manifest_row, dict):
        raise ValueError(
            "bundle manifest row must be a JSON object"
        )

    if set(manifest_row) != {
        "schema_version",
        "coverage_manifest",
    }:
        raise ValueError(
            "bundle manifest row fields do not match v2 schema"
        )

    if (
        manifest_row.get("schema_version")
        != MEASURED_VENUE_TELEMETRY_BUNDLE_JSONL_V2
    ):
        raise ValueError(
            "unsupported telemetry bundle schema_version"
        )

    manifest_payload = manifest_row.get(
        "coverage_manifest"
    )
    if not isinstance(manifest_payload, dict):
        raise ValueError(
            "coverage_manifest must be a JSON object"
        )

    expected_manifest_fields = {
        field.name
        for field in fields(MeasuredVenueCoverageManifest)
    }
    if set(manifest_payload) != expected_manifest_fields:
        raise ValueError(
            "coverage manifest fields do not match schema"
        )

    target_payload = manifest_payload.get("target")
    if not isinstance(target_payload, dict):
        raise ValueError(
            "coverage manifest target must be a JSON object"
        )

    target = ExecutionValidationTarget(**target_payload)

    manifest_values = dict(manifest_payload)
    manifest_values["target"] = target
    manifest = MeasuredVenueCoverageManifest(
        **manifest_values
    )

    observations: list[
        MeasuredVenueExecutionObservation
    ] = []
    seen_intent_ids: set[str] = set()
    seen_event_ids: set[str] = set()

    for line_number, line in enumerate(
        lines[1:],
        start=2,
    ):
        intent, events = _parse_record(
            line,
            line_number=line_number,
        )

        if intent.intent_id in seen_intent_ids:
            raise ValueError(
                "duplicate intent_id in telemetry bundle"
            )
        seen_intent_ids.add(intent.intent_id)

        for event in events:
            if event.event_id in seen_event_ids:
                raise ValueError(
                    "duplicate event_id in telemetry bundle"
                )
            seen_event_ids.add(event.event_id)

        observations.append(
            MeasuredVenueExecutionObservation(
                intent=intent,
                events=events,
            )
        )

    source = MeasuredVenueExecutionSource(
        source_id=source_id,
        source_version=source_version,
        source_run_id=source_run_id,
        source_artifact_hash=hashlib.sha256(
            raw_bytes
        ).hexdigest(),
    )

    return MeasuredVenueTelemetryBundleImport(
        source=source,
        manifest=manifest,
        observations=tuple(observations),
    )


def verify_measured_venue_telemetry_bundle_jsonl(
    raw_bytes: bytes,
    imported: MeasuredVenueTelemetryBundleImport,
) -> tuple[str, ...]:
    if not isinstance(raw_bytes, bytes):
        raise TypeError("raw_bytes must be bytes")

    if not isinstance(
        imported,
        MeasuredVenueTelemetryBundleImport,
    ):
        raise TypeError(
            "imported must be MeasuredVenueTelemetryBundleImport"
        )

    reasons: list[str] = []

    def add_reason(reason: str) -> None:
        if reason not in reasons:
            reasons.append(reason)

    expected_source_hash = hashlib.sha256(
        raw_bytes
    ).hexdigest()

    if (
        imported.source.source_artifact_hash
        != expected_source_hash
    ):
        add_reason(
            "BUNDLE_SOURCE_ARTIFACT_HASH_MISMATCH"
        )

    try:
        reparsed = (
            import_measured_venue_telemetry_bundle_jsonl(
                raw_bytes,
                source_id=imported.source.source_id,
                source_version=imported.source.source_version,
                source_run_id=imported.source.source_run_id,
            )
        )
    except (TypeError, ValueError):
        add_reason("BUNDLE_RAW_PARSE_FAILED")
        return tuple(reasons)

    if imported.manifest != reparsed.manifest:
        add_reason("BUNDLE_MANIFEST_MISMATCH")

    if imported.observations != reparsed.observations:
        add_reason("BUNDLE_OBSERVATIONS_MISMATCH")

    return tuple(reasons)


from std0_quant.execution.contracts import OrderEventType


COVERAGE_COMPLETE = "COVERAGE_COMPLETE"
COVERAGE_INCOMPLETE = "COVERAGE_INCOMPLETE"
COVERAGE_BLOCKED = "COVERAGE_BLOCKED"

_TERMINAL_EVENT_TYPES = {
    OrderEventType.FILLED,
    OrderEventType.CANCELLED,
    OrderEventType.REJECTED,
    OrderEventType.EXPIRED,
}


@dataclass(frozen=True)
class MeasuredVenueBundleCoverageResult:
    status: str
    reasons: tuple[str, ...]
    observed_intent_count: int
    terminal_intent_count: int
    observed_intent_ids_hash: str


def evaluate_measured_venue_bundle_coverage(
    imported: MeasuredVenueTelemetryBundleImport,
) -> MeasuredVenueBundleCoverageResult:
    if not isinstance(
        imported,
        MeasuredVenueTelemetryBundleImport,
    ):
        raise TypeError(
            "imported must be MeasuredVenueTelemetryBundleImport"
        )

    manifest = imported.manifest
    observations = imported.observations
    reasons: list[str] = []

    def add_reason(reason: str) -> None:
        if reason not in reasons:
            reasons.append(reason)

    intent_ids = tuple(
        observation.intent.intent_id
        for observation in observations
    )

    observed_intent_ids_hash = (
        measured_venue_intent_ids_hash(intent_ids)
    )

    if (
        len(observations)
        != manifest.declared_intent_count
    ):
        add_reason(
            "COVERAGE_DECLARED_INTENT_COUNT_MISMATCH"
        )

    if (
        observed_intent_ids_hash
        != manifest.declared_intent_ids_hash
    ):
        add_reason(
            "COVERAGE_DECLARED_INTENT_IDS_HASH_MISMATCH"
        )

    terminal_intent_count = 0

    for observation in observations:
        intent = observation.intent

        if (
            intent.strategy_id != manifest.target.alpha_id
            or intent.strategy_version
            != manifest.target.alpha_version
            or intent.risk_policy_version
            != manifest.target.risk_policy_version
        ):
            add_reason("COVERAGE_TARGET_MISMATCH")

        if not (
            manifest.window_start_ms
            <= intent.decision_ts_ms
            < manifest.window_end_ms
        ):
            add_reason("COVERAGE_INTENT_OUTSIDE_WINDOW")

        if (
            observation.events
            and observation.events[-1].event_type
            in _TERMINAL_EVENT_TYPES
        ):
            terminal_intent_count += 1

    blocking_reasons = tuple(
        reason
        for reason in reasons
        if reason
        != "COVERAGE_TERMINAL_LIFECYCLE_INCOMPLETE"
    )

    if (
        terminal_intent_count
        != len(observations)
    ):
        add_reason(
            "COVERAGE_TERMINAL_LIFECYCLE_INCOMPLETE"
        )

    if blocking_reasons:
        status = COVERAGE_BLOCKED
    elif (
        terminal_intent_count
        != len(observations)
    ):
        status = COVERAGE_INCOMPLETE
    else:
        status = COVERAGE_COMPLETE

    return MeasuredVenueBundleCoverageResult(
        status=status,
        reasons=tuple(reasons),
        observed_intent_count=len(observations),
        terminal_intent_count=terminal_intent_count,
        observed_intent_ids_hash=(
            observed_intent_ids_hash
        ),
    )


MEASURED_VENUE_BUNDLE_COVERAGE_ARTIFACT_SCHEMA_V1 = (
    "measured_venue_bundle_coverage_artifact_v1"
)
MEASURED_VENUE_BUNDLE_COVERAGE_EVALUATOR_V1 = (
    "measured_venue_bundle_coverage_evaluator_v1"
)


def measured_venue_coverage_manifest_hash(
    manifest: MeasuredVenueCoverageManifest,
) -> str:
    if not isinstance(
        manifest,
        MeasuredVenueCoverageManifest,
    ):
        raise TypeError(
            "manifest must be MeasuredVenueCoverageManifest"
        )

    payload = {
        "target": {
            "factor_id": manifest.target.factor_id,
            "factor_version": manifest.target.factor_version,
            "definition_hash": manifest.target.definition_hash,
            "alpha_id": manifest.target.alpha_id,
            "alpha_version": manifest.target.alpha_version,
            "risk_policy_version": (
                manifest.target.risk_policy_version
            ),
        },
        "window_start_ms": manifest.window_start_ms,
        "window_end_ms": manifest.window_end_ms,
        "coverage_mode": manifest.coverage_mode,
        "filtering_mode": manifest.filtering_mode,
        "declared_intent_count": (
            manifest.declared_intent_count
        ),
        "declared_intent_ids_hash": (
            manifest.declared_intent_ids_hash
        ),
        "require_terminal_lifecycle": (
            manifest.require_terminal_lifecycle
        ),
        "schema_version": manifest.schema_version,
    }

    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class MeasuredVenueBundleCoverageArtifact:
    coverage_run_id: str
    source_artifact_hash: str
    manifest_hash: str
    status: str
    reasons: tuple[str, ...]
    observed_intent_count: int
    terminal_intent_count: int
    observed_intent_ids_hash: str
    artifact_hash: str
    evaluator_version: str = (
        MEASURED_VENUE_BUNDLE_COVERAGE_EVALUATOR_V1
    )
    schema_version: str = (
        MEASURED_VENUE_BUNDLE_COVERAGE_ARTIFACT_SCHEMA_V1
    )

    def __post_init__(self) -> None:
        if (
            not isinstance(self.coverage_run_id, str)
            or not self.coverage_run_id.strip()
        ):
            raise ValueError(
                "coverage_run_id must be non-empty"
            )

        for name in (
            "source_artifact_hash",
            "manifest_hash",
            "observed_intent_ids_hash",
            "artifact_hash",
        ):
            if not _is_sha256_hex(getattr(self, name)):
                raise ValueError(
                    f"{name} must be lowercase sha256 hex"
                )

        if self.status not in {
            COVERAGE_COMPLETE,
            COVERAGE_INCOMPLETE,
            COVERAGE_BLOCKED,
        }:
            raise ValueError(
                "unsupported coverage artifact status"
            )

        reasons = tuple(self.reasons)
        object.__setattr__(self, "reasons", reasons)

        if self.status == COVERAGE_COMPLETE and reasons:
            raise ValueError(
                "COVERAGE_COMPLETE cannot contain reasons"
            )

        if (
            self.status
            in {COVERAGE_INCOMPLETE, COVERAGE_BLOCKED}
            and not reasons
        ):
            raise ValueError(
                "non-complete coverage requires reasons"
            )

        for name in (
            "observed_intent_count",
            "terminal_intent_count",
        ):
            value = getattr(self, name)
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            ):
                raise ValueError(
                    f"{name} must be a nonnegative integer"
                )

        if (
            self.terminal_intent_count
            > self.observed_intent_count
        ):
            raise ValueError(
                "terminal_intent_count cannot exceed "
                "observed_intent_count"
            )

        if (
            self.evaluator_version
            != MEASURED_VENUE_BUNDLE_COVERAGE_EVALUATOR_V1
        ):
            raise ValueError(
                "unsupported coverage evaluator_version"
            )

        if (
            self.schema_version
            != MEASURED_VENUE_BUNDLE_COVERAGE_ARTIFACT_SCHEMA_V1
        ):
            raise ValueError(
                "unsupported coverage artifact schema_version"
            )


def measured_venue_bundle_coverage_artifact_hash(
    artifact: MeasuredVenueBundleCoverageArtifact,
) -> str:
    if not isinstance(
        artifact,
        MeasuredVenueBundleCoverageArtifact,
    ):
        raise TypeError(
            "artifact must be "
            "MeasuredVenueBundleCoverageArtifact"
        )

    payload = {
        "source_artifact_hash": (
            artifact.source_artifact_hash
        ),
        "manifest_hash": artifact.manifest_hash,
        "status": artifact.status,
        "reasons": artifact.reasons,
        "observed_intent_count": (
            artifact.observed_intent_count
        ),
        "terminal_intent_count": (
            artifact.terminal_intent_count
        ),
        "observed_intent_ids_hash": (
            artifact.observed_intent_ids_hash
        ),
        "evaluator_version": artifact.evaluator_version,
        "schema_version": artifact.schema_version,
    }

    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def build_measured_venue_bundle_coverage_artifact(
    imported: MeasuredVenueTelemetryBundleImport,
    *,
    coverage_run_id: str,
) -> MeasuredVenueBundleCoverageArtifact:
    if (
        not isinstance(coverage_run_id, str)
        or not coverage_run_id.strip()
    ):
        raise ValueError(
            "coverage_run_id must be non-empty"
        )

    result = evaluate_measured_venue_bundle_coverage(
        imported
    )

    provisional = MeasuredVenueBundleCoverageArtifact(
        coverage_run_id=coverage_run_id,
        source_artifact_hash=(
            imported.source.source_artifact_hash
        ),
        manifest_hash=(
            measured_venue_coverage_manifest_hash(
                imported.manifest
            )
        ),
        status=result.status,
        reasons=result.reasons,
        observed_intent_count=(
            result.observed_intent_count
        ),
        terminal_intent_count=(
            result.terminal_intent_count
        ),
        observed_intent_ids_hash=(
            result.observed_intent_ids_hash
        ),
        artifact_hash="0" * 64,
    )

    return replace(
        provisional,
        artifact_hash=(
            measured_venue_bundle_coverage_artifact_hash(
                provisional
            )
        ),
    )


def verify_measured_venue_bundle_coverage_artifact(
    raw_bytes: bytes,
    artifact: MeasuredVenueBundleCoverageArtifact,
    imported: MeasuredVenueTelemetryBundleImport,
) -> tuple[str, ...]:
    if not isinstance(raw_bytes, bytes):
        raise TypeError("raw_bytes must be bytes")

    if not isinstance(
        artifact,
        MeasuredVenueBundleCoverageArtifact,
    ):
        raise TypeError(
            "artifact must be "
            "MeasuredVenueBundleCoverageArtifact"
        )

    if not isinstance(
        imported,
        MeasuredVenueTelemetryBundleImport,
    ):
        raise TypeError(
            "imported must be MeasuredVenueTelemetryBundleImport"
        )

    reasons: list[str] = []

    def add_reason(reason: str) -> None:
        if reason not in reasons:
            reasons.append(reason)

    for reason in verify_measured_venue_telemetry_bundle_jsonl(
        raw_bytes,
        imported,
    ):
        add_reason(reason)

    if (
        measured_venue_bundle_coverage_artifact_hash(
            artifact
        )
        != artifact.artifact_hash
    ):
        add_reason(
            "COVERAGE_ARTIFACT_HASH_MISMATCH"
        )

    if (
        artifact.source_artifact_hash
        != imported.source.source_artifact_hash
    ):
        add_reason(
            "COVERAGE_SOURCE_ARTIFACT_HASH_MISMATCH"
        )

    expected_manifest_hash = (
        measured_venue_coverage_manifest_hash(
            imported.manifest
        )
    )
    if artifact.manifest_hash != expected_manifest_hash:
        add_reason(
            "COVERAGE_MANIFEST_HASH_MISMATCH"
        )

    expected = evaluate_measured_venue_bundle_coverage(
        imported
    )

    if artifact.status != expected.status:
        add_reason("COVERAGE_STATUS_MISMATCH")

    if artifact.reasons != expected.reasons:
        add_reason("COVERAGE_REASONS_MISMATCH")

    if (
        artifact.observed_intent_count
        != expected.observed_intent_count
    ):
        add_reason(
            "COVERAGE_OBSERVED_INTENT_COUNT_MISMATCH"
        )

    if (
        artifact.terminal_intent_count
        != expected.terminal_intent_count
    ):
        add_reason(
            "COVERAGE_TERMINAL_INTENT_COUNT_MISMATCH"
        )

    if (
        artifact.observed_intent_ids_hash
        != expected.observed_intent_ids_hash
    ):
        add_reason(
            "COVERAGE_OBSERVED_INTENT_IDS_HASH_MISMATCH"
        )

    return tuple(reasons)
