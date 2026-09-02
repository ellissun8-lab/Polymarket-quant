from dataclasses import replace

import pytest

from std0_quant.execution.clodds_mapping import CLODDS_MAPPING_VERSION_V1
from std0_quant.execution.clodds_shadow_protocol import (
    AUDITED_CLODDS_COMMIT_V1,
    CLODDS_SHADOW_PROTOCOL_V1,
)
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
from std0_quant.execution.measured_venue_execution import (
    MEASURED_VENUE_EXECUTION_ARTIFACT_SCHEMA_V1,
    MEASURED_VENUE_EXECUTION_ASSEMBLER_V1,
    MeasuredVenueExecutionArtifact,
    MeasuredVenueExecutionObservation,
    MeasuredVenueExecutionSource,
    build_measured_venue_execution_artifact,
    measured_venue_execution_artifact_hash,
)


def target(**changes):
    values = {
        "factor_id": "factor-a",
        "factor_version": "1",
        "definition_hash": "a" * 64,
        "alpha_id": "alpha-a",
        "alpha_version": "1",
        "risk_policy_version": "risk-v1",
    }
    values.update(changes)
    return ExecutionValidationTarget(**values)


def provenance(*, status=READY_FOR_POLICY_EVALUATION, reasons=()):
    provisional = ExecutionValidationArtifact(
        execution_run_id="provenance-run",
        target=target(),
        status=status,
        reasons=tuple(reasons),
        alpha_factory_artifact_hash="b" * 64,
        strategy_candidate_hash="c" * 64,
        strategy_shadow_artifact_hash="d" * 64,
        strategy_shadow_run_id="shadow-run",
        protocol_version=CLODDS_SHADOW_PROTOCOL_V1,
        clodds_commit=AUDITED_CLODDS_COMMIT_V1,
        mapping_version=CLODDS_MAPPING_VERSION_V1,
        artifact_hash="PENDING",
    )
    return replace(
        provisional,
        artifact_hash=execution_validation_artifact_hash(provisional),
    )


def source(**changes):
    values = {
        "source_id": "venue-telemetry-export",
        "source_version": "1",
        "source_run_id": "source-run",
        "source_artifact_hash": "e" * 64,
    }
    values.update(changes)
    return MeasuredVenueExecutionSource(**values)


def intent(**changes):
    values = {
        "intent_id": "intent-1",
        "condition_id": "condition-1",
        "outcome": "YES",
        "side": "BUY",
        "qty": 10,
        "limit_price": 0.50,
        "time_in_force": "GTC",
        "decision_ts_ms": 1000,
        "market_data_ts_ms": 999,
        "strategy_id": "alpha-a",
        "strategy_version": "1",
        "risk_policy_version": "risk-v1",
    }
    values.update(changes)
    return OrderIntent(**values)


def venue_ack(*, intent_id="intent-1", **changes):
    values = {
        "event_id": "event-ack",
        "intent_id": intent_id,
        "event_type": OrderEventType.VENUE_ACK,
        "receive_ts_ms": 1004,
        "venue_ts_ms": 1003,
        "venue_order_id": "venue-order-1",
        "fill_qty": 0,
        "fill_price": None,
        "cumulative_filled_qty": 0,
        "remaining_qty": 10,
        "reason": None,
    }
    values.update(changes)
    return OrderEvent(**values)


def partial_fill(*, intent_id="intent-1", **changes):
    values = {
        "event_id": "event-partial",
        "intent_id": intent_id,
        "event_type": OrderEventType.PARTIAL_FILL,
        "receive_ts_ms": 1006,
        "venue_ts_ms": 1005,
        "venue_order_id": "venue-order-1",
        "fill_qty": 4,
        "fill_price": 0.50,
        "cumulative_filled_qty": 4,
        "remaining_qty": 6,
        "reason": None,
    }
    values.update(changes)
    return OrderEvent(**values)


