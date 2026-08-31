from dataclasses import FrozenInstanceError, replace

import pytest

from std0_quant.research.factors.baseline_evidence import (
    run_factor_baseline_relative_evidence,
)
from std0_quant.research.factors.contracts import (
    FACTOR_VALIDATION_EVIDENCE_BUNDLE_SCHEMA_V1,
    FactorSpec,
    FactorValidationEvidenceBundle,
    factor_validation_evidence_bundle_hash,
)
from std0_quant.research.factors.evaluator import (
    evaluate_factor_with_oos_predictions,
)
from std0_quant.research.factors.null_control import (
    run_factor_null_control,
)
from std0_quant.research.factors.validation_evidence import (
    build_validation_evidence_bundle,
)


def spec():
    return FactorSpec(
        factor_id="bundle-factor",
        version="v1",
        hypothesis="Synthetic factor for evidence-bundle tests.",
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


def chain(suffix="1"):
    result, oos = evaluate_factor_with_oos_predictions(
        spec(),
        rows(),
        min_train_periods=3,
        min_test_n=20,
        run_id=f"eval-{suffix}",
    )
    null = run_factor_null_control(
        oos,
        n_shuffles=10,
        seed=12345,
        run_id=f"null-{suffix}",
    )
    baseline = run_factor_baseline_relative_evidence(
        result,
        oos,
        run_id=f"baseline-{suffix}",
    )
    return result, oos, null, baseline


def bundle(suffix="1"):
    return build_validation_evidence_bundle(*chain(suffix))


def test_validation_evidence_bundle_schema_and_bindings():
    result, oos, null, baseline = chain()
    row = build_validation_evidence_bundle(
        result,
        oos,
        null,
        baseline,
    )

    assert isinstance(row, FactorValidationEvidenceBundle)
    assert (
        row.schema_version
        == FACTOR_VALIDATION_EVIDENCE_BUNDLE_SCHEMA_V1
        == "factor_validation_evidence_bundle_v1"
    )
    assert row.factor_id == result.factor_id
    assert row.factor_version == result.factor_version
    assert row.factor_spec_hash == oos.factor_spec_hash
    assert row.factor_result_artifact_hash == result.artifact_hash
    assert row.factor_result_run_id == result.run_id
    assert row.oos_run_id == oos.run_id
    assert row.null_control_run_id == null.run_id
    assert row.baseline_relative_run_id == baseline.run_id


def test_validation_evidence_bundle_is_frozen_and_round_trips():
    row = bundle()

    with pytest.raises(FrozenInstanceError):
        row.factor_id = "changed"

    assert FactorValidationEvidenceBundle.from_json(row.to_json()) == row


def test_validation_evidence_bundle_hash_excludes_run_provenance():
    first = bundle("run-1")
    second = bundle("run-2")

    assert (
        factor_validation_evidence_bundle_hash(first)
        == factor_validation_evidence_bundle_hash(second)
    )


def test_bundle_rejects_null_control_oos_hash_mismatch():
    result, oos, null, baseline = chain()
    bad = replace(null, oos_predictions_hash="tampered")

    with pytest.raises(ValueError, match="null.*OOS|OOS.*null"):
        build_validation_evidence_bundle(
            result,
            oos,
            bad,
            baseline,
        )


def test_bundle_rejects_baseline_result_hash_mismatch():
    result, oos, null, baseline = chain()
    bad = replace(
        baseline,
        factor_result_artifact_hash="tampered",
    )

    with pytest.raises(ValueError, match="baseline.*result|result.*baseline"):
        build_validation_evidence_bundle(
            result,
            oos,
            null,
            bad,
        )


def test_bundle_rejects_source_run_mismatch():
    result, oos, null, baseline = chain()
    bad = replace(
        baseline,
        source_run_id="different-eval-run",
    )

    with pytest.raises(ValueError, match="run"):
        build_validation_evidence_bundle(
            result,
            oos,
            null,
            bad,
        )


def test_validation_evidence_bundle_has_no_decision_or_thresholds():
    row = bundle()

    assert not hasattr(row, "status")
    assert not hasattr(row, "research_validation_status")
    assert not hasattr(row, "thresholds")
    assert not hasattr(row, "policy_id")
