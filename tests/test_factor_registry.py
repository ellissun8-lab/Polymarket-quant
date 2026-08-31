from dataclasses import FrozenInstanceError
import pytest

from std0_quant.research.factors.contracts import FactorStatus, ValidationStatus
from std0_quant.research.factors.registry import (
    FactorRegistryRecord,
    PromotionEvidence,
    promote_factor,
)


def record(status=FactorStatus.CANDIDATE):
    return FactorRegistryRecord(
        factor_id="btc_ret_3s",
        factor_version="1",
        definition_hash="a" * 64,
        status=status,
        created_by="human",
        created_at="2026-08-30T00:00:00+00:00",
    )


def evidence(research=ValidationStatus.PASS, temporal=ValidationStatus.PASS, execution=None):
    return PromotionEvidence(
        research_validation_status=research,
        temporal_integrity=temporal,
        research_artifact_hash="b" * 64,
        research_run_id="research-1",
        execution_validation_status=execution,
        execution_artifact_hash="c" * 64 if execution is not None else None,
        execution_run_id="execution-1" if execution is not None else None,
        decided_at="2026-08-30T01:00:00+00:00",
    )


def test_registry_record_is_frozen():
    row = record()
    with pytest.raises(FrozenInstanceError):
        row.status = FactorStatus.VALIDATED


def test_candidate_to_validated_requires_research_and_temporal_pass():
    out = promote_factor(record(), FactorStatus.VALIDATED, evidence())
    assert out.status == FactorStatus.VALIDATED
    assert len(out.transitions) == 1
    assert out.transitions[0].status_before == FactorStatus.CANDIDATE
    assert out.transitions[0].status_after == FactorStatus.VALIDATED


@pytest.mark.parametrize(
    "research,temporal",
    [
        (ValidationStatus.FAIL, ValidationStatus.PASS),
        (ValidationStatus.PASS, ValidationStatus.FAIL),
        (ValidationStatus.PENDING, ValidationStatus.PASS),
    ],
)
def test_candidate_to_validated_fails_closed(research, temporal):
    with pytest.raises(ValueError):
        promote_factor(record(), FactorStatus.VALIDATED, evidence(research, temporal))


def test_candidate_to_rejected_requires_research_fail():
    out = promote_factor(
        record(),
        FactorStatus.REJECTED,
        evidence(research=ValidationStatus.FAIL),
    )
    assert out.status == FactorStatus.REJECTED


def test_candidate_cannot_jump_to_production_eligible():
    with pytest.raises(ValueError):
        promote_factor(
            record(),
            FactorStatus.PRODUCTION_ELIGIBLE,
            evidence(execution=ValidationStatus.PASS),
        )


def test_validated_to_production_requires_execution_pass_and_evidence():
    out = promote_factor(
        record(FactorStatus.VALIDATED),
        FactorStatus.PRODUCTION_ELIGIBLE,
        evidence(execution=ValidationStatus.PASS),
    )
    assert out.status == FactorStatus.PRODUCTION_ELIGIBLE


def test_validated_to_production_fails_without_execution_pass():
    with pytest.raises(ValueError):
        promote_factor(
            record(FactorStatus.VALIDATED),
            FactorStatus.PRODUCTION_ELIGIBLE,
            evidence(),
        )


def test_rejected_cannot_be_promoted_in_place():
    with pytest.raises(ValueError):
        promote_factor(record(FactorStatus.REJECTED), FactorStatus.VALIDATED, evidence())


def test_deprecation_preserves_transition_audit():
    out = promote_factor(
        record(FactorStatus.VALIDATED),
        FactorStatus.DEPRECATED,
        evidence(),
    )
    assert out.status == FactorStatus.DEPRECATED
    assert out.transitions[-1].status_after == FactorStatus.DEPRECATED
