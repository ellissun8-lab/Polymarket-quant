import pytest

from std0_quant.execution.measured_venue_telemetry_bundle import (
    COVERAGE_MODE_ALL_TARGET_INTENTS_IN_WINDOW,
    FILTERING_MODE_NONE,
    MEASURED_VENUE_TELEMETRY_BUNDLE_JSONL_V2,
    measured_venue_intent_ids_hash,
)


def test_contract_symbols():
    assert (
        MEASURED_VENUE_TELEMETRY_BUNDLE_JSONL_V2
        == "measured_venue_telemetry_bundle_jsonl_v2"
    )
    assert (
        COVERAGE_MODE_ALL_TARGET_INTENTS_IN_WINDOW
        == "ALL_TARGET_INTENTS_IN_WINDOW"
    )
    assert FILTERING_MODE_NONE == "NONE"


def test_intent_ids_hash_is_order_independent():
    assert measured_venue_intent_ids_hash(
        ("intent-b", "intent-a")
    ) == measured_venue_intent_ids_hash(
        ("intent-a", "intent-b")
    )


def test_intent_ids_hash_rejects_duplicates():
    with pytest.raises(ValueError):
        measured_venue_intent_ids_hash(
            ("intent-a", "intent-a")
        )


from std0_quant.execution.execution_validation import (
    ExecutionValidationTarget,
)
from std0_quant.execution.measured_venue_telemetry_bundle import (
    MEASURED_VENUE_COVERAGE_MANIFEST_SCHEMA_V1,
    MeasuredVenueCoverageManifest,
)


def target():
    return ExecutionValidationTarget(
        factor_id="factor-a",
        factor_version="1",
        definition_hash="a" * 64,
        alpha_id="alpha-a",
        alpha_version="1",
        risk_policy_version="risk-v1",
    )


def manifest(**changes):
    values = {
        "target": target(),
        "window_start_ms": 1000.0,
        "window_end_ms": 2000.0,
        "coverage_mode": COVERAGE_MODE_ALL_TARGET_INTENTS_IN_WINDOW,
        "filtering_mode": FILTERING_MODE_NONE,
        "declared_intent_count": 2,
        "declared_intent_ids_hash": measured_venue_intent_ids_hash(
            ("intent-a", "intent-b")
        ),
        "require_terminal_lifecycle": True,
    }
    values.update(changes)
    return MeasuredVenueCoverageManifest(**values)


def test_manifest_contract():
    item = manifest()

    assert (
        item.schema_version
        == MEASURED_VENUE_COVERAGE_MANIFEST_SCHEMA_V1
    )
    assert item.target == target()
    assert item.window_start_ms == 1000.0
    assert item.window_end_ms == 2000.0
    assert item.declared_intent_count == 2
    assert item.require_terminal_lifecycle is True


@pytest.mark.parametrize(
    "changes",
    (
        {"window_end_ms": 1000.0},
        {"filtering_mode": "SELECTED"},
        {"declared_intent_count": 0},
        {"declared_intent_count": True},
        {"declared_intent_ids_hash": "not-a-sha256"},
        {"require_terminal_lifecycle": False},
    ),
)
def test_manifest_rejects_weakened_or_invalid_contract(changes):
    with pytest.raises((TypeError, ValueError)):
        manifest(**changes)


import hashlib
import json

from std0_quant.execution.contracts import (
    ORDER_EVENT_SCHEMA_V1,
    ORDER_INTENT_SCHEMA_V1,
)
from std0_quant.execution.measured_venue_telemetry_bundle import (
    MeasuredVenueTelemetryBundleImport,
    import_measured_venue_telemetry_bundle_jsonl,
)
from std0_quant.execution.measured_venue_telemetry_import import (
    MEASURED_VENUE_TELEMETRY_JSONL_V1,
)


