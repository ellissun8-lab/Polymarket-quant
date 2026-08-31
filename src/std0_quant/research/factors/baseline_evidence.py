# Deterministic baseline-relative evidence for Factor Factory research.

from __future__ import annotations

from std0_quant.audit.conditional_metrics import probability_metrics

from .contracts import (
    BaselineFoldEvidence,
    FactorBaselineRelativeEvidence,
    FactorOOSPredictionArtifact,
    FactorResult,
    factor_oos_predictions_hash,
)


_METHOD = "train_prevalence_per_fold_v1"


def run_factor_baseline_relative_evidence(
    result: FactorResult,
    source: FactorOOSPredictionArtifact,
    *,
    run_id: str,
) -> FactorBaselineRelativeEvidence:
    if not isinstance(result, FactorResult):
        raise ValueError("result must be FactorResult")
    if not isinstance(source, FactorOOSPredictionArtifact):
        raise ValueError("source must be FactorOOSPredictionArtifact")

    if result.factor_id != source.factor_id:
        raise ValueError("factor_id mismatch")
    if result.factor_version != source.factor_version:
        raise ValueError("factor_version mismatch")
    if result.run_id != source.run_id:
        raise ValueError("source run mismatch")
    if result.n_folds != len(result.period_metrics):
        raise ValueError("period_metrics must match n_folds")

    fold_metrics = {}
    for fold in result.period_metrics:
        if not isinstance(fold, dict):
            raise ValueError("period_metrics rows must be dictionaries")
        if "train_positive_rate" not in fold:
            raise ValueError("train_positive_rate is required")
        fold_id = int(fold["fold_id"])
        if fold_id in fold_metrics:
            raise ValueError("duplicate fold_id")
        fold_metrics[fold_id] = fold

    if set(row.fold_id for row in source.predictions) - set(fold_metrics):
        raise ValueError("prediction fold missing from period_metrics")

    candidate_y = [row.y for row in source.predictions]
    candidate_p = [row.probability for row in source.predictions]
    periods = [row.test_period for row in source.predictions]
    baseline_p = []
    fold_rows = []

    for fold_id in sorted(fold_metrics):
        fold = fold_metrics[fold_id]
        predictions = [
            row for row in source.predictions
            if row.fold_id == fold_id
        ]
        if not predictions:
            raise ValueError("each fold requires OOS predictions")
        if any(row.test_period != str(fold["test_period"]) for row in predictions):
            raise ValueError("test_period mismatch")
        if len(predictions) != int(fold["test_n"]):
            raise ValueError("test_n mismatch")

        baseline_probability = float(fold["train_positive_rate"])
        if not 0.0 <= baseline_probability <= 1.0:
            raise ValueError("train_positive_rate must be within [0, 1]")

        y = [row.y for row in predictions]
        candidate_probability = [row.probability for row in predictions]
        baseline_probability_rows = [baseline_probability] * len(predictions)

        candidate_metrics = probability_metrics(
            y,
            candidate_probability,
            [row.test_period for row in predictions],
        )
        baseline_metrics = probability_metrics(
            y,
            baseline_probability_rows,
            [row.test_period for row in predictions],
        )

        fold_rows.append(
            BaselineFoldEvidence(
                fold_id=fold_id,
                test_period=str(fold["test_period"]),
                train_n=int(fold["train_n"]),
                test_n=int(fold["test_n"]),
                baseline_probability=baseline_probability,
                candidate_brier=candidate_metrics["brier"],
                baseline_brier=baseline_metrics["brier"],
                candidate_logloss=candidate_metrics["logloss"],
                baseline_logloss=baseline_metrics["logloss"],
            )
        )

    baseline_by_fold = {
        row.fold_id: row.baseline_probability
        for row in fold_rows
    }
    baseline_p = [
        baseline_by_fold[row.fold_id]
        for row in source.predictions
    ]

    candidate = probability_metrics(candidate_y, candidate_p, periods)
    baseline = probability_metrics(candidate_y, baseline_p, periods)

    valid_folds = len(fold_rows)
    pct_brier = (
        sum(
            row.baseline_brier - row.candidate_brier > 0
            for row in fold_rows
        )
        / valid_folds
        if valid_folds
        else 0.0
    )
    pct_logloss = (
        sum(
            row.baseline_logloss - row.candidate_logloss > 0
            for row in fold_rows
        )
        / valid_folds
        if valid_folds
        else 0.0
    )

    candidate_macro = candidate["macro_auc"]
    baseline_macro = baseline["macro_auc"]

    return FactorBaselineRelativeEvidence(
        factor_id=result.factor_id,
        factor_version=result.factor_version,
        factor_spec_hash=source.factor_spec_hash,
        factor_result_artifact_hash=result.artifact_hash,
        oos_predictions_hash=factor_oos_predictions_hash(source),
        source_run_id=result.run_id,
        baseline_method=_METHOD,
        n_predictions=len(source.predictions),
        n_folds=result.n_folds,
        candidate_brier=candidate["brier"],
        baseline_brier=baseline["brier"],
        delta_brier=baseline["brier"] - candidate["brier"],
        candidate_logloss=candidate["logloss"],
        baseline_logloss=baseline["logloss"],
        delta_logloss=baseline["logloss"] - candidate["logloss"],
        candidate_macro_period_auc=candidate_macro,
        baseline_macro_period_auc=baseline_macro,
        delta_macro_period_auc=(
            candidate_macro - baseline_macro
            if candidate_macro is not None and baseline_macro is not None
            else None
        ),
        pct_folds_brier_improved=pct_brier,
        pct_folds_logloss_improved=pct_logloss,
        fold_baselines=tuple(fold_rows),
        run_id=run_id,
    )
