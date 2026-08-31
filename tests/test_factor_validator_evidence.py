from dataclasses import replace

import pytest

from std0_quant.research.factors.contracts import (
    FactorResult,
    FactorValidationEvidenceBundle,
    ValidationStatus,
    factor_validation_evidence_bundle_hash,
)
from std0_quant.research.factors.validator import (
    ValidationPolicy,
    validate_factor_result,
    validate_factor_result_with_evidence,
)


def result(**overrides):
    values = {
        "factor_id": "test_factor",
        "factor_version": "1",
        "n_total": 240,
        "n_eligible": 220,
        "coverage": 220 / 240,
        "missing_rate": 0.05,
        "oos_auc": 0.61,
        "macro_period_auc": 0.58,
        "weighted_period_auc": 0.59,
        "brier": 0.20,
        "logloss": 0.59,
        "ece": 0.04,
        "n_folds": 5,
        "fold_positive_fraction": 0.80,
        "sign_consistency": 0.80,
        "temporal_integrity": ValidationStatus.PASS,
        "research_validation_status": ValidationStatus.PENDING,
        "period_metrics": (),
        "regime_metrics": (),
        "artifact_hash": "a" * 64,
        "run_id": "research-1",
    }
    values.update(overrides)
    return FactorResult(**values)


def policy():
    return ValidationPolicy(
        policy_id="generic-factor-research",
        version="1",
        min_n_eligible=100,
        min_coverage=0.80,
        max_missing_rate=0.20,
        min_oos_auc=0.55,
        min_macro_period_auc=0.53,
        min_weighted_period_auc=0.53,
        min_n_folds=3,
        min_fold_positive_fraction=0.60,
        min_sign_consistency=0.60,
        max_brier=0.25,
        max_logloss=0.70,
        max_ece=0.10,
    )


def bundle(**overrides):
    values = {
        "factor_id": "test_factor",
        "factor_version": "1",
        "factor_spec_hash": "spec-hash",
        "factor_result_artifact_hash": "a" * 64,
        "factor_result_run_id": "research-1",
        "oos_predictions_hash": "oos-hash",
        "oos_run_id": "research-1",
        "null_control_evidence_hash": "null-hash",
        "null_control_run_id": "null-1",
        "baseline_relative_evidence_hash": "baseline-hash",
        "baseline_relative_run_id": "baseline-1",
    }
    values.update(overrides)
    return FactorValidationEvidenceBundle(**values)


def test_evidence_aware_validator_binds_bundle_hash_without_changing_decision():
    plain = validate_factor_result(result(), policy())
    bound = validate_factor_result_with_evidence(
        result(),
        policy(),
        bundle(),
    )

    assert bound.research_validation_status == plain.research_validation_status
    assert bound.temporal_integrity == plain.temporal_integrity
    assert bound.reasons == plain.reasons
    assert bound.policy_hash == plain.policy_hash
    assert (
        bound.validation_evidence_bundle_hash
        == factor_validation_evidence_bundle_hash(bundle())
    )


def test_existing_validator_remains_unbound():
    decision = validate_factor_result(result(), policy())
    assert decision.validation_evidence_bundle_hash is None


@pytest.mark.parametrize(
    "override,match",
    [
        ({"factor_id": "other"}, "factor"),
        ({"factor_version": "2"}, "factor"),
        ({"factor_result_artifact_hash": "tampered"}, "artifact"),
        ({"factor_result_run_id": "other-run"}, "run"),
    ],
)
def test_evidence_aware_validator_rejects_bundle_result_mismatch(
    override,
    match,
):
    with pytest.raises(ValueError, match=match):
        validate_factor_result_with_evidence(
            result(),
            policy(),
            bundle(**override),
        )


def test_evidence_aware_validator_does_not_mutate_inputs():
    source_result = result()
    source_bundle = bundle()
    before_result = source_result.to_json()
    before_bundle = source_bundle.to_json()

    validate_factor_result_with_evidence(
        source_result,
        policy(),
        source_bundle,
    )

    assert source_result.to_json() == before_result
    assert source_bundle.to_json() == before_bundle
