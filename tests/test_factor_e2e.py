import pytest

from std0_quant.research.factors.contracts import (
    FactorSpec,
    FactorStatus,
    ValidationStatus,
    factor_spec_hash,
)
from std0_quant.research.factors.evaluator import evaluate_factor
from std0_quant.research.factors.registry import (
    FactorRegistryRecord,
    PromotionEvidence,
    load_registry_record,
    promote_factor,
    write_registry_record,
)


def _spec():
    return FactorSpec(
        factor_id="e2e_signal",
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
        created_by="human",
        created_at="2026-08-30T00:00:00+00:00",
    )


def _rows():
    rows = []
    base = 1_760_000_000_000
    for week in range(6):
        for i in range(40):
            signal = -1.0 if i < 20 else 1.0
            ts = base + week * 604800000 + i * 1000
            rows.append(
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
    return rows


def test_human_triggered_factor_e2e_to_candidate_registry(tmp_path):
    spec = _spec()
    result = evaluate_factor(
        spec,
        _rows(),
        min_train_periods=3,
        min_test_n=20,
        run_id="human-e2e-1",
    )

    assert result.temporal_integrity == ValidationStatus.PASS
    assert result.research_validation_status == ValidationStatus.PENDING
    assert result.oos_auc > 0.9

    record = FactorRegistryRecord(
        factor_id=spec.factor_id,
        factor_version=spec.version,
        definition_hash=factor_spec_hash(spec),
        status=FactorStatus.CANDIDATE,
        created_by="human",
        created_at="2026-08-30T00:00:00+00:00",
    )

    path = tmp_path / "factor_registry.json"
    write_registry_record(path, record)
    loaded = load_registry_record(path)

    assert loaded == record
    assert loaded.factor_id == result.factor_id
    assert loaded.factor_version == result.factor_version
    assert loaded.definition_hash == factor_spec_hash(spec)
    assert loaded.status == FactorStatus.CANDIDATE
    assert loaded.transitions == ()


def test_pending_evaluation_cannot_promote_candidate():
    spec = _spec()
    result = evaluate_factor(
        spec,
        _rows(),
        min_train_periods=3,
        min_test_n=20,
        run_id="human-e2e-pending",
    )

    record = FactorRegistryRecord(
        factor_id=spec.factor_id,
        factor_version=spec.version,
        definition_hash=factor_spec_hash(spec),
        status=FactorStatus.CANDIDATE,
        created_by="human",
        created_at="2026-08-30T00:00:00+00:00",
    )

    evidence = PromotionEvidence(
        research_validation_status=result.research_validation_status,
        temporal_integrity=result.temporal_integrity,
        research_artifact_hash=result.artifact_hash,
        research_run_id=result.run_id,
        decided_at="2026-08-30T01:00:00+00:00",
    )

    with pytest.raises(ValueError):
        promote_factor(record, FactorStatus.VALIDATED, evidence)
