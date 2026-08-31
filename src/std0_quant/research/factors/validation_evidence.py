"""Deterministic aggregation of Factor Factory validation evidence."""

from __future__ import annotations

from .contracts import (
    FactorBaselineRelativeEvidence,
    FactorNullControlEvidence,
    FactorOOSPredictionArtifact,
    FactorResult,
    FactorValidationEvidenceBundle,
    factor_baseline_relative_evidence_hash,
    factor_null_control_evidence_hash,
    factor_oos_predictions_hash,
)


def build_validation_evidence_bundle(
    result: FactorResult,
    oos: FactorOOSPredictionArtifact,
    null_control: FactorNullControlEvidence,
    baseline: FactorBaselineRelativeEvidence,
) -> FactorValidationEvidenceBundle:
    if not isinstance(result, FactorResult):
        raise ValueError("result must be FactorResult")
    if not isinstance(oos, FactorOOSPredictionArtifact):
        raise ValueError("oos must be FactorOOSPredictionArtifact")
    if not isinstance(null_control, FactorNullControlEvidence):
        raise ValueError("null_control must be FactorNullControlEvidence")
    if not isinstance(baseline, FactorBaselineRelativeEvidence):
        raise ValueError("baseline must be FactorBaselineRelativeEvidence")

    identities = (
        (oos.factor_id, oos.factor_version),
        (null_control.factor_id, null_control.factor_version),
        (baseline.factor_id, baseline.factor_version),
    )
    if any(
        factor_id != result.factor_id
        or factor_version != result.factor_version
        for factor_id, factor_version in identities
    ):
        raise ValueError("factor identity mismatch across validation evidence")

    if null_control.factor_spec_hash != oos.factor_spec_hash:
        raise ValueError("null control factor spec hash mismatch")
    if baseline.factor_spec_hash != oos.factor_spec_hash:
        raise ValueError("baseline factor spec hash mismatch")

    oos_hash = factor_oos_predictions_hash(oos)
    if null_control.oos_predictions_hash != oos_hash:
        raise ValueError("null control OOS hash mismatch")
    if baseline.oos_predictions_hash != oos_hash:
        raise ValueError("baseline OOS hash mismatch")

    if baseline.factor_result_artifact_hash != result.artifact_hash:
        raise ValueError("baseline result artifact hash mismatch")

    if result.run_id != oos.run_id:
        raise ValueError("result/OOS run mismatch")
    if null_control.oos_run_id != oos.run_id:
        raise ValueError("null control OOS run mismatch")
    if baseline.source_run_id != result.run_id:
        raise ValueError("baseline source run mismatch")

    if null_control.n_predictions != len(oos.predictions):
        raise ValueError("null control prediction count mismatch")
    if baseline.n_predictions != len(oos.predictions):
        raise ValueError("baseline prediction count mismatch")
    if baseline.n_folds != result.n_folds:
        raise ValueError("baseline fold count mismatch")

    return FactorValidationEvidenceBundle(
        factor_id=result.factor_id,
        factor_version=result.factor_version,
        factor_spec_hash=oos.factor_spec_hash,
        factor_result_artifact_hash=result.artifact_hash,
        factor_result_run_id=result.run_id,
        oos_predictions_hash=oos_hash,
        oos_run_id=oos.run_id,
        null_control_evidence_hash=factor_null_control_evidence_hash(
            null_control
        ),
        null_control_run_id=null_control.run_id,
        baseline_relative_evidence_hash=(
            factor_baseline_relative_evidence_hash(baseline)
        ),
        baseline_relative_run_id=baseline.run_id,
    )