def filled(*, intent_id="intent-1", **changes):
    values = {
        "event_id": "event-filled",
        "intent_id": intent_id,
        "event_type": OrderEventType.FILLED,
        "receive_ts_ms": 1008,
        "venue_ts_ms": 1007,
        "venue_order_id": "venue-order-1",
        "fill_qty": 6,
        "fill_price": 0.51,
        "cumulative_filled_qty": 10,
        "remaining_qty": 0,
        "reason": None,
    }
    values.update(changes)
    return OrderEvent(**values)


def observation(*, order_intent=None, events=None):
    order_intent = order_intent or intent()
    events = events or (
        venue_ack(intent_id=order_intent.intent_id),
        partial_fill(intent_id=order_intent.intent_id),
        filled(intent_id=order_intent.intent_id),
    )
    return MeasuredVenueExecutionObservation(
        intent=order_intent,
        events=tuple(events),
    )


def build(*, p=None, s=None, observations=None, evidence_run_id="measured-run"):
    return build_measured_venue_execution_artifact(
        p or provenance(),
        s or source(),
        tuple(observations or (observation(),)),
        evidence_run_id=evidence_run_id,
    )


def test_contract_symbols_and_versions():
    assert (
        MEASURED_VENUE_EXECUTION_ARTIFACT_SCHEMA_V1
        == "measured_venue_execution_artifact_v1"
    )
    assert (
        MEASURED_VENUE_EXECUTION_ASSEMBLER_V1
        == "measured_venue_execution_assembler_v1"
    )


def test_valid_measured_venue_artifact_is_ready_for_policy_evaluation():
    p = provenance()
    s = source()
    obs = observation()

    artifact = build(p=p, s=s, observations=(obs,))

    assert artifact.evidence_run_id == "measured-run"
    assert artifact.target == p.target
    assert artifact.evidence_kind == MEASURED_VENUE_EXECUTION
    assert artifact.status == READY_FOR_POLICY_EVALUATION
    assert artifact.reasons == ()
    assert artifact.provenance_artifact_hash == p.artifact_hash
    assert artifact.source == s
    assert artifact.observations == (obs,)
    assert (
        measured_venue_execution_artifact_hash(artifact)
        == artifact.artifact_hash
    )


@pytest.mark.parametrize(
    "field",
    (
        "source_id",
        "source_version",
        "source_run_id",
        "source_artifact_hash",
    ),
)
def test_source_requires_nonempty_provenance_fields(field):
    with pytest.raises((TypeError, ValueError)):
        source(**{field: ""})


def test_observation_requires_events():
    with pytest.raises(ValueError):
        MeasuredVenueExecutionObservation(
            intent=intent(),
            events=(),
        )


def test_observation_rejects_event_intent_mismatch():
    with pytest.raises(ValueError):
        observation(
            events=(
                venue_ack(intent_id="different-intent"),
            )
        )


def test_tampered_execution_provenance_is_blocked():
    p = replace(
        provenance(),
        artifact_hash="0" * 64,
    )

    artifact = build(p=p)

    assert artifact.status == BLOCKED
    assert (
        "EXECUTION_PROVENANCE_ARTIFACT_HASH_MISMATCH"
        in artifact.reasons
    )


def test_blocked_execution_provenance_cannot_be_washed_by_measured_data():
    p = provenance(
        status=BLOCKED,
        reasons=("UPSTREAM_BLOCKED",),
    )

    artifact = build(p=p)

    assert artifact.status == BLOCKED
    assert "EXECUTION_PROVENANCE_BLOCKED" in artifact.reasons


@pytest.mark.parametrize(
    "field,value,reason",
    (
        (
            "strategy_id",
            "other-alpha",
            "OBSERVATION_ALPHA_ID_MISMATCH",
        ),
        (
            "strategy_version",
            "2",
            "OBSERVATION_ALPHA_VERSION_MISMATCH",
        ),
        (
            "risk_policy_version",
            "other-risk",
            "OBSERVATION_RISK_POLICY_VERSION_MISMATCH",
        ),
    ),
)
def test_observation_intent_must_match_execution_target(
    field,
    value,
    reason,
):
    artifact = build(
        observations=(
            observation(
                order_intent=intent(**{field: value}),
                events=(
                    venue_ack(),
                ),
            ),
        )
    )

    assert artifact.status == BLOCKED
    assert reason in artifact.reasons


