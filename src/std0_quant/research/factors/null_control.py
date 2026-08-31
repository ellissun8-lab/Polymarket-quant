"""Deterministic fixed-OOS null control for Factor Factory research."""

from __future__ import annotations

import numpy as np

from std0_quant.audit.conditional_metrics import conditional_auc

from .contracts import (
    FactorNullControlEvidence,
    FactorOOSPredictionArtifact,
    NullControlMetricSummary,
    factor_oos_predictions_hash,
)


_METHOD = "within_period_label_permutation_fixed_oos_v1"


def _summary(values: list[float | None]) -> NullControlMetricSummary:
    valid = np.asarray(
        [value for value in values if value is not None],
        dtype=float,
    )
    if not len(valid):
        return NullControlMetricSummary(
            n_valid=0,
            mean=None,
            std=None,
            p95=None,
            max=None,
        )
    return NullControlMetricSummary(
        n_valid=len(valid),
        mean=float(valid.mean()),
        std=float(valid.std()),
        p95=float(np.percentile(valid, 95)),
        max=float(valid.max()),
    )


def run_factor_null_control(
    source: FactorOOSPredictionArtifact,
    *,
    n_shuffles: int,
    seed: int,
    run_id: str,
) -> FactorNullControlEvidence:
    if not isinstance(source, FactorOOSPredictionArtifact):
        raise ValueError("source must be FactorOOSPredictionArtifact")
    if isinstance(n_shuffles, bool) or not isinstance(n_shuffles, int) or n_shuffles < 1:
        raise ValueError("n_shuffles must be a positive integer")

    labels = np.asarray([row.y for row in source.predictions], dtype=int)
    probabilities = [row.probability for row in source.predictions]
    periods = [row.test_period for row in source.predictions]
    rng = np.random.default_rng(seed)

    pooled: list[float | None] = []
    macro: list[float | None] = []
    weighted: list[float | None] = []

    grouped = {
        period: np.asarray(
            [i for i, value in enumerate(periods) if value == period],
            dtype=int,
        )
        for period in sorted(set(periods))
    }

    for _ in range(n_shuffles):
        shuffled = labels.copy()
        for index in grouped.values():
            shuffled[index] = rng.permutation(shuffled[index])

        metrics = conditional_auc(shuffled, probabilities, periods)
        pooled.append(metrics["pooled_auc"])
        macro.append(metrics["macro_auc"])
        weighted.append(metrics["weighted_auc"])

    return FactorNullControlEvidence(
        factor_id=source.factor_id,
        factor_version=source.factor_version,
        factor_spec_hash=source.factor_spec_hash,
        oos_predictions_hash=factor_oos_predictions_hash(source),
        oos_run_id=source.run_id,
        method=_METHOD,
        seed=seed,
        n_shuffles=n_shuffles,
        n_predictions=len(source.predictions),
        pooled_auc=_summary(pooled),
        macro_period_auc=_summary(macro),
        weighted_period_auc=_summary(weighted),
        run_id=run_id,
    )
