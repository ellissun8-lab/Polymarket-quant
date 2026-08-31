from dataclasses import replace

import pytest

import std0_quant.research.factors.validator as validator_module
from std0_quant.research.factors.contracts import (
    BaselineFoldEvidence,
    FactorBaselineRelativeEvidence,
    FactorNullControlEvidence,
    FactorResult,
    FactorValidationEvidenceBundle,
    NullControlMetricSummary,
    ValidationStatus,
    factor_baseline_relative_evidence_hash,
    factor_null_control_evidence_hash,
    factor_validation_evidence_bundle_hash,
)


def result(**overrides):
    values = {
        "factor_id": "test_factor",
        "factor_version": "1",
        "n_total": 160,
        "n_eligible": 150,
        "coverage": 0.9375,
        "missing_rate": 0.0625,
        "oos_auc": 0.60,
        "macro_period_auc": 0.59,
        "weighted_period_auc": 0.59,
        "brier": 0.20,
        "logloss": 0.58,
        "ece": 0.12,
        "n_folds": 3,
        "fold_positive_fraction": 1.0,
        "sign_consistency": 1.0,
        "temporal_integrity": ValidationStatus.PASS,
        "research_validation_status": ValidationStatus.PENDING,
        "period_metrics": (),
        "regime_metrics": (),
        "artifact_hash": "a" * 64,
        "run_id": "research-1",
    }
    values.update(overrides)
    return FactorResult(**values)


def summary(p95, *, mean=0.50, maximum=0.60):
    return NullControlMetricSummary(
        n_valid=499,
        mean=mean,
        std=0.01,
        p95=p95,
        max=max(maximum, p95),
    )


def null_evidence(**overrides):
    values = {
        "factor_id": "test_factor",
        "factor_version": "1",
        "factor_spec_hash": "spec-hash",
        "oos_predictions_hash": "oos-hash",
        "oos_run_id": "research-1",
        "method": "within_period_label_permutation_fixed_oos_v1",
        "seed": 20260824,
        "n_shuffles": 500,
        "n_predictions": 150,
        "pooled_auc": summary(0.80, mean=0.65, maximum=0.85),
        "macro_period_auc": summary(0.52),
        "weighted_period_auc": summary(0.51),
        "run_id": "null-1",
    }
    values.update(overrides)
    return FactorNullControlEvidence(**values)


def fold(fold_id):
    return BaselineFoldEvidence(
        fold_id=fold_id,
        test_period=f"P{fold_id}",
        train_n=100,
        test_n=50,
        baseline_probability=0.50,
        candidate_brier=0.20,
        baseline_brier=0.25,
        candidate_logloss=0.58,
        baseline_logloss=0.69,
    )


def baseline_evidence(**overrides):
    values = {
        "factor_id": "test_factor",
        "factor_version": "1",
        "factor_spec_hash": "spec-hash",
        "factor_result_artifact_hash": "a" * 64,
        "oos_predictions_hash": "oos-hash",
        "source_run_id": "research-1",
        "baseline_method": "train_prevalence_per_fold_v1",
        "n_predictions": 150,
        "n_folds": 3,
        "candidate_brier": 0.20,
        "baseline_brier": 0.25,
        "delta_brier": 0.05,
        "candidate_logloss": 0.58,
        "baseline_logloss": 0.69,
        "delta_logloss": 0.11,
        "candidate_macro_period_auc": 0.60,
        "baseline_macro_period_auc": 0.50,
        "delta_macro_period_auc": 0.10,
        "pct_folds_brier_improved": 0.80,
        "pct_folds_logloss_improved": 0.70,
        "fold_baselines": (fold(1), fold(2), fold(3)),
        "run_id": "baseline-1",
    }
    values.update(overrides)
    return FactorBaselineRelativeEvidence(**values)


def bundle(null=None, baseline=None, **overrides):
    null = null_evidence() if null is None else null
    baseline = baseline_evidence() if baseline is None else baseline
    values = {
        "factor_id": "test_factor",
        "factor_version": "1",
        "factor_spec_hash": "spec-hash",
        "factor_result_artifact_hash": "a" * 64,
        "factor_result_run_id": "research-1",
        "oos_predictions_hash": "oos-hash",
        "oos_run_id": "research-1",
        "null_control_evidence_hash": factor_null_control_evidence_hash(null),
        "null_control_run_id": null.run_id,
        "baseline_relative_evidence_hash": factor_baseline_relative_evidence_hash(baseline),
        "baseline_relative_run_id": baseline.run_id,
    }
    values.update(overrides)
    return FactorValidationEvidenceBundle(**values)