def test_artifact_requires_at_least_one_observation():
    artifact = build_measured_venue_execution_artifact(
        provenance(),
        source(),
        (),
        evidence_run_id="measured-run",
    )

    assert artifact.status == BLOCKED
    assert "OBSERVATIONS_EMPTY" in artifact.reasons


def test_duplicate_intent_ids_are_blocked():
    first = observation()
    second_intent = intent(condition_id="condition-2")
    second = observation(
        order_intent=second_intent,
        events=(
            venue_ack(
                event_id="event-ack-2",
                venue_order_id="venue-order-2",
            ),
        ),
    )

    artifact = build(observations=(first, second))

    assert artifact.status == BLOCKED
    assert "DUPLICATE_INTENT_ID" in artifact.reasons


def test_duplicate_event_ids_across_observations_are_blocked():
    first = observation(
        events=(venue_ack(),),
    )
    second_intent = intent(
        intent_id="intent-2",
        condition_id="condition-2",
    )
    second = observation(
        order_intent=second_intent,
        events=(
            venue_ack(
                intent_id="intent-2",
                venue_order_id="venue-order-2",
            ),
        ),
    )

    artifact = build(observations=(first, second))

    assert artifact.status == BLOCKED
    assert "DUPLICATE_EVENT_ID" in artifact.reasons


def test_shadow_synthetic_ack_cannot_be_measured_evidence():
    synthetic = venue_ack(
        venue_ts_ms=None,
        venue_order_id="shadow:intent-1",
        reason="SHADOW_SYNTHETIC_ACK",
    )

    artifact = build(
        observations=(
            observation(events=(synthetic,)),
        )
    )

    assert artifact.status == BLOCKED
    assert "SHADOW_SYNTHETIC_ACK_NOT_MEASURED" in artifact.reasons


def test_local_only_events_do_not_establish_measured_venue_evidence():
    submitted = OrderEvent(
        event_id="event-submitted",
        intent_id="intent-1",
        event_type=OrderEventType.SUBMITTED,
        receive_ts_ms=1001,
        venue_ts_ms=None,
        venue_order_id=None,
        fill_qty=0,
        fill_price=None,
        cumulative_filled_qty=0,
        remaining_qty=10,
        reason=None,
    )

    artifact = build(
        observations=(
            observation(events=(submitted,)),
        )
    )

    assert artifact.status == BLOCKED
    assert "MEASURED_VENUE_EVENT_MISSING" in artifact.reasons


def test_venue_ack_requires_venue_timestamp():
    artifact = build(
        observations=(
            observation(
                events=(
                    venue_ack(venue_ts_ms=None),
                ),
            ),
        )
    )

    assert artifact.status == BLOCKED
    assert "VENUE_EVENT_TIMESTAMP_MISSING" in artifact.reasons


def test_fill_requires_venue_order_id_for_measured_evidence():
    artifact = build(
        observations=(
            observation(
                events=(
                    partial_fill(
                        venue_order_id=None,
                    ),
                ),
            ),
        )
    )

    assert artifact.status == BLOCKED
    assert "VENUE_ORDER_ID_MISSING" in artifact.reasons


def test_receive_timeline_must_be_monotonic_within_observation():
    artifact = build(
        observations=(
            observation(
                events=(
                    venue_ack(receive_ts_ms=1010),
                    partial_fill(receive_ts_ms=1009),
                ),
            ),
        )
    )

    assert artifact.status == BLOCKED
    assert "EVENT_RECEIVE_TIMELINE_NON_MONOTONIC" in artifact.reasons


