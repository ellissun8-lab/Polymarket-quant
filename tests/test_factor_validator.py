from dataclasses import FrozenInstanceError

import pytest

from std0_quant.research.factors.contracts import FactorResult, ValidationStatus
from std0_quant.research.factors.validator import ValidationPolicy, validate_factor_result


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


def policy(**overrides):
    values = {
        "policy_id": "generic-factor-research",
        "version": "1",
        "min_n_eligible": 100,
        "min_coverage": 0.80,
        "max_missing_rate": 0.20,
        "min_oos_auc": 0.55,
        "min_macro_period_auc": 0.53,
        "min_weighted_period_auc": 0.53,
        "min_n_folds": 3,
        "min_fold_positive_fraction": 0.60,
        "min_sign_consistency": 0.60,
        "max_brier": 0.25,
        "max_logloss": 0.70,
        "max_ece": 0.10,
    }
    values.update(overrides)
    return ValidationPolicy(**values)


def test_validation_policy_is_frozen():
    row = policy()
    with pytest.raises(FrozenInstanceError):
        row.min_oos_auc = 0.99


def test_result_passes_when_all_explicit_policy_gates_pass():
    decision = validate_factor_result(result(), policy())

    assert decision.research_validation_status == ValidationStatus.PASS
    assert decision.temporal_integrity == ValidationStatus.PASS
    assert decision.reasons == ()
    assert decision.factor_id == "test_factor"
    assert decision.factor_version == "1"
    assert decision.research_artifact_hash == "a" * 64
    assert decision.research_run_id == "research-1"
    assert len(decision.policy_hash) == 64


@pytest.mark.parametrize(
    "result_override,reason",
    [
        ({"n_eligible": 99}, "N_ELIGIBLE_BELOW_MIN"),
        ({"coverage": 0.79}, "COVERAGE_BELOW_MIN"),
        ({"missing_rate": 0.21}, "MISSING_RATE_ABOVE_MAX"),
        ({"oos_auc": 0.54}, "OOS_AUC_BELOW_MIN"),
        ({"macro_period_auc": 0.52}, "MACRO_PERIOD_AUC_BELOW_MIN"),
        ({"weighted_period_auc": 0.52}, "WEIGHTED_PERIOD_AUC_BELOW_MIN"),
        ({"n_folds": 2}, "N_FOLDS_BELOW_MIN"),
        ({"fold_positive_fraction": 0.59}, "FOLD_POSITIVE_FRACTION_BELOW_MIN"),
        ({"sign_consistency": 0.59}, "SIGN_CONSISTENCY_BELOW_MIN"),
        ({"brier": 0.26}, "BRIER_ABOVE_MAX"),
        ({"logloss": 0.71}, "LOGLOSS_ABOVE_MAX"),
        ({"ece": 0.11}, "ECE_ABOVE_MAX"),
    ],
)
def test_each_failed_gate_produces_research_fail(result_override, reason):
    decision = validate_factor_result(result(**result_override), policy())

    assert decision.research_validation_status == ValidationStatus.FAIL
    assert reason in decision.reasons


@pytest.mark.parametrize(
    "field,reason",
    [
        ("oos_auc", "OOS_AUC_MISSING"),
        ("macro_period_auc", "MACRO_PERIOD_AUC_MISSING"),
        ("weighted_period_auc", "WEIGHTED_PERIOD_AUC_MISSING"),
        ("brier", "BRIER_MISSING"),
        ("logloss", "LOGLOSS_MISSING"),
        ("ece", "ECE_MISSING"),
    ],
)
def test_required_metric_missing_fails_closed(field, reason):
    decision = validate_factor_result(result(**{field: None}), policy())

    assert decision.research_validation_status == ValidationStatus.FAIL
    assert reason in decision.reasons


def test_temporal_integrity_not_pass_fails_closed():
    decision = validate_factor_result(
        result(temporal_integrity=ValidationStatus.FAIL),
        policy(),
    )

    assert decision.research_validation_status == ValidationStatus.FAIL
    assert "TEMPORAL_INTEGRITY_NOT_PASS" in decision.reasons


def test_validator_refuses_to_overwrite_non_pending_research_status():
    with pytest.raises(ValueError, match="PENDING"):
        validate_factor_result(
            result(research_validation_status=ValidationStatus.PASS),
            policy(),
        )


def test_policy_hash_is_deterministic_and_semantic():
    first = validate_factor_result(result(), policy())
    second = validate_factor_result(result(), policy())
    changed = validate_factor_result(
        result(),
        policy(min_oos_auc=0.56),
    )

    assert first.policy_hash == second.policy_hash
    assert first.policy_hash != changed.policy_hash
