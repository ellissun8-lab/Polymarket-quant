from dataclasses import replace

import pytest

from std0_quant.research.factors.contracts import (
    FACTOR_BASELINE_RELATIVE_EVIDENCE_SCHEMA_V1,
    FactorBaselineRelativeEvidence,
    FactorSpec,
    factor_baseline_relative_evidence_hash,
)
from std0_quant.research.factors.evaluator import (
    evaluate_factor_with_oos_predictions,
)
from std0_quant.research.factors.baseline_evidence import (
    run_factor_baseline_relative_evidence,
)


def spec():
    return FactorSpec(
        factor_id="baseline-factor",
        version="v1",
        hypothesis="Synthetic signal carries incremental information.",
        inputs=("signal",),
        transform="identity",
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
        created_at="2026-08-31T00:00:00+00:00",
    )


def rows():
    out = []
    base = 1_760_000_000_000
    for week in range(6):
        for i in range(40):
            signal = -1.0 if i < 20 else 1.0
            out.append(
                {
                    "condition_id": f"{week}-{i}",
                    "iso_week": f"W{week}",
                    "prediction_ts_ms": base + week * 604800000 + i * 1000,
                    "feature_cutoff_ms": base + week * 604800000 + i * 1000 - 1,
                    "model_eligible": True,
                    "signal": signal,
                    "y30": int(signal > 0),
                }
            )
    return out


def evaluate(run_id="eval-baseline-1"):
    return evaluate_factor_with_oos_predictions(
        spec(),
        rows(),
        min_train_periods=3,
        min_test_n=20,
        run_id=run_id,
    )


def evidence(*, source_run_id="eval-baseline-1", evidence_run_id="baseline-1"):
    result, source = evaluate(source_run_id)
    return run_factor_baseline_relative_evidence(
        result,
        source,
        run_id=evidence_run_id,
    )


def test_baseline_relative_schema_and_source_binding():
    result, source = evaluate()
    row = run_factor_baseline_relative_evidence(
        result,
        source,
        run_id="baseline-1",
    )

    assert isinstance(row, FactorBaselineRelativeEvidence)
    assert (
        row.schema_version
        == FACTOR_BASELINE_RELATIVE_EVIDENCE_SCHEMA_V1
        == "factor_baseline_relative_evidence_v1"
    )
    assert row.factor_id == result.factor_id
    assert row.factor_version == result.factor_version
    assert row.factor_spec_hash == source.factor_spec_hash
    assert row.factor_result_artifact_hash == result.artifact_hash
    assert row.source_run_id == result.run_id == source.run_id
    assert row.baseline_method == "train_prevalence_per_fold_v1"
    assert row.n_predictions == 120
    assert row.n_folds == 3


def test_train_prevalence_baseline_is_auditable_per_fold():
    row = evidence()

    assert len(row.fold_baselines) == 3
    assert [fold.fold_id for fold in row.fold_baselines] == [1, 2, 3]
    assert [fold.test_period for fold in row.fold_baselines] == ["W3", "W4", "W5"]
    assert all(fold.baseline_probability == pytest.approx(0.5) for fold in row.fold_baselines)


def test_strong_candidate_improves_over_train_prevalence_baseline():
    row = evidence()

    assert row.delta_brier > 0
    assert row.delta_logloss > 0
    assert row.delta_macro_period_auc > 0
    assert row.pct_folds_brier_improved == pytest.approx(1.0)
    assert row.pct_folds_logloss_improved == pytest.approx(1.0)


def test_baseline_relative_evidence_has_no_embedded_pass_fail():
    row = evidence()

    assert not hasattr(row, "status")
    assert not hasattr(row, "research_validation_status")


def test_baseline_relative_hash_excludes_run_provenance():
    first = evidence(
        source_run_id="eval-run-1",
        evidence_run_id="baseline-run-1",
    )
    second = evidence(
        source_run_id="eval-run-2",
        evidence_run_id="baseline-run-2",
    )

    assert (
        factor_baseline_relative_evidence_hash(first)
        == factor_baseline_relative_evidence_hash(second)
    )


def test_baseline_evidence_requires_matching_source_run():
    result, source = evaluate("eval-run-1")
    mismatched = replace(source, run_id="eval-run-2")

    with pytest.raises(ValueError, match="run"):
        run_factor_baseline_relative_evidence(
            result,
            mismatched,
            run_id="baseline-bad",
        )


def test_baseline_evidence_requires_train_positive_rate():
    result, source = evaluate()
    stripped = replace(
        result,
        period_metrics=tuple(
            {
                key: value
                for key, value in fold.items()
                if key != "train_positive_rate"
            }
            for fold in result.period_metrics
        ),
    )

    with pytest.raises(ValueError, match="train_positive_rate"):
        run_factor_baseline_relative_evidence(
            stripped,
            source,
            run_id="baseline-missing-rate",
        )


def test_baseline_evidence_does_not_mutate_sources():
    result, source = evaluate()
    result_before = result.to_json()
    source_before = source.to_json()

    run_factor_baseline_relative_evidence(
        result,
        source,
        run_id="baseline-no-mutation",
    )

    assert result.to_json() == result_before
    assert source.to_json() == source_before