def _bundle_record():
    return {
        "schema_version": MEASURED_VENUE_TELEMETRY_JSONL_V1,
        "intent": {
            "intent_id": "intent-a",
            "condition_id": "condition-1",
            "outcome": "YES",
            "side": "BUY",
            "qty": 10.0,
            "limit_price": 0.42,
            "time_in_force": "GTC",
            "decision_ts_ms": 1500.0,
            "market_data_ts_ms": 1499.0,
            "strategy_id": "alpha-a",
            "strategy_version": "1",
            "risk_policy_version": "risk-v1",
            "schema_version": ORDER_INTENT_SCHEMA_V1,
        },
        "events": [
            {
                "event_id": "event-a",
                "intent_id": "intent-a",
                "event_type": "FILLED",
                "receive_ts_ms": 1502.0,
                "venue_ts_ms": 1501.0,
                "venue_order_id": "venue-order-a",
                "fill_qty": 10.0,
                "fill_price": 0.41,
                "cumulative_filled_qty": 10.0,
                "remaining_qty": 0.0,
                "reason": None,
                "schema_version": ORDER_EVENT_SCHEMA_V1,
            }
        ],
    }


def _manifest_row():
    return {
        "schema_version": MEASURED_VENUE_TELEMETRY_BUNDLE_JSONL_V2,
        "coverage_manifest": {
            "target": {
                "factor_id": "factor-a",
                "factor_version": "1",
                "definition_hash": "a" * 64,
                "alpha_id": "alpha-a",
                "alpha_version": "1",
                "risk_policy_version": "risk-v1",
            },
            "window_start_ms": 1000.0,
            "window_end_ms": 2000.0,
            "coverage_mode": (
                COVERAGE_MODE_ALL_TARGET_INTENTS_IN_WINDOW
            ),
            "filtering_mode": FILTERING_MODE_NONE,
            "declared_intent_count": 1,
            "declared_intent_ids_hash": (
                measured_venue_intent_ids_hash(("intent-a",))
            ),
            "require_terminal_lifecycle": True,
            "schema_version": (
                MEASURED_VENUE_COVERAGE_MANIFEST_SCHEMA_V1
            ),
        },
    }


def _bundle_raw():
    rows = (_manifest_row(), _bundle_record())
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


def test_valid_bundle_import_binds_exact_raw_bytes():
    payload = _bundle_raw()

    result = import_measured_venue_telemetry_bundle_jsonl(
        payload,
        source_id="venue-export",
        source_version="2",
        source_run_id="source-run-2",
    )

    assert isinstance(
        result,
        MeasuredVenueTelemetryBundleImport,
    )
    assert result.manifest.target == target()
    assert len(result.observations) == 1
    assert (
        result.observations[0].intent.intent_id
        == "intent-a"
    )
    assert (
        result.source.source_artifact_hash
        == hashlib.sha256(payload).hexdigest()
    )


def _bundle_raw_with_records(*records):
    rows = (_manifest_row(), *records)
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


def test_bundle_duplicate_intent_ids_fail_closed():
    first = _bundle_record()
    second = json.loads(json.dumps(first))
    second["events"][0]["event_id"] = "event-b"
    second["events"][0]["venue_order_id"] = "venue-order-b"

    with pytest.raises(ValueError):
        import_measured_venue_telemetry_bundle_jsonl(
            _bundle_raw_with_records(first, second),
            source_id="venue-export",
            source_version="2",
            source_run_id="source-run-2",
        )


def test_bundle_duplicate_event_ids_fail_closed():
    first = _bundle_record()
    second = json.loads(json.dumps(first))
    second["intent"]["intent_id"] = "intent-b"
    second["events"][0]["intent_id"] = "intent-b"
    second["events"][0]["venue_order_id"] = "venue-order-b"

    with pytest.raises(ValueError):
        import_measured_venue_telemetry_bundle_jsonl(
            _bundle_raw_with_records(first, second),
            source_id="venue-export",
            source_version="2",
            source_run_id="source-run-2",
        )


from dataclasses import replace

from std0_quant.execution.measured_venue_telemetry_bundle import (
    verify_measured_venue_telemetry_bundle_jsonl,
)


def _load_bundle(payload=None):
    return import_measured_venue_telemetry_bundle_jsonl(
        _bundle_raw() if payload is None else payload,
        source_id="venue-export",
        source_version="2",
        source_run_id="source-run-2",
    )


def test_bundle_independent_verifier_accepts_exact_raw_bytes():
    payload = _bundle_raw()
    imported = _load_bundle(payload)

    assert (
        verify_measured_venue_telemetry_bundle_jsonl(
            payload,
            imported,
        )
        == ()
    )


