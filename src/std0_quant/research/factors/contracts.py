"""Deterministic Factor Factory contracts v1.

Research-only contracts. They define auditable candidate-factor identity and
validation states; they do not promote factors into production or place orders.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
import json
import math
from typing import Any

from std0_quant.storage import canonical_json


FACTOR_SPEC_SCHEMA_V1 = "factor_spec_v1"
FACTOR_RESULT_SCHEMA_V1 = "factor_result_v1"
FACTOR_OOS_PREDICTIONS_SCHEMA_V1 = "factor_oos_predictions_v1"
FACTOR_NULL_CONTROL_EVIDENCE_SCHEMA_V1 = "factor_null_control_evidence_v1"
FACTOR_BASELINE_RELATIVE_EVIDENCE_SCHEMA_V1 = "factor_baseline_relative_evidence_v1"
FACTOR_VALIDATION_EVIDENCE_BUNDLE_SCHEMA_V1 = "factor_validation_evidence_bundle_v1"
FACTOR_REGISTRY_SCHEMA_V1 = "factor_registry_v1"


class FactorStatus(str, Enum):
    CANDIDATE = "CANDIDATE"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"
    PRODUCTION_ELIGIBLE = "PRODUCTION_ELIGIBLE"
    DEPRECATED = "DEPRECATED"


class ValidationStatus(str, Enum):
    PENDING = "PENDING"
    PASS = "PASS"
    FAIL = "FAIL"


def _nonempty(value: Any, name: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{name} must be non-empty")
    return text


def _nonnegative_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a non-negative integer")
    try:
        normalized = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a non-negative integer") from exc
    if normalized < 0 or normalized != value:
        raise ValueError(f"{name} must be a non-negative integer")
    return normalized


def _normalize_inputs(values: Any) -> tuple[str, ...]:
    try:
        normalized = tuple(_nonempty(value, "input") for value in values)
    except TypeError as exc:
        raise ValueError("inputs must be a non-empty iterable") from exc
    if not normalized:
        raise ValueError("inputs must be non-empty")
    return normalized


def _normalize_parameters(values: Any) -> tuple[tuple[str, Any], ...]:
    try:
        pairs = tuple(values)
    except TypeError as exc:
        raise ValueError("parameters must be an iterable of key/value pairs") from exc
    normalized: list[tuple[str, Any]] = []
    seen: set[str] = set()
    for item in pairs:
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            raise ValueError("parameters entries must be key/value pairs")
        key = _nonempty(item[0], "parameter key")
        if key in seen:
            raise ValueError(f"duplicate parameter key: {key}")
        seen.add(key)
        normalized.append((key, item[1]))
    return tuple(sorted(normalized, key=lambda item: item[0]))


@dataclass(frozen=True)
class FactorSpec:
    """Immutable research definition for one candidate factor version."""

    factor_id: str
    version: str
    hypothesis: str
    inputs: tuple[str, ...]
    transform: str
    parameters: tuple[tuple[str, Any], ...]
    lookback_ms: int
    decision_ts_rule: str
    availability_ts_rule: str
    missing_policy: str
    universe: str
    label: str
    period_key: str
    expected_direction: str
    expected_regime: str
    created_by: str
    created_at: str
    schema_version: str = FACTOR_SPEC_SCHEMA_V1

    def __post_init__(self) -> None:
        for name in (
            "factor_id",
            "version",
            "hypothesis",
            "transform",
            "decision_ts_rule",
            "availability_ts_rule",
            "missing_policy",
            "universe",
            "label",
            "period_key",
            "expected_direction",
            "expected_regime",
            "created_by",
            "created_at",
       ):
            object.__setattr__(self, name, _nonempty(getattr(self, name), name))
        object.__setattr__(self, "inputs", _normalize_inputs(self.inputs))
        object.__setattr__(self, "parameters", _normalize_parameters(self.parameters))
        object.__setattr__(self, "lookback_ms", _nonnegative_int(self.lookback_ms, "lookback_ms"))
        if self.schema_version != FACTOR_SPEC_SCHEMA_V1:
            raise ValueError("unsupported FactorSpec schema_version")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "FactorSpec":
        return cls(**row)

    @classmethod
    def from_json(cls, payload: str) -> "FactorSpec":
        return cls.from_dict(json.loads(payload))


_FACTOR_DEFINITION_FIELDS = (
    "schema_version",
    "factor_id",
    "version",
    "hypothesis",
    "inputs",
    "transform",
    "parameters",
    "lookback_ms",
    "decision_ts_rule",
    "availability_ts_rule",
    "missing_policy",
    "universe",
    "label",
    "period_key",
    "expected_direction",
    "expected_regime",
)


def factor_spec_definition(spec: FactorSpec) -> dict[str, Any]:
    """Return only fields that define the research semantics of a factor."""

    row = spec.to_dict()
    return {name: row[name] for name in _FACTOR_DEFINITION_FIELDS}


def factor_spec_hash(spec: FactorSpec) -> str:
    """Stable SHA256 of research semantics, excluding creation metadata."""

    payload = canonical_json(factor_spec_definition(spec))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _fraction(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be within [0, 1]")
    try:
        normalized = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be within [0, 1]") from exc
    if not 0.0 <= normalized <= 1.0:
        raise ValueError(f"{name} must be within [0, 1]")
    return normalized


@dataclass(frozen=True)
class FactorResult:
    factor_id: str
    factor_version: str
    n_total: int
    n_eligible: int
    coverage: float
    missing_rate: float
    oos_auc: float | None
    macro_period_auc: float | None
    weighted_period_auc: float | None
    brier: float | None
    logloss: float | None
    ece: float | None
    n_folds: int
    fold_positive_fraction: float
    sign_consistency: float
    temporal_integrity: ValidationStatus | str
    research_validation_status: ValidationStatus | str
    period_metrics: tuple[Any, ...]
    regime_metrics: tuple[Any, ...]
    artifact_hash: str
    run_id: str
    schema_version: str = FACTOR_RESULT_SCHEMA_V1

    def __post_init__(self) -> None:
        object.__setattr__(self, "factor_id", _nonempty(self.factor_id, "factor_id"))
        object.__setattr__(self, "factor_version", _nonempty(self.factor_version, "factor_version"))
        object.__setattr__(self, "n_total", _nonnegative_int(self.n_total, "n_total"))
        object.__setattr__(self, "n_eligible", _nonnegative_int(self.n_eligible, "n_eligible"))
        object.__setattr__(self, "n_folds", _nonnegative_int(self.n_folds, "n_folds"))

        if self.n_eligible > self.n_total:
            raise ValueError("n_eligible cannot exceed n_total")

        for name in ("coverage", "missing_rate", "fold_positive_fraction", "sign_consistency"):
            object.__setattr__(self, name, _fraction(getattr(self, name), name))

        try:
            temporal = ValidationStatus(self.temporal_integrity)
        except ValueError as exc:
            raise ValueError("unsupported temporal_integrity") from exc
        try:
            research = ValidationStatus(self.research_validation_status)
        except ValueError as exc:
            raise ValueError("unsupported research_validation_status") from exc

        if research == ValidationStatus.PASS and temporal != ValidationStatus.PASS:
            raise ValueError("research PASS requires temporal_integrity PASS")

        object.__setattr__(self, "temporal_integrity", temporal)
        object.__setattr__(self, "research_validation_status", research)
        object.__setattr__(self, "period_metrics", tuple(self.period_metrics))
        object.__setattr__(self, "regime_metrics", tuple(self.regime_metrics))
        object.__setattr__(self, "artifact_hash", _nonempty(self.artifact_hash, "artifact_hash"))
        object.__setattr__(self, "run_id", _nonempty(self.run_id, "run_id"))

        if self.schema_version != FACTOR_RESULT_SCHEMA_V1:
            raise ValueError("unsupported FactorResult schema_version")

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["temporal_integrity"] = self.temporal_integrity.value
        row["research_validation_status"] = self.research_validation_status.value
        return row

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "FactorResult":
        return cls(**row)

    @classmethod
    def from_json(cls, payload: str) -> "FactorResult":
        return cls.from_dict(json.loads(payload))


@dataclass(frozen=True)
class FactorOOSPrediction:
    condition_id: str
    fold_id: int
    test_period: str
    prediction_ts_ms: int
    y: int
    probability: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "condition_id", _nonempty(self.condition_id, "condition_id"))
        fold_id = _nonnegative_int(self.fold_id, "fold_id")
        if fold_id < 1:
            raise ValueError("fold_id must be >= 1")
        object.__setattr__(self, "fold_id", fold_id)
        object.__setattr__(self, "test_period", _nonempty(self.test_period, "test_period"))
        object.__setattr__(
            self,
            "prediction_ts_ms",
            _nonnegative_int(self.prediction_ts_ms, "prediction_ts_ms"),
        )

        if isinstance(self.y, bool):
            raise ValueError("y must be binary 0 or 1")
        try:
            y = int(self.y)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("y must be binary 0 or 1") from exc
        if y not in (0, 1) or y != self.y:
            raise ValueError("y must be binary 0 or 1")
        object.__setattr__(self, "y", y)

        if isinstance(self.probability, bool):
            raise ValueError("probability must be finite and within [0, 1]")
        try:
            probability = float(self.probability)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(
                "probability must be finite and within [0, 1]"
            ) from exc
        if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
            raise ValueError("probability must be finite and within [0, 1]")
        object.__setattr__(self, "probability", probability)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "FactorOOSPrediction":
        return cls(**row)

    @classmethod
    def from_json(cls, payload: str) -> "FactorOOSPrediction":
        return cls.from_dict(json.loads(payload))


@dataclass(frozen=True)
class FactorOOSPredictionArtifact:
    factor_id: str
    factor_version: str
    factor_spec_hash: str
    run_id: str
    predictions: tuple[FactorOOSPrediction, ...]
    schema_version: str = FACTOR_OOS_PREDICTIONS_SCHEMA_V1

    def __post_init__(self) -> None:
        for name in (
            "factor_id",
            "factor_version",
            "factor_spec_hash",
            "run_id",
        ):
            object.__setattr__(self, name, _nonempty(getattr(self, name), name))

        try:
            predictions = tuple(self.predictions)
        except TypeError as exc:
            raise ValueError("predictions must be an iterable") from exc
        if any(not isinstance(row, FactorOOSPrediction) for row in predictions):
            raise ValueError(
                "predictions must contain FactorOOSPrediction rows only"
            )
        object.__setattr__(self, "predictions", predictions)

        if self.schema_version != FACTOR_OOS_PREDICTIONS_SCHEMA_V1:
            raise ValueError(
                "unsupported FactorOOSPredictionArtifact schema_version"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "FactorOOSPredictionArtifact":
        normalized = dict(row)
        normalized["predictions"] = tuple(
            value
            if isinstance(value, FactorOOSPrediction)
            else FactorOOSPrediction.from_dict(value)
            for value in normalized.get("predictions", ())
        )
        return cls(**normalized)

    @classmethod
    def from_json(cls, payload: str) -> "FactorOOSPredictionArtifact":
        return cls.from_dict(json.loads(payload))


def factor_oos_predictions_definition(
    artifact: FactorOOSPredictionArtifact,
) -> dict[str, Any]:
    row = artifact.to_dict()
    return {
        name: row[name]
        for name in (
            "schema_version",
            "factor_id",
            "factor_version",
            "factor_spec_hash",
            "predictions",
        )
    }


def factor_oos_predictions_hash(
    artifact: FactorOOSPredictionArtifact,
) -> str:
    payload = canonical_json(factor_oos_predictions_definition(artifact))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class NullControlMetricSummary:
    n_valid: int
    mean: float | None
    std: float | None
    p95: float | None
    max: float | None

    def __post_init__(self) -> None:
        n_valid = _nonnegative_int(self.n_valid, "n_valid")
        object.__setattr__(self, "n_valid", n_valid)
        values = (self.mean, self.std, self.p95, self.max)
        if n_valid == 0:
            if any(value is not None for value in values):
                raise ValueError("empty null metric summary requires None statistics")
            return
        if any(value is None for value in values):
            raise ValueError("non-empty null metric summary requires all statistics")
        for name in ("mean", "std", "p95", "max"):
            value = getattr(self, name)
            if isinstance(value, bool):
                raise ValueError(f"{name} must be finite and within [0, 1]")
            try:
                normalized = float(value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(
                    f"{name} must be finite and within [0, 1]"
                ) from exc
            if not math.isfinite(normalized) or not 0.0 <= normalized <= 1.0:
                raise ValueError(f"{name} must be finite and within [0, 1]")
            object.__setattr__(self, name, normalized)


@dataclass(frozen=True)
class FactorNullControlEvidence:
    factor_id: str
    factor_version: str
    factor_spec_hash: str
    oos_predictions_hash: str
    oos_run_id: str
    method: str
    seed: int
    n_shuffles: int
    n_predictions: int
    pooled_auc: NullControlMetricSummary
    macro_period_auc: NullControlMetricSummary
    weighted_period_auc: NullControlMetricSummary
    run_id: str
    schema_version: str = FACTOR_NULL_CONTROL_EVIDENCE_SCHEMA_V1

    def __post_init__(self) -> None:
        for name in (
            "factor_id",
            "factor_version",
            "factor_spec_hash",
            "oos_predictions_hash",
            "oos_run_id",
            "method",
            "run_id",
        ):
            object.__setattr__(self, name, _nonempty(getattr(self, name), name))

        object.__setattr__(self, "seed", _nonnegative_int(self.seed, "seed"))
        n_shuffles = _nonnegative_int(self.n_shuffles, "n_shuffles")
        if n_shuffles < 1:
            raise ValueError("n_shuffles must be >= 1")
        object.__setattr__(self, "n_shuffles", n_shuffles)
        object.__setattr__(
            self,
            "n_predictions",
            _nonnegative_int(self.n_predictions, "n_predictions"),
        )

        for name in (
            "pooled_auc",
            "macro_period_auc",
            "weighted_period_auc",
        ):
            if not isinstance(getattr(self, name), NullControlMetricSummary):
                raise ValueError(f"{name} must be NullControlMetricSummary")
            if getattr(self, name).n_valid > n_shuffles:
                raise ValueError(f"{name}.n_valid cannot exceed n_shuffles")

        if self.schema_version != FACTOR_NULL_CONTROL_EVIDENCE_SCHEMA_V1:
            raise ValueError(
                "unsupported FactorNullControlEvidence schema_version"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "FactorNullControlEvidence":
        normalized = dict(row)
        for name in (
            "pooled_auc",
            "macro_period_auc",
            "weighted_period_auc",
        ):
            value = normalized[name]
            if not isinstance(value, NullControlMetricSummary):
                normalized[name] = NullControlMetricSummary(**value)
        return cls(**normalized)

    @classmethod
    def from_json(cls, payload: str) -> "FactorNullControlEvidence":
        return cls.from_dict(json.loads(payload))


def factor_null_control_evidence_definition(
    evidence: FactorNullControlEvidence,
) -> dict[str, Any]:
    row = evidence.to_dict()
    return {
        name: row[name]
        for name in (
            "schema_version",
            "factor_id",
            "factor_version",
            "factor_spec_hash",
            "oos_predictions_hash",
            "method",
            "seed",
            "n_shuffles",
            "n_predictions",
            "pooled_auc",
            "macro_period_auc",
            "weighted_period_auc",
        )
    }


def factor_null_control_evidence_hash(
    evidence: FactorNullControlEvidence,
) -> str:
    payload = canonical_json(
        factor_null_control_evidence_definition(evidence)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _optional_finite(value: Any, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite or None")
    try:
        normalized = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be finite or None") from exc
    if not math.isfinite(normalized):
        raise ValueError(f"{name} must be finite or None")
    return normalized


@dataclass(frozen=True)
class BaselineFoldEvidence:
    fold_id: int
    test_period: str
    train_n: int
    test_n: int
    baseline_probability: float
    candidate_brier: float
    baseline_brier: float
    candidate_logloss: float
    baseline_logloss: float

    def __post_init__(self) -> None:
        fold_id = _nonnegative_int(self.fold_id, "fold_id")
        if fold_id < 1:
            raise ValueError("fold_id must be >= 1")
        object.__setattr__(self, "fold_id", fold_id)
        object.__setattr__(self, "test_period", _nonempty(self.test_period, "test_period"))
        object.__setattr__(self, "train_n", _nonnegative_int(self.train_n, "train_n"))
        object.__setattr__(self, "test_n", _nonnegative_int(self.test_n, "test_n"))
        object.__setattr__(
            self,
            "baseline_probability",
            _fraction(self.baseline_probability, "baseline_probability"),
        )
        for name in (
            "candidate_brier",
            "baseline_brier",
            "candidate_logloss",
            "baseline_logloss",
        ):
            value = _optional_finite(getattr(self, name), name)
            if value is None or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
            object.__setattr__(self, name, value)


@dataclass(frozen=True)
class FactorBaselineRelativeEvidence:
    factor_id: str
    factor_version: str
    factor_spec_hash: str
    factor_result_artifact_hash: str
    oos_predictions_hash: str
    source_run_id: str
    baseline_method: str
    n_predictions: int
    n_folds: int
    candidate_brier: float | None
    baseline_brier: float | None
    delta_brier: float | None
    candidate_logloss: float | None
    baseline_logloss: float | None
    delta_logloss: float | None
    candidate_macro_period_auc: float | None
    baseline_macro_period_auc: float | None
    delta_macro_period_auc: float | None
    pct_folds_brier_improved: float
    pct_folds_logloss_improved: float
    fold_baselines: tuple[BaselineFoldEvidence, ...]
    run_id: str
    schema_version: str = FACTOR_BASELINE_RELATIVE_EVIDENCE_SCHEMA_V1

    def __post_init__(self) -> None:
        for name in (
            "factor_id",
            "factor_version",
            "factor_spec_hash",
            "factor_result_artifact_hash",
            "oos_predictions_hash",
            "source_run_id",
            "baseline_method",
            "run_id",
        ):
            object.__setattr__(self, name, _nonempty(getattr(self, name), name))

        object.__setattr__(
            self,
            "n_predictions",
            _nonnegative_int(self.n_predictions, "n_predictions"),
        )
        object.__setattr__(self, "n_folds", _nonnegative_int(self.n_folds, "n_folds"))

        for name in (
            "candidate_brier",
            "baseline_brier",
            "delta_brier",
            "candidate_logloss",
            "baseline_logloss",
            "delta_logloss",
            "candidate_macro_period_auc",
            "baseline_macro_period_auc",
            "delta_macro_period_auc",
        ):
            object.__setattr__(
                self,
                name,
                _optional_finite(getattr(self, name), name),
            )

        for name in (
            "pct_folds_brier_improved",
            "pct_folds_logloss_improved",
        ):
            object.__setattr__(self, name, _fraction(getattr(self, name), name))

        try:
            folds = tuple(self.fold_baselines)
        except TypeError as exc:
            raise ValueError("fold_baselines must be an iterable") from exc
        if any(not isinstance(row, BaselineFoldEvidence) for row in folds):
            raise ValueError(
                "fold_baselines must contain BaselineFoldEvidence rows only"
            )
        if len(folds) != self.n_folds:
            raise ValueError("fold_baselines length must equal n_folds")
        object.__setattr__(self, "fold_baselines", folds)

        if self.schema_version != FACTOR_BASELINE_RELATIVE_EVIDENCE_SCHEMA_V1:
            raise ValueError(
                "unsupported FactorBaselineRelativeEvidence schema_version"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "FactorBaselineRelativeEvidence":
        normalized = dict(row)
        normalized["fold_baselines"] = tuple(
            value
            if isinstance(value, BaselineFoldEvidence)
            else BaselineFoldEvidence(**value)
            for value in normalized.get("fold_baselines", ())
        )
        return cls(**normalized)

    @classmethod
    def from_json(cls, payload: str) -> "FactorBaselineRelativeEvidence":
        return cls.from_dict(json.loads(payload))


def factor_baseline_relative_evidence_definition(
    evidence: FactorBaselineRelativeEvidence,
) -> dict[str, Any]:
    row = evidence.to_dict()
    return {
        name: row[name]
        for name in (
            "schema_version",
            "factor_id",
            "factor_version",
            "factor_spec_hash",
            "factor_result_artifact_hash",
            "oos_predictions_hash",
            "baseline_method",
            "n_predictions",
            "n_folds",
            "candidate_brier",
            "baseline_brier",
            "delta_brier",
            "candidate_logloss",
            "baseline_logloss",
            "delta_logloss",
            "candidate_macro_period_auc",
            "baseline_macro_period_auc",
            "delta_macro_period_auc",
            "pct_folds_brier_improved",
            "pct_folds_logloss_improved",
            "fold_baselines",
        )
    }


def factor_baseline_relative_evidence_hash(
    evidence: FactorBaselineRelativeEvidence,
) -> str:
    payload = canonical_json(
        factor_baseline_relative_evidence_definition(evidence)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FactorValidationEvidenceBundle:
    factor_id: str
    factor_version: str
    factor_spec_hash: str
    factor_result_artifact_hash: str
    factor_result_run_id: str
    oos_predictions_hash: str
    oos_run_id: str
    null_control_evidence_hash: str
    null_control_run_id: str
    baseline_relative_evidence_hash: str
    baseline_relative_run_id: str
    schema_version: str = FACTOR_VALIDATION_EVIDENCE_BUNDLE_SCHEMA_V1

    def __post_init__(self) -> None:
        for name in (
            "factor_id",
            "factor_version",
            "factor_spec_hash",
            "factor_result_artifact_hash",
            "factor_result_run_id",
            "oos_predictions_hash",
            "oos_run_id",
            "null_control_evidence_hash",
            "null_control_run_id",
            "baseline_relative_evidence_hash",
            "baseline_relative_run_id",
        ):
            object.__setattr__(
                self,
                name,
                _nonempty(getattr(self, name), name),
            )

        if self.schema_version != FACTOR_VALIDATION_EVIDENCE_BUNDLE_SCHEMA_V1:
            raise ValueError(
                "unsupported FactorValidationEvidenceBundle schema_version"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(
        cls,
        row: dict[str, Any],
    ) -> "FactorValidationEvidenceBundle":
        return cls(**row)

    @classmethod
    def from_json(
        cls,
        payload: str,
    ) -> "FactorValidationEvidenceBundle":
        return cls.from_dict(json.loads(payload))


def factor_validation_evidence_bundle_definition(
    bundle: FactorValidationEvidenceBundle,
) -> dict[str, Any]:
    row = bundle.to_dict()
    return {
        name: row[name]
        for name in (
            "schema_version",
            "factor_id",
            "factor_version",
            "factor_spec_hash",
            "factor_result_artifact_hash",
            "oos_predictions_hash",
            "null_control_evidence_hash",
            "baseline_relative_evidence_hash",
        )
    }


def factor_validation_evidence_bundle_hash(
    bundle: FactorValidationEvidenceBundle,
) -> str:
    payload = canonical_json(
        factor_validation_evidence_bundle_definition(bundle)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
