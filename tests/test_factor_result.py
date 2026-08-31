from dataclasses import FrozenInstanceError
import pytest

from std0_quant.research.factors.contracts import FactorResult, ValidationStatus


def make_result(**overrides):
    values = {
        "factor_id": "btc_ret_3s",
        "factor_version": "1",
        "n_total": 1000,
        "n_eligible": 900,
        "coverage": 0.9,
        "missing_rate": 0.1,
        "oos_auc": 0.56,
        "macro_period_auc": 0.55,
        "weighted_period_auc": 0.56,
        "brier": 0.21,
        "logloss": 0.61,
        "ece": 0.03,
        "n_folds": 8,
        "fold_positive_fraction": 0.75,
        "sign_consistency": 0.875,
        "temporal_integrity": ValidationStatus.PASS,
        "research_validation_status": ValidationStatus.PASS,
        "period_metrics": (),
        "regime_metrics": (),
        "artifact_hash": "a" * 64,
        "run_id": "factor-eval-1",
    }
    values.update(overrides)
    return FactorResult(**values)


def test_factor_result_is_frozen():
    result = make_result()
    with pytest.raises(FrozenInstanceError):
        result.n_total = 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("n_total", -1),
        ("n_eligible", -1),
        ("n_folds", -1),
        ("coverage", -0.01),
        ("coverage", 1.01),
        ("missing_rate", -0.01),
        ("missing_rate", 1.01),
        ("fold_positive_fraction", -0.01),
        ("fold_positive_fraction", 1.01),
        ("sign_consistency", -0.01),
        ("sign_consistency", 1.01),
    ],
)
def test_factor_result_rejects_invalid_ranges(field, value):
    with pytest.raises(ValueError):
        make_result(**{field: value})


def test_factor_result_rejects_eligible_above_total():
    with pytest.raises(ValueError):
        make_result(n_eligible=1001)


def test_factor_result_requires_pass_temporal_integrity_for_research_pass():
    with pytest.raises(ValueError):
        make_result(
            temporal_integrity=ValidationStatus.FAIL,
            research_validation_status=ValidationStatus.PASS,
        )


def test_factor_result_requires_artifact_hash_and_run_id():
    with pytest.raises(ValueError):
        make_result(artifact_hash="")
    with pytest.raises(ValueError):
        make_result(run_id="")