def test_bundle_independent_verifier_detects_source_hash_swap():
    payload = _bundle_raw()
    imported = _load_bundle(payload)
    forged_source = replace(
        imported.source,
        source_artifact_hash="b" * 64,
    )
    forged = replace(
        imported,
        source=forged_source,
    )

    assert (
        "BUNDLE_SOURCE_ARTIFACT_HASH_MISMATCH"
        in verify_measured_venue_telemetry_bundle_jsonl(
            payload,
            forged,
        )
    )


def test_bundle_independent_verifier_detects_manifest_swap():
    payload = _bundle_raw()
    imported = _load_bundle(payload)
    forged_manifest = replace(
        imported.manifest,
        window_start_ms=1100.0,
    )
    forged = replace(
        imported,
        manifest=forged_manifest,
    )

    assert (
        "BUNDLE_MANIFEST_MISMATCH"
        in verify_measured_venue_telemetry_bundle_jsonl(
            payload,
            forged,
        )
    )


from std0_quant.execution.measured_venue_telemetry_bundle import (
    COVERAGE_BLOCKED,
    COVERAGE_COMPLETE,
    COVERAGE_INCOMPLETE,
    evaluate_measured_venue_bundle_coverage,
)


def test_coverage_complete_for_exact_terminal_bundle():
    result = evaluate_measured_venue_bundle_coverage(
        _load_bundle()
    )

    assert result.status == COVERAGE_COMPLETE
    assert result.reasons == ()
    assert result.observed_intent_count == 1
    assert result.terminal_intent_count == 1


def test_coverage_declared_count_mismatch_is_blocked():
    imported = _load_bundle()
    forged = replace(
        imported,
        manifest=replace(
            imported.manifest,
            declared_intent_count=2,
        ),
    )

    result = evaluate_measured_venue_bundle_coverage(
        forged
    )

    assert result.status == COVERAGE_BLOCKED
    assert (
        "COVERAGE_DECLARED_INTENT_COUNT_MISMATCH"
        in result.reasons
    )


def test_coverage_intent_set_mismatch_is_blocked():
    imported = _load_bundle()
    forged = replace(
        imported,
        manifest=replace(
            imported.manifest,
            declared_intent_ids_hash=(
                measured_venue_intent_ids_hash(
                    ("different-intent",)
                )
            ),
        ),
    )

    result = evaluate_measured_venue_bundle_coverage(
        forged
    )

    assert result.status == COVERAGE_BLOCKED
    assert (
        "COVERAGE_DECLARED_INTENT_IDS_HASH_MISMATCH"
        in result.reasons
    )


def test_coverage_target_mismatch_is_blocked():
    row = _bundle_record()
    row["intent"]["strategy_id"] = "different-alpha"

    imported = import_measured_venue_telemetry_bundle_jsonl(
        _bundle_raw_with_records(row),
        source_id="venue-export",
        source_version="2",
        source_run_id="source-run-2",
    )

    result = evaluate_measured_venue_bundle_coverage(
        imported
    )

    assert result.status == COVERAGE_BLOCKED
    assert "COVERAGE_TARGET_MISMATCH" in result.reasons


def test_coverage_nonterminal_lifecycle_is_incomplete():
    row = _bundle_record()
    row["events"] = [
        {
            "event_id": "event-a",
            "intent_id": "intent-a",
            "event_type": "VENUE_ACK",
            "receive_ts_ms": 1502.0,
            "venue_ts_ms": 1501.0,
            "venue_order_id": "venue-order-a",
            "fill_qty": 0.0,
            "fill_price": None,
            "cumulative_filled_qty": 0.0,
            "remaining_qty": 10.0,
            "reason": None,
            "schema_version": ORDER_EVENT_SCHEMA_V1,
        }
    ]

    imported = import_measured_venue_telemetry_bundle_jsonl(
        _bundle_raw_with_records(row),
        source_id="venue-export",
        source_version="2",
        source_run_id="source-run-2",
    )

    result = evaluate_measured_venue_bundle_coverage(
        imported
    )

    assert result.status == COVERAGE_INCOMPLETE
    assert (
        "COVERAGE_TERMINAL_LIFECYCLE_INCOMPLETE"
        in result.reasons
    )
    assert result.terminal_intent_count == 0


