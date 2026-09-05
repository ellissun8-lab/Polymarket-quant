"""Deterministic batch factor evaluator v1.

Research-only. This module evaluates approved FactorSpec transforms over
point-in-time rows. It does not promote registry state or execute orders.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any, Iterable, Sequence

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from std0_quant.audit.conditional_metrics import probability_metrics
from std0_quant.storage import canonical_json

from .contracts import (
    FactorOOSPrediction,
    FactorOOSPredictionArtifact,
    FactorResult,
    FactorSpec,
    ValidationStatus,
    factor_spec_hash,
)


_ALLOWED_TRANSFORMS = {"identity", "product"}
_ALLOWED_MISSING_POLICIES = {"EXCLUDE"}
_ALLOWED_DECISION_RULES = {"prediction_ts_ms"}
_ALLOWED_AVAILABILITY_RULES = {"feature_cutoff_ms<prediction_ts_ms"}
_ALLOWED_DIRECTIONS = {"POSITIVE", "NEGATIVE"}
_FACTOR_RESULT_SEMANTIC_DIGEST_SCHEMA_V2 = "factor_result_semantic_digest_v2"
_FACTOR_RESULT_SEMANTIC_DIGEST_FLOAT_ENCODING_V1 = "decimal_sig15_v1"


def _semantic_digest_value_v2(value: Any) -> Any:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("semantic digest requires finite floats")
        return format(value, ".15g")
    if isinstance(value, dict):
        return {key: _semantic_digest_value_v2(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_semantic_digest_value_v2(item) for item in value]
    if isinstance(value, tuple):
        return [_semantic_digest_value_v2(item) for item in value]
    return value


def factor_result_artifact_hash_v1(artifact_payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        canonical_json(artifact_payload).encode("utf-8")
    ).hexdigest()


def factor_result_semantic_digest_v2(artifact_payload: dict[str, Any]) -> str:
    payload = {
        "schema_version": _FACTOR_RESULT_SEMANTIC_DIGEST_SCHEMA_V2,
        "float_encoding": _FACTOR_RESULT_SEMANTIC_DIGEST_FLOAT_ENCODING_V1,
        "payload": _semantic_digest_value_v2(artifact_payload),
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _validate_spec_for_evaluator(spec: FactorSpec) -> None:
    if spec.transform not in _ALLOWED_TRANSFORMS:
        raise ValueError(f"unsupported factor transform: {spec.transform}")
    if spec.missing_policy not in _ALLOWED_MISSING_POLICIES:
        raise ValueError(
            "Factor Evaluator v1 supports missing_policy=EXCLUDE only"
        )
    if spec.decision_ts_rule not in _ALLOWED_DECISION_RULES:
        raise ValueError(
            f"unsupported decision_ts_rule: {spec.decision_ts_rule}"
        )
    if spec.availability_ts_rule not in _ALLOWED_AVAILABILITY_RULES:
        raise ValueError(
            f"unsupported availability_ts_rule: {spec.availability_ts_rule}"
        )
    if spec.parameters:
        raise ValueError(
            "Factor Evaluator v1 does not support transform parameters"
        )
    direction = spec.expected_direction.upper()
    if direction not in _ALLOWED_DIRECTIONS:
        raise ValueError(
            f"unsupported expected_direction: {spec.expected_direction}"
        )
    if spec.transform == "identity" and len(spec.inputs) != 1:
        raise ValueError("identity transform requires exactly one input")
    if spec.transform == "product" and not spec.inputs:
        raise ValueError("product transform requires at least one input")


def _factor_value(spec: FactorSpec, row: dict[str, Any]) -> float | None:
    values: list[float] = []
    for name in spec.inputs:
        value = row.get(name)
        if value is None:
            return None
        try:
            numeric = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"factor input {name} must be numeric") from exc
        if not math.isfinite(numeric):
            return None
        values.append(numeric)

    if spec.transform == "identity":
        return values[0]

    result = 1.0
    for value in values:
        result *= value
    return result if math.isfinite(result) else None


def _point_in_time_rows(
    spec: FactorSpec,
    rows: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    eligible: list[dict[str, Any]] = []
    missing = 0

    for row in rows:
        prediction = row.get("prediction_ts_ms")
        cutoff = row.get("feature_cutoff_ms")
        if prediction is None or cutoff is None:
            raise ValueError(
                "temporal fields prediction_ts_ms and feature_cutoff_ms are required"
            )
        prediction_ms = int(prediction)
        cutoff_ms = int(cutoff)
        if cutoff_ms >= prediction_ms:
            raise ValueError(
                "temporal integrity failure: feature cutoff must strictly precede prediction"
            )

        value = _factor_value(spec, row)
        if value is None:
            missing += 1
            continue

        if row.get("model_eligible") is False:
            continue

        label = row.get(spec.label)
        period = row.get(spec.period_key)
        if label is None or period is None:
            continue

        y = int(label)
        if y not in (0, 1):
            raise ValueError("Factor Evaluator v1 requires a binary label")

        condition_id = row.get("condition_id")
        if condition_id is None or not str(condition_id).strip():
            raise ValueError("condition_id must be non-empty")
        eligible.append(
            {
                "condition_id": str(condition_id).strip(),
                "prediction_ts_ms": prediction_ms,
                "period": str(period),
                "factor_value": float(value),
                "y": y,
            }
        )

    eligible.sort(
        key=lambda row: (
            row["prediction_ts_ms"],
            row["condition_id"],
        )
    )
    return eligible, missing


def _fit(train_x: np.ndarray, train_y: np.ndarray):
    if len(np.unique(train_y)) < 2:
        return None
    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(max_iter=1000, random_state=0),
    )
    model.fit(train_x.reshape(-1, 1), train_y)
    return model


def _empty_metrics() -> dict[str, float | None]:
    return {
        "pooled_auc": None,
        "macro_auc": None,
        "weighted_auc": None,
        "brier": None,
        "logloss": None,
        "ece": None,
    }


def _evaluate_factor_impl(
    spec: FactorSpec,
    rows: Sequence[dict[str, Any]],
    *,
    min_train_periods: int = 4,
    min_test_n: int = 30,
    run_id: str,
    prediction_sink: list[FactorOOSPrediction] | None = None,
    semantic_digest_sink: list[str] | None = None,
) -> FactorResult:
    """Evaluate one factor with expanding-period out-of-sample folds."""

    _validate_spec_for_evaluator(spec)
    if min_train_periods < 1:
        raise ValueError("min_train_periods must be >= 1")
    if min_test_n < 1:
        raise ValueError("min_test_n must be >= 1")
    run_id = str(run_id).strip()
    if not run_id:
        raise ValueError("run_id must be non-empty")

    total = len(rows)
    eligible, missing = _point_in_time_rows(spec, rows)
    periods = sorted({row["period"] for row in eligible})

    predictions: list[dict[str, Any]] = []
    folds: list[dict[str, Any]] = []
    coefficient_signs: list[int] = []

    for index in range(min_train_periods, len(periods)):
        test_period = periods[index]
        train = [row for row in eligible if row["period"] < test_period]
        test = [row for row in eligible if row["period"] == test_period]

        if len(test) < min_test_n or not train:
            continue

        if max(row["prediction_ts_ms"] for row in train) >= min(
            row["prediction_ts_ms"] for row in test
        ):
            raise ValueError(
                "temporal integrity failure: train must strictly precede test"
            )

        x_train = np.asarray(
            [row["factor_value"] for row in train],
            dtype=float,
        )
        y_train = np.asarray([row["y"] for row in train], dtype=int)
        model = _fit(x_train, y_train)
        if model is None:
            continue

        x_test = np.asarray(
            [row["factor_value"] for row in test],
            dtype=float,
        )
        y_test = np.asarray([row["y"] for row in test], dtype=int)
        probability = model.predict_proba(x_test.reshape(-1, 1))[:, 1]
        metrics = probability_metrics(
            y_test,
            probability,
            [test_period] * len(test),
        )

        coefficient = float(
            model.named_steps["logisticregression"].coef_[0][0]
        )
        coefficient_signs.append(
            1 if coefficient > 0 else -1 if coefficient < 0 else 0
        )
        folds.append(
            {
                "fold_id": len(folds) + 1,
                "test_period": test_period,
                "train_n": len(train),
                "test_n": len(test),
                "train_positive_rate": float(y_train.mean()),
                "coefficient": coefficient,
                "auc": metrics["pooled_auc"],
                "brier": metrics["brier"],
                "logloss": metrics["logloss"],
            }
        )
        for row, probability_value in zip(test, probability):
            predictions.append(
                {
                    "condition_id": row["condition_id"],
                    "period": test_period,
                    "y": row["y"],
                    "probability": float(probability_value),
                }
            )
            if prediction_sink is not None:
                prediction_sink.append(
                    FactorOOSPrediction(
                        condition_id=row["condition_id"],
                        fold_id=len(folds),
                        test_period=test_period,
                        prediction_ts_ms=row["prediction_ts_ms"],
                        y=row["y"],
                        probability=float(probability_value),
                    )
                )

    if predictions:
        aggregate = probability_metrics(
            [row["y"] for row in predictions],
            [row["probability"] for row in predictions],
            [row["period"] for row in predictions],
        )
    else:
        aggregate = _empty_metrics()

    fold_positive_fraction = (
        sum(
            row["auc"] is not None and row["auc"] > 0.5
            for row in folds
        )
        / len(folds)
        if folds
        else 0.0
    )

    expected_sign = (
        1 if spec.expected_direction.upper() == "POSITIVE" else -1
    )
    sign_consistency = (
        sum(sign == expected_sign for sign in coefficient_signs)
        / len(coefficient_signs)
        if coefficient_signs
        else 0.0
    )

    artifact_payload = {
        "factor_spec_hash": factor_spec_hash(spec),
        "n_total": total,
        "n_eligible": len(eligible),
        "missing_count": missing,
        "folds": [
            {
                key: value
                for key, value in row.items()
                if key != "train_positive_rate"
            }
            for row in folds
        ],
        "predictions": predictions,
    }
    artifact_hash = factor_result_artifact_hash_v1(artifact_payload)
    if semantic_digest_sink is not None:
        semantic_digest_sink.append(
            factor_result_semantic_digest_v2(artifact_payload)
        )

    return FactorResult(
        factor_id=spec.factor_id,
        factor_version=spec.version,
        n_total=total,
        n_eligible=len(eligible),
        coverage=(len(eligible) / total) if total else 0.0,
        missing_rate=(missing / total) if total else 0.0,
        oos_auc=aggregate["pooled_auc"],
        macro_period_auc=aggregate["macro_auc"],
        weighted_period_auc=aggregate["weighted_auc"],
        brier=aggregate["brier"],
        logloss=aggregate["logloss"],
        ece=aggregate["ece"],
        n_folds=len(folds),
        fold_positive_fraction=fold_positive_fraction,
        sign_consistency=sign_consistency,
        temporal_integrity=ValidationStatus.PASS,
        research_validation_status=ValidationStatus.PENDING,
        period_metrics=tuple(folds),
        regime_metrics=(),
        artifact_hash=artifact_hash,
        run_id=run_id,
    )


def evaluate_factor(
    spec: FactorSpec,
    rows: Sequence[dict[str, Any]],
    *,
    min_train_periods: int = 4,
    min_test_n: int = 30,
    run_id: str,
) -> FactorResult:
    return _evaluate_factor_impl(
        spec,
        rows,
        min_train_periods=min_train_periods,
        min_test_n=min_test_n,
        run_id=run_id,
    )


def evaluate_factor_with_semantic_digest_v2(
    spec: FactorSpec,
    rows: Sequence[dict[str, Any]],
    *,
    min_train_periods: int = 4,
    min_test_n: int = 30,
    run_id: str,
) -> tuple[FactorResult, str]:
    semantic_digest_sink: list[str] = []
    result = _evaluate_factor_impl(
        spec,
        rows,
        min_train_periods=min_train_periods,
        min_test_n=min_test_n,
        run_id=run_id,
        semantic_digest_sink=semantic_digest_sink,
    )
    if len(semantic_digest_sink) != 1:
        raise RuntimeError("semantic digest v2 was not produced exactly once")
    return result, semantic_digest_sink[0]


def evaluate_factor_with_oos_predictions(
    spec: FactorSpec,
    rows: Sequence[dict[str, Any]],
    *,
    min_train_periods: int = 4,
    min_test_n: int = 30,
    run_id: str,
) -> tuple[FactorResult, FactorOOSPredictionArtifact]:
    prediction_sink: list[FactorOOSPrediction] = []
    result = _evaluate_factor_impl(
        spec,
        rows,
        min_train_periods=min_train_periods,
        min_test_n=min_test_n,
        run_id=run_id,
        prediction_sink=prediction_sink,
    )
    artifact = FactorOOSPredictionArtifact(
        factor_id=spec.factor_id,
        factor_version=spec.version,
        factor_spec_hash=factor_spec_hash(spec),
        run_id=result.run_id,
        predictions=tuple(prediction_sink),
    )
    return result, artifact


def evaluate_batch(
    specs: Iterable[FactorSpec],
    rows: Sequence[dict[str, Any]],
    *,
    min_train_periods: int = 4,
    min_test_n: int = 30,
    run_id: str,
) -> tuple[FactorResult, ...]:
    """Evaluate FactorSpecs in caller order without registry side effects."""

    return tuple(
        evaluate_factor(
            spec,
            rows,
            min_train_periods=min_train_periods,
            min_test_n=min_test_n,
            run_id=run_id,
        )
        for spec in specs
    )