def test_fill_accounting_must_match_cumulative_and_remaining_qty():
    artifact = build(
        observations=(
            observation(
                events=(
                    partial_fill(
                        fill_qty=4,
                        cumulative_filled_qty=3,
                        remaining_qty=7,
                    ),
                ),
            ),
        )
    )

    assert artifact.status == BLOCKED
    assert "FILL_ACCOUNTING_INCONSISTENT" in artifact.reasons


def test_cumulative_fill_cannot_exceed_intent_qty():
    artifact = build(
        observations=(
            observation(
                events=(
                    partial_fill(
                        fill_qty=11,
                        cumulative_filled_qty=11,
                        remaining_qty=1,
                    ),
                ),
            ),
        )
    )

    assert artifact.status == BLOCKED
    assert "CUMULATIVE_FILL_EXCEEDS_INTENT_QTY" in artifact.reasons


def test_venue_order_id_must_be_consistent_within_intent():
    artifact = build(
        observations=(
            observation(
                events=(
                    venue_ack(),
                    partial_fill(
                        venue_order_id="different-order",
                    ),
                ),
            ),
        )
    )

    assert artifact.status == BLOCKED
    assert "VENUE_ORDER_ID_MISMATCH" in artifact.reasons


def test_artifact_hash_excludes_run_ids():
    artifact = build()
    changed = replace(
        artifact,
        evidence_run_id="different-evidence-run",
        source=replace(
            artifact.source,
            source_run_id="different-source-run",
        ),
    )

    assert (
        measured_venue_execution_artifact_hash(changed)
        == artifact.artifact_hash
    )


def test_artifact_hash_binds_source_semantics():
    artifact = build()
    changed = replace(
        artifact,
        source=replace(
            artifact.source,
            source_artifact_hash="f" * 64,
        ),
    )

    assert (
        measured_venue_execution_artifact_hash(changed)
        != artifact.artifact_hash
    )


def test_artifact_hash_binds_full_execution_events():
    artifact = build()
    obs = artifact.observations[0]
    events = list(obs.events)
    events[-1] = replace(
        events[-1],
        fill_price=0.52,
    )
    changed = replace(
        artifact,
        observations=(
            replace(obs, events=tuple(events)),
        ),
    )

    assert (
        measured_venue_execution_artifact_hash(changed)
        != artifact.artifact_hash
    )


def test_ready_artifact_cannot_have_reasons():
    artifact = build()

    with pytest.raises(ValueError):
        replace(
            artifact,
            reasons=("SHOULD_NOT_EXIST",),
        )


def test_blocked_artifact_requires_reasons():
    artifact = build()

    with pytest.raises(ValueError):
        replace(
            artifact,
            status=BLOCKED,
            reasons=(),
        )


def test_non_fill_event_cumulative_cannot_exceed_intent_qty():
    artifact = build(
        observations=(
            observation(
                events=(
                    venue_ack(
                        cumulative_filled_qty=11,
                        remaining_qty=0,
                    ),
                ),
            ),
        )
    )

    assert artifact.status == BLOCKED
    assert "CUMULATIVE_FILL_EXCEEDS_INTENT_QTY" in artifact.reasons


def test_non_fill_event_remaining_accounting_must_match_intent_qty():
    artifact = build(
        observations=(
            observation(
                events=(
                    venue_ack(
                        cumulative_filled_qty=2,
                        remaining_qty=9,
                    ),
                ),
            ),
        )
    )

    assert artifact.status == BLOCKED
    assert "FILL_ACCOUNTING_INCONSISTENT" in artifact.reasons


def test_cumulative_fill_cannot_move_backwards_on_non_fill_event():
    cancelled = venue_ack(
        event_id="event-cancelled",
        event_type=OrderEventType.CANCELLED,
        receive_ts_ms=1008,
        venue_ts_ms=1007,
        cumulative_filled_qty=3,
        remaining_qty=7,
    )

    artifact = build(
        observations=(
            observation(
                events=(
                    partial_fill(),
                    cancelled,
                ),
            ),
        )
    )

    assert artifact.status == BLOCKED
    assert "FILL_ACCOUNTING_INCONSISTENT" in artifact.reasons
