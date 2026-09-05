import pytest

from std0_quant.research.factors.contracts import (
    FactorOOSPredictionArtifact,
    FactorSpec,
    ValidationStatus,
    factor_oos_predictions_hash,
    factor_spec_hash,
)
from std0_quant.research.factors.evaluator import (
    evaluate_batch,
    evaluate_factor,
    evaluate_factor_with_oos_predictions,
    evaluate_factor_with_semantic_digest_v2,
    factor_result_artifact_hash_v1,
    factor_result_semantic_digest_v2,
)


def spec(transform="identity", inputs=("signal",)):
    return FactorSpec(
        factor_id="test_factor",
        version="1",
        hypothesis="Synthetic signal contains predictive information.",
        inputs=inputs,
        transform=transform,
        parameters=(),
        lookback_ms=1000,
        decision_ts_rule="prediction_ts_ms",
        availability_ts_rule="feature_cutoff_ms<prediction_ts_ms",
        missing_policy="EXCLUDE",
        universe="fixture",
        label="y30",
        period_key="iso_week",
        expected_direction="POSITIVE",
        expected_regime="ALL",
        created_by="test",
        created_at="2026-08-30T00:00:00+00:00",
    )


def rows():
    out = []
    base = 1_760_000_000_000
    for week in range(6):
        for i in range(40):
            signal = -1.0 if i < 20 else 1.0
            out.append({
                "condition_id": f"{week}-{i}",
                "iso_week": f"W{week}",
                "prediction_ts_ms": base + week * 604800000 + i * 1000,
                "feature_cutoff_ms": base + week * 604800000 + i * 1000 - 1,
                "model_eligible": True,
                "signal": signal,
                "other": 2.0,
                "y30": int(signal > 0),
            })
    return out


def test_identity_factor_walk_forward_returns_pending_result():
    result = evaluate_factor(spec(), rows(), min_train_periods=3, min_test_n=20, run_id="eval-1")
    assert result.factor_id == "test_factor"
    assert result.n_total == 240
    assert result.n_eligible == 240
    assert result.n_folds == 3
    assert result.temporal_integrity == ValidationStatus.PASS
    assert result.research_validation_status == ValidationStatus.PENDING
    assert result.oos_auc > 0.9
    assert len(result.artifact_hash) == 64


def test_product_transform_is_supported():
    result = evaluate_factor(
        spec(transform="product", inputs=("signal", "other")),
        rows(),
        min_train_periods=3,
        min_test_n=20,
        run_id="eval-2",
    )
    assert result.oos_auc > 0.9


def test_future_cutoff_fails_closed():
    bad = rows()
    bad[0]["feature_cutoff_ms"] = bad[0]["prediction_ts_ms"] + 1
    with pytest.raises(ValueError, match="temporal"):
        evaluate_factor(spec(), bad, min_train_periods=3, min_test_n=20, run_id="eval-bad")


def test_unknown_transform_fails_closed():
    with pytest.raises(ValueError, match="transform"):
        evaluate_factor(spec(transform="agent_python"), rows(), min_train_periods=3, min_test_n=20, run_id="eval-bad")


def test_missing_exclude_changes_eligibility():
    data = rows()
    data[0]["signal"] = None
    result = evaluate_factor(spec(), data, min_train_periods=3, min_test_n=20, run_id="eval-missing")
    assert result.n_total == 240
    assert result.n_eligible == 239
    assert result.missing_rate == pytest.approx(1 / 240)


def test_batch_preserves_spec_order():
    specs = (
        spec(),
        spec(transform="product", inputs=("signal", "other")),
    )
    results = evaluate_batch(specs, rows(), min_train_periods=3, min_test_n=20, run_id="batch-1")
    assert len(results) == 2
    assert all(r.temporal_integrity == ValidationStatus.PASS for r in results)


def test_evaluate_factor_with_oos_predictions_exposes_artifact():
    result, artifact = evaluate_factor_with_oos_predictions(
        spec(),
        rows(),
        min_train_periods=3,
        min_test_n=20,
        run_id="eval-artifact-1",
    )

    assert isinstance(artifact, FactorOOSPredictionArtifact)
    assert artifact.factor_id == result.factor_id == "test_factor"
    assert artifact.factor_version == result.factor_version == "1"
    assert artifact.factor_spec_hash == factor_spec_hash(spec())
    assert artifact.run_id == result.run_id == "eval-artifact-1"
    assert len(artifact.predictions) == 120

    first = artifact.predictions[0]
    assert first.condition_id == "3-0"
    assert first.fold_id == 1
    assert first.test_period == "W3"
    assert first.prediction_ts_ms == 1_760_000_000_000 + 3 * 604800000
    assert first.y == 0
    assert 0.0 <= first.probability <= 1.0

    assert {row.fold_id for row in artifact.predictions} == {1, 2, 3}
    assert {row.test_period for row in artifact.predictions} == {"W3", "W4", "W5"}


def test_oos_prediction_artifact_hash_is_stable_across_run_ids():
    _, first = evaluate_factor_with_oos_predictions(
        spec(),
        rows(),
        min_train_periods=3,
        min_test_n=20,
        run_id="eval-artifact-run-1",
    )
    _, second = evaluate_factor_with_oos_predictions(
        spec(),
        rows(),
        min_train_periods=3,
        min_test_n=20,
        run_id="eval-artifact-run-2",
    )

    assert factor_oos_predictions_hash(first) == factor_oos_predictions_hash(second)


def test_evaluator_requires_nonempty_condition_id_for_auditable_predictions():
    data = rows()
    data[0].pop("condition_id")

    with pytest.raises(ValueError, match="condition_id"):
        evaluate_factor_with_oos_predictions(
            spec(),
            data,
            min_train_periods=3,
            min_test_n=20,
            run_id="eval-artifact-bad-id",
        )


def _legacy_factor_result_hash_payload():
    return {
        "factor_spec_hash": "spec-hash",
        "n_total": 240,
        "n_eligible": 240,
        "missing_count": 0,
        "folds": [
            {
                "fold_id": 1,
                "test_period": "W3",
                "train_n": 120,
                "test_n": 40,
                "coefficient": 3.910497197739051,
                "auc": 1.0,
                "brier": 0.01,
                "logloss": 0.1,
            }
        ],
        "predictions": [
            {
                "condition_id": "3-0",
                "period": "W3",
                "y": 0,
                "probability": 0.01987654321098765,
            }
        ],
    }


def test_factor_result_artifact_hash_v1_preserves_static_legacy_contract():
    assert (
        factor_result_artifact_hash_v1(_legacy_factor_result_hash_payload())
        == "3a398c550ae5df7eb8ac93c718317443783602f09440deb07cbf8f4fe02353df"
    )


def test_factor_result_semantic_digest_v2_is_stable_for_evaluator_fixture():
    _, digest = evaluate_factor_with_semantic_digest_v2(
        spec(),
        rows(),
        min_train_periods=3,
        min_test_n=20,
        run_id="semantic-digest-v2-check",
    )

    assert digest == "fb50781455154b052e505d6c0da3749f7d3facf94e03ca24a2b1fe307592a142"


def test_factor_result_semantic_digest_v2_absorbs_ulp_scale_float_drift():
    first = _legacy_factor_result_hash_payload()
    second = _legacy_factor_result_hash_payload()
    second["folds"][0]["coefficient"] = 3.9104971977390512
    second["predictions"][0]["probability"] = 0.019876543210987657

    assert factor_result_semantic_digest_v2(first) == factor_result_semantic_digest_v2(second)
