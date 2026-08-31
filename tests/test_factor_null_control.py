from dataclasses import FrozenInstanceError

import pytest

from std0_quant.research.factors.contracts import (
    FACTOR_NULL_CONTROL_EVIDENCE_SCHEMA_V1,
    FactorNullControlEvidence,
    FactorOOSPrediction,
    FactorOOSPredictionArtifact,
    factor_null_control_evidence_hash,
    factor_oos_predictions_hash,
)
from std0_quant.research.factors.null_control import run_factor_null_control


def predictions():
    rows = []
    ts = 1_000
    for period_index in range(4):
        for i in range(20):
            y = i % 2
            rows.append(
                FactorOOSPrediction(
                    condition_id=f"{period_index}-{i}",
                    fold_id=period_index + 1,
                    test_period=f"P{period_index}",
                    prediction_ts_ms=ts,
                    y=y,
                    probability=0.8 if y else 0.2,
                )
            )
            ts += 1
    return tuple(rows)


def artifact(*, run_id="oos-run-1", prediction_rows=None):
    return FactorOOSPredictionArtifact(
        factor_id="factor-1",
        factor_version="v1",
        factor_spec_hash="spec-hash-1",
        run_id=run_id,
        predictions=predictions() if prediction_rows is None else prediction_rows,
    )


def run(source=None, **overrides):
    values = {
        "n_shuffles": 50,
        "seed": 12345,
        "run_id": "null-run-1",
    }
    values.update(overrides)
    return run_factor_null_control(
        artifact() if source is None else source,
        **values,
    )


def test_null_control_schema_and_source_binding():
    source = artifact()
    evidence = run(source)

    assert isinstance(evidence, FactorNullControlEvidence)
    assert (
        evidence.schema_version
        == FACTOR_NULL_CONTROL_EVIDENCE_SCHEMA_V1
        == "factor_null_control_evidence_v1"
    )
    assert evidence.factor_id == source.factor_id
    assert evidence.factor_version == source.factor_version
    assert evidence.factor_spec_hash == source.factor_spec_hash
    assert evidence.oos_predictions_hash == factor_oos_predictions_hash(source)
    assert evidence.oos_run_id == source.run_id
    assert evidence.method == "within_period_label_permutation_fixed_oos_v1"
    assert evidence.seed == 12345
    assert evidence.n_shuffles == 50
    assert evidence.n_predictions == 80
    assert evidence.run_id == "null-run-1"


def test_null_control_evidence_is_frozen():
    evidence = run()
    with pytest.raises(FrozenInstanceError):
        evidence.seed = 1


def test_null_control_is_deterministic_for_same_seed():
    assert run() == run()


def test_null_control_metric_summaries_are_well_formed():
    evidence = run()

    for summary in (
        evidence.pooled_auc,
        evidence.macro_period_auc,
        evidence.weighted_period_auc,
    ):
        assert summary.n_valid == 50
        for value in (summary.mean, summary.std, summary.p95, summary.max):
            assert value is not None
            assert 0.0 <= value <= 1.0


def test_null_control_preserves_period_structure():
    rows = []
    ts = 1_000
    for period, positive_count, base_probability in (
        ("LOW", 2, 0.1),
        ("HIGH", 18, 0.9),
    ):
        for i in range(20):
            rows.append(
                FactorOOSPrediction(
                    condition_id=f"{period}-{i}",
                    fold_id=1 if period == "LOW" else 2,
                    test_period=period,
                    prediction_ts_ms=ts,
                    y=int(i < positive_count),
                    probability=base_probability + i * 0.0001,
                )
            )
            ts += 1

    evidence = run(
        artifact(prediction_rows=tuple(rows)),
        n_shuffles=100,
        seed=7,
    )

    assert evidence.pooled_auc.mean is not None
    assert evidence.macro_period_auc.mean is not None
    assert evidence.pooled_auc.mean > 0.75
    assert abs(evidence.macro_period_auc.mean - 0.5) < 0.15


def test_null_control_does_not_mutate_source_artifact():
    source = artifact()
    before = source.to_json()
    run(source)
    assert source.to_json() == before


def test_null_control_hash_excludes_run_provenance():
    first_source = artifact(run_id="oos-run-1")
    second_source = artifact(run_id="oos-run-2")

    first = run(first_source, run_id="null-run-1")
    second = run(second_source, run_id="null-run-2")

    assert factor_null_control_evidence_hash(first) == factor_null_control_evidence_hash(second)


def test_null_control_has_no_embedded_pass_fail_decision():
    evidence = run()
    assert not hasattr(evidence, "status")
    assert not hasattr(evidence, "research_validation_status")


def test_null_control_requires_positive_shuffle_count():
    with pytest.raises(ValueError, match="n_shuffles"):
        run(n_shuffles=0)


def test_null_control_allows_unevaluable_auc_as_missing_evidence():
    rows = tuple(
        FactorOOSPrediction(
            condition_id=f"x-{i}",
            fold_id=1,
            test_period="P0",
            prediction_ts_ms=1_000 + i,
            y=1,
            probability=0.5,
        )
        for i in range(10)
    )

    evidence = run(
        artifact(prediction_rows=rows),
        n_shuffles=5,
    )

    for summary in (
        evidence.pooled_auc,
        evidence.macro_period_auc,
        evidence.weighted_period_auc,
    ):
        assert summary.n_valid == 0
        assert summary.mean is None
        assert summary.std is None
        assert summary.p95 is None
        assert summary.max is None
