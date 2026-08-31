from dataclasses import FrozenInstanceError

import pytest

from std0_quant.research.factors.contracts import (
    FACTOR_OOS_PREDICTIONS_SCHEMA_V1,
    FactorOOSPrediction,
    FactorOOSPredictionArtifact,
    factor_oos_predictions_hash,
)


def prediction(**overrides):
    row = {
        "condition_id": "condition-1",
        "fold_id": 1,
        "test_period": "2026-W30",
        "prediction_ts_ms": 1_000,
        "y": 1,
        "probability": 0.75,
    }
    row.update(overrides)
    return FactorOOSPrediction(**row)


def artifact(**overrides):
    row = {
        "factor_id": "factor-1",
        "factor_version": "v1",
        "factor_spec_hash": "spec-hash-1",
        "run_id": "run-1",
        "predictions": (prediction(),),
    }
    row.update(overrides)
    return FactorOOSPredictionArtifact(**row)


def test_oos_prediction_schema_version_is_frozen():
    assert FACTOR_OOS_PREDICTIONS_SCHEMA_V1 == "factor_oos_predictions_v1"
    assert artifact().schema_version == FACTOR_OOS_PREDICTIONS_SCHEMA_V1


def test_oos_prediction_row_is_frozen():
    row = prediction()
    with pytest.raises(FrozenInstanceError):
        row.probability = 0.1


def test_oos_prediction_row_round_trips():
    row = prediction()
    assert FactorOOSPrediction.from_json(row.to_json()) == row


@pytest.mark.parametrize(
    "field,value",
    [
        ("condition_id", ""),
        ("test_period", ""),
        ("fold_id", 0),
        ("fold_id", -1),
        ("prediction_ts_ms", -1),
        ("y", -1),
        ("y", 2),
        ("y", True),
        ("probability", -0.01),
        ("probability", 1.01),
        ("probability", float("nan")),
        ("probability", float("inf")),
        ("probability", True),
    ],
)
def test_oos_prediction_row_rejects_invalid_values(field, value):
    with pytest.raises(ValueError):
        prediction(**{field: value})


def test_oos_prediction_artifact_is_frozen():
    row = artifact()
    with pytest.raises(FrozenInstanceError):
        row.run_id = "changed"


def test_oos_prediction_artifact_round_trips():
    row = artifact()
    assert FactorOOSPredictionArtifact.from_json(row.to_json()) == row


def test_oos_prediction_artifact_normalizes_prediction_sequence():
    row = artifact(predictions=[prediction()])
    assert isinstance(row.predictions, tuple)
    assert row.predictions == (prediction(),)


def test_oos_prediction_artifact_rejects_invalid_prediction_members():
    with pytest.raises(ValueError):
        artifact(predictions=({"condition_id": "not-a-contract"},))


def test_oos_prediction_artifact_rejects_unsupported_schema():
    with pytest.raises(ValueError):
        artifact(schema_version="future_schema")


def test_oos_prediction_hash_is_deterministic_and_run_id_is_provenance_only():
    first = artifact(run_id="run-1")
    second = artifact(run_id="run-2")

    assert factor_oos_predictions_hash(first) == factor_oos_predictions_hash(second)

    changed = artifact(
        predictions=(prediction(probability=0.70),),
    )
    assert factor_oos_predictions_hash(first) != factor_oos_predictions_hash(changed)