from std0_quant.execution.measured_venue_telemetry_bundle import (
    MeasuredVenueBundleCoverageArtifact,
    build_measured_venue_bundle_coverage_artifact,
    measured_venue_coverage_manifest_hash,
    measured_venue_bundle_coverage_artifact_hash,
    verify_measured_venue_bundle_coverage_artifact,
)


def test_coverage_manifest_hash_is_deterministic():
    assert measured_venue_coverage_manifest_hash(
        manifest()
    ) == measured_venue_coverage_manifest_hash(
        manifest()
    )


def test_coverage_artifact_binds_bundle_and_result():
    imported = _load_bundle()

    artifact = build_measured_venue_bundle_coverage_artifact(
        imported,
        coverage_run_id="coverage-run-1",
    )

    assert isinstance(
        artifact,
        MeasuredVenueBundleCoverageArtifact,
    )
    assert artifact.status == COVERAGE_COMPLETE
    assert artifact.reasons == ()
    assert (
        artifact.source_artifact_hash
        == imported.source.source_artifact_hash
    )
    assert (
        artifact.manifest_hash
        == measured_venue_coverage_manifest_hash(
            imported.manifest
        )
    )
    assert (
        artifact.artifact_hash
        == measured_venue_bundle_coverage_artifact_hash(
            artifact
        )
    )


def test_coverage_artifact_independent_verifier_accepts_exact_bundle():
    imported = _load_bundle()
    artifact = build_measured_venue_bundle_coverage_artifact(
        imported,
        coverage_run_id="coverage-run-1",
    )

    assert (
        verify_measured_venue_bundle_coverage_artifact(
            _bundle_raw(),
            artifact,
            imported,
        )
        == ()
    )


def test_coverage_artifact_verifier_detects_source_hash_swap():
    imported = _load_bundle()
    artifact = build_measured_venue_bundle_coverage_artifact(
        imported,
        coverage_run_id="coverage-run-1",
    )
    forged = replace(
        artifact,
        source_artifact_hash="b" * 64,
    )

    assert (
        "COVERAGE_SOURCE_ARTIFACT_HASH_MISMATCH"
        in verify_measured_venue_bundle_coverage_artifact(
            _bundle_raw(),
            forged,
            imported,
        )
    )


def test_coverage_artifact_verifier_detects_raw_bundle_swap():
    payload = _bundle_raw()
    imported = _load_bundle(payload)
    artifact = build_measured_venue_bundle_coverage_artifact(
        imported,
        coverage_run_id="coverage-run-1",
    )

    tampered_raw = payload.replace(
        b"venue-order-a",
        b"venue-order-b",
    )

    reasons = verify_measured_venue_bundle_coverage_artifact(
        tampered_raw,
        artifact,
        imported,
    )

    assert (
        "BUNDLE_SOURCE_ARTIFACT_HASH_MISMATCH"
        in reasons
    )
    assert "BUNDLE_OBSERVATIONS_MISMATCH" in reasons


def test_coverage_artifact_hash_excludes_coverage_run_id():
    imported = _load_bundle()

    first = build_measured_venue_bundle_coverage_artifact(
        imported,
        coverage_run_id="coverage-run-1",
    )
    second = build_measured_venue_bundle_coverage_artifact(
        imported,
        coverage_run_id="coverage-run-2",
    )

    assert first.coverage_run_id != second.coverage_run_id
    assert first.artifact_hash == second.artifact_hash


def test_coverage_artifact_verifier_detects_raw_manifest_swap():
    payload = _bundle_raw()
    imported = _load_bundle(payload)
    artifact = build_measured_venue_bundle_coverage_artifact(
        imported,
        coverage_run_id="coverage-run-1",
    )

    tampered_raw = payload.replace(
        b"2000.0",
        b"2100.0",
        1,
    )

    reasons = verify_measured_venue_bundle_coverage_artifact(
        tampered_raw,
        artifact,
        imported,
    )

    assert (
        "BUNDLE_SOURCE_ARTIFACT_HASH_MISMATCH"
        in reasons
    )
    assert "BUNDLE_MANIFEST_MISMATCH" in reasons
