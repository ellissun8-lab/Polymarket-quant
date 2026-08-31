from std0_quant.research.factors.contracts import (
    FactorSpec,
    FactorStatus,
    ValidationStatus,
    factor_spec_hash,
)
from std0_quant.research.factors.evaluator import evaluate_factor
from std0_quant.research.factors.registry import (
    FactorRegistryRecord,
    promote_factor,
)
from std0_quant.research.factors.validation_bridge import (
    promotion_evidence_from_decision,
)
from std0_quant.research.factors.validator import (
    ValidationPolicy,
    validate_factor_result,
)


def spec():
    return FactorSpec(
        factor_id="validation_e2e_signal",
        version="1",
        hypothesis="Synthetic signal contains predictive information.",
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
        created_at="2026-08-30T00:00:00+00:00",
    )


def rows():
    out = []
    base = 1_760_000_000_000
    for week in range(6):
        for i in range(40):
            signal = -1.0 if i < 20 else 1.0
            ts = base + week * 604800000 + i * 1000
            out.append(
                {
                    "condition_id": f"{week}-{i}",
                    "iso_week": f"W{week}",
                    "prediction_ts_ms": ts,
                    "feature_cutoff_ms": ts - 1,
                    "model_eligible": True,
                    "signal": signal,
                    "y30": int(signal > 0),
                }
            )
    return out


def policy(**overrides):
    values = {
        "policy_id": "validation-e2e-policy",
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


def candidate(factor_spec):
    return FactorRegistryRecord(
        factor_id=factor_spec.factor_id,
        factor_version=factor_spec.version,
        definition_hash=factor_spec_hash(factor_spec),
        status=FactorStatus.CANDIDATE,
        created_by="test",
        created_at="2026-08-30T00:00:00+00:00",
    )


def test_evaluate_validate_and_promote_to_validated():
    factor_spec = spec()
    result = evaluate_factor(
        factor_spec,
        rows(),
        min_train_periods=3,
        min_test_n=20,
        run_id="validation-e2e-pass",
    )

    assert result.research_validation_status == ValidationStatus.PENDING

    decision = validate_factor_result(result, policy())
    assert decision.research_validation_status == ValidationStatus.PASS

    evidence = promotion_evidence_from_decision(
        decision,
        decided_at="2026-08-30T01:00:00+00:00",
    )
    promoted = promote_factor(
        candidate(factor_spec),
        FactorStatus.VALIDATED,
        evidence,
    )

    assert promoted.status == FactorStatus.VALIDATED
    transition = promoted.transitions[-1]
    assert transition.research_validation_status == ValidationStatus.PASS
    assert transition.research_policy_hash == decision.policy_hash
    assert transition.research_validation_reasons == ()


def test_evaluate_validate_and_reject_on_failed_policy_gate():
    factor_spec = spec()
    result = evaluate_factor(
        factor_spec,
        rows(),
        min_train_periods=3,
        min_test_n=20,
        run_id="validation-e2e-fail",
    )

    decision = validate_factor_result(
        result,
        policy(min_n_eligible=241),
    )

    assert decision.research_validation_status == ValidationStatus.FAIL
    assert "N_ELIGIBLE_BELOW_MIN" in decision.reasons

    evidence = promotion_evidence_from_decision(
        decision,
        decided_at="2026-08-30T01:00:00+00:00",
    )
    rejected = promote_factor(
        candidate(factor_spec),
        FactorStatus.REJECTED,
        evidence,
    )

    assert rejected.status == FactorStatus.REJECTED
    transition = rejected.transitions[-1]
    assert transition.research_validation_status == ValidationStatus.FAIL
    assert transition.research_policy_hash == decision.policy_hash
    assert "N_ELIGIBLE_BELOW_MIN" in transition.research_validation_reasons