def policy():
    return validator_module.EvidenceValidationPolicy(
        policy_id="generic-factor-evidence",
        version="1",
        required_null_method="within_period_label_permutation_fixed_oos_v1",
        min_null_shuffles=500,
        max_null_macro_period_auc_p95=0.55,
        max_null_weighted_period_auc_p95=0.55,
        required_baseline_method="train_prevalence_per_fold_v1",
        min_baseline_stability_fraction=0.60,
    )


def validate(null=None, baseline=None, source_result=None, source_bundle=None):
    null = null_evidence() if null is None else null
    baseline = baseline_evidence() if baseline is None else baseline
    source_result = result() if source_result is None else source_result
    source_bundle = bundle(null, baseline) if source_bundle is None else source_bundle
    return validator_module.validate_factor_result_with_full_evidence(
        source_result,
        policy(),
        source_bundle,
        null,
        baseline,
    )


def test_supported_evidence_policy_passes_and_binds_bundle_hash():
    source_null = null_evidence()
    source_baseline = baseline_evidence()
    source_bundle = bundle(source_null, source_baseline)

    decision = validate(
        source_null,
        source_baseline,
        source_bundle=source_bundle,
    )

    assert decision.research_validation_status == ValidationStatus.PASS
    assert decision.reasons == ()
    assert decision.validation_evidence_bundle_hash == factor_validation_evidence_bundle_hash(source_bundle)


def test_pooled_null_auc_is_diagnostic_only():
    source_null = null_evidence(
        pooled_auc=summary(0.99, mean=0.90, maximum=0.99),
    )

    assert validate(null=source_null).research_validation_status == ValidationStatus.PASS


@pytest.mark.parametrize(
    "source_null,reason",
    [
        (null_evidence(n_shuffles=499), "NULL_SHUFFLES_BELOW_MIN"),
        (null_evidence(macro_period_auc=summary(0.55)), "NULL_MACRO_PERIOD_AUC_P95_NOT_BELOW_MAX"),
        (null_evidence(weighted_period_auc=summary(0.55)), "NULL_WEIGHTED_PERIOD_AUC_P95_NOT_BELOW_MAX"),
        (null_evidence(method="wrong-method"), "NULL_CONTROL_METHOD_MISMATCH"),
    ],
)
def test_null_hard_gates_fail(source_null, reason):
    decision = validate(null=source_null)

    assert decision.research_validation_status == ValidationStatus.FAIL
    assert reason in decision.reasons


@pytest.mark.parametrize(
    "source_baseline,reason",
    [
        (baseline_evidence(delta_brier=0.0), "DELTA_BRIER_NOT_POSITIVE"),
        (baseline_evidence(delta_logloss=0.0), "DELTA_LOGLOSS_NOT_POSITIVE"),
        (baseline_evidence(delta_macro_period_auc=0.0), "DELTA_MACRO_PERIOD_AUC_NOT_POSITIVE"),
        (
            baseline_evidence(
                pct_folds_brier_improved=0.59,
                pct_folds_logloss_improved=0.40,
            ),
            "BASELINE_STABILITY_BELOW_MIN",
        ),
        (
            baseline_evidence(baseline_method="wrong-method"),
            "BASELINE_METHOD_MISMATCH",
        ),
    ],
)
def test_baseline_hard_gates_fail(source_baseline, reason):
    decision = validate(baseline=source_baseline)

    assert decision.research_validation_status == ValidationStatus.FAIL
    assert reason in decision.reasons


def test_temporal_integrity_remains_hard_gate():
    decision = validate(
        source_result=result(temporal_integrity=ValidationStatus.FAIL),
    )

    assert decision.research_validation_status == ValidationStatus.FAIL
    assert "TEMPORAL_INTEGRITY_NOT_PASS" in decision.reasons


def test_evidence_hash_mismatch_fails_closed():
    source_null = null_evidence()
    source_baseline = baseline_evidence()
    source_bundle = bundle(source_null, source_baseline)
    changed_null = replace(
        source_null,
        macro_period_auc=summary(0.53),
    )

    with pytest.raises(ValueError, match="null.*hash|hash.*null"):
        validate(
            changed_null,
            source_baseline,
            source_bundle=source_bundle,
        )


def test_evidence_run_mismatch_fails_closed():
    source_null = null_evidence()
    source_baseline = baseline_evidence()
    source_bundle = bundle(source_null, source_baseline)
    changed_null = replace(source_null, run_id="other-null-run")

    with pytest.raises(ValueError, match="run"):
        validate(
            changed_null,
            source_baseline,
            source_bundle=source_bundle,
        )
