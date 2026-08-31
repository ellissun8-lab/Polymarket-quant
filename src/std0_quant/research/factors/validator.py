"""Deterministic generic factor research validator v1.

Research-only. Validation thresholds are explicit policy inputs. This module
does not mutate FactorResult, promote registry state, or execute orders.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import math
from typing import Any

from std0_quant.storage import canonical_json

from .contracts import (
    FactorBaselineRelativeEvidence,
    FactorNullControlEvidence,
    FactorResult,
    FactorValidationEvidenceBundle,
    ValidationStatus,
    factor_baseline_relative_evidence_hash,
    factor_null_control_evidence_hash,
    factor_validation_evidence_bundle_hash,
)


VALIDATION_POLICY_SCHEMA_V1 = "factor_validation_policy_v1"
EVIDENCE_VALIDATION_POLICY_SCHEMA_V1 = "factor_evidence_validation_policy_v1"
VALIDATION_DECISION_SCHEMA_V1 = "factor_validation_decision_v1"


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


def _fraction(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be within [0, 1]")
    try:
        normalized = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be within [0, 1]") from exc
    if not math.isfinite(normalized) or not 0.0 <= normalized <= 1.0:
        raise ValueError(f"{name} must be within [0, 1]")
    return normalized


def _nonnegative_float(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a non-negative finite number")
    try:
        normalized = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a non-negative finite number") from exc
    if not math.isfinite(normalized) or normalized < 0.0:
        raise ValueError(f"{name} must be a non-negative finite number")
    return normalized


@dataclass(frozen=True)
class ValidationPolicy:
    policy_id: str
    version: str
    min_n_eligible: int
    min_coverage: float
    max_missing_rate: float
    min_oos_auc: float
    min_macro_period_auc: float
    min_weighted_period_auc: float
    min_n_folds: int
    min_fold_positive_fraction: float
    min_sign_consistency: float
    max_brier: float
    max_logloss: float
    max_ece: float
    schema_version: str = VALIDATION_POLICY_SCHEMA_V1

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_id", _nonempty(self.policy_id, "policy_id"))
        object.__setattr__(self, "version", _nonempty(self.version, "version"))
        object.__setattr__(
            self,
            "min_n_eligible",
            _nonnegative_int(self.min_n_eligible, "min_n_eligible"),
        )
        object.__setattr__(
            self,
            "min_n_folds",
            _nonnegative_int(self.min_n_folds, "min_n_folds"),
        )

        for name in (
            "min_coverage",
            "max_missing_rate",
            "min_oos_auc",
            "min_macro_period_auc",
            "min_weighted_period_auc",
            "min_fold_positive_fraction",
            "min_sign_consistency",
        ):
            object.__setattr__(self, name, _fraction(getattr(self, name), name))

        for name in ("max_brier", "max_logloss", "max_ece"):
            object.__setattr__(
                self,
                name,
                _nonnegative_float(getattr(self, name), name),
            )

        if self.schema_version != VALIDATION_POLICY_SCHEMA_V1:
            raise ValueError("unsupported ValidationPolicy schema_version")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validation_policy_hash(policy: ValidationPolicy) -> str:
    payload = canonical_json(policy.to_dict())
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ValidationDecision:
    factor_id: str
    factor_version: str
    research_validation_status: ValidationStatus | str
    temporal_integrity: ValidationStatus | str
    reasons: tuple[str, ...]
    research_artifact_hash: str
    research_run_id: str
    policy_id: str
    policy_version: str
    policy_hash: str
    validation_evidence_bundle_hash: str | None = None
    schema_version: str = VALIDATION_DECISION_SCHEMA_V1

    def __post_init__(self) -> None:
        object.__setattr__(self, "factor_id", _nonempty(self.factor_id, "factor_id"))
        object.__setattr__(
            self,
            "factor_version",
            _nonempty(self.factor_version, "factor_version"),
        )
        object.__setattr__(
            self,
            "research_validation_status",
            ValidationStatus(self.research_validation_status),
        )
        object.__setattr__(
            self,
            "temporal_integrity",
            ValidationStatus(self.temporal_integrity),
        )
        object.__setattr__(self, "reasons", tuple(self.reasons))
        object.__setattr__(
            self,
            "research_artifact_hash",
            _nonempty(self.research_artifact_hash, "research_artifact_hash"),
        )
        object.__setattr__(
            self,
            "research_run_id",
            _nonempty(self.research_run_id, "research_run_id"),
        )
        object.__setattr__(self, "policy_id", _nonempty(self.policy_id, "policy_id"))
        object.__setattr__(
            self,
            "policy_version",
            _nonempty(self.policy_version, "policy_version"),
        )
        object.__setattr__(
            self,
            "policy_hash",
            _nonempty(self.policy_hash, "policy_hash"),
        )
        if self.validation_evidence_bundle_hash is not None:
            object.__setattr__(
                self,
                "validation_evidence_bundle_hash",
                _nonempty(
                    self.validation_evidence_bundle_hash,
                    "validation_evidence_bundle_hash",
                ),
            )

        if self.schema_version != VALIDATION_DECISION_SCHEMA_V1:
            raise ValueError("unsupported ValidationDecision schema_version")


def validate_factor_result(
    result: FactorResult,
    policy: ValidationPolicy,
) -> ValidationDecision:
    if result.research_validation_status != ValidationStatus.PENDING:
        raise ValueError("validator requires research_validation_status=PENDING")

    reasons: list[str] = []

    if result.temporal_integrity != ValidationStatus.PASS:
        reasons.append("TEMPORAL_INTEGRITY_NOT_PASS")

    if result.n_eligible < policy.min_n_eligible:
        reasons.append("N_ELIGIBLE_BELOW_MIN")
    if result.coverage < policy.min_coverage:
        reasons.append("COVERAGE_BELOW_MIN")
    if result.missing_rate > policy.max_missing_rate:
        reasons.append("MISSING_RATE_ABOVE_MAX")
    if result.n_folds < policy.min_n_folds:
        reasons.append("N_FOLDS_BELOW_MIN")
    if result.fold_positive_fraction < policy.min_fold_positive_fraction:
        reasons.append("FOLD_POSITIVE_FRACTION_BELOW_MIN")
    if result.sign_consistency < policy.min_sign_consistency:
        reasons.append("SIGN_CONSISTENCY_BELOW_MIN")

    minimum_metrics = (
        ("oos_auc", policy.min_oos_auc, "OOS_AUC"),
        (
            "macro_period_auc",
            policy.min_macro_period_auc,
            "MACRO_PERIOD_AUC",
        ),
        (
            "weighted_period_auc",
            policy.min_weighted_period_auc,
            "WEIGHTED_PERIOD_AUC",
        ),
    )
    for field, threshold, prefix in minimum_metrics:
        value = getattr(result, field)
        if value is None:
            reasons.append(f"{prefix}_MISSING")
        elif not math.isfinite(float(value)):
            reasons.append(f"{prefix}_MISSING")
        elif value < threshold:
            reasons.append(f"{prefix}_BELOW_MIN")

    maximum_metrics = (
        ("brier", policy.max_brier, "BRIER"),
        ("logloss", policy.max_logloss, "LOGLOSS"),
        ("ece", policy.max_ece, "ECE"),
    )
    for field, threshold, prefix in maximum_metrics:
        value = getattr(result, field)
        if value is None:
            reasons.append(f"{prefix}_MISSING")
        elif not math.isfinite(float(value)):
            reasons.append(f"{prefix}_MISSING")
        elif value > threshold:
            reasons.append(f"{prefix}_ABOVE_MAX")

    status = (
        ValidationStatus.FAIL
        if reasons
        else ValidationStatus.PASS
    )

    return ValidationDecision(
        factor_id=result.factor_id,
        factor_version=result.factor_version,
        research_validation_status=status,
        temporal_integrity=result.temporal_integrity,
        reasons=tuple(reasons),
        research_artifact_hash=result.artifact_hash,
        research_run_id=result.run_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        policy_hash=validation_policy_hash(policy),
    )


def validate_factor_result_with_evidence(
    result: FactorResult,
    policy: ValidationPolicy,
    bundle: FactorValidationEvidenceBundle,
) -> ValidationDecision:
    if not isinstance(bundle, FactorValidationEvidenceBundle):
        raise ValueError(
            "bundle must be FactorValidationEvidenceBundle"
        )

    if (
        bundle.factor_id != result.factor_id
        or bundle.factor_version != result.factor_version
    ):
        raise ValueError(
            "factor identity mismatch between result and evidence bundle"
        )
    if bundle.factor_result_artifact_hash != result.artifact_hash:
        raise ValueError(
            "artifact hash mismatch between result and evidence bundle"
        )
    if bundle.factor_result_run_id != result.run_id:
        raise ValueError(
            "run mismatch between result and evidence bundle"
        )

    decision = validate_factor_result(result, policy)
    return replace(
        decision,
        validation_evidence_bundle_hash=(
            factor_validation_evidence_bundle_hash(bundle)
        ),
    )


@dataclass(frozen=True)
class EvidenceValidationPolicy:
    policy_id: str
    version: str
    required_null_method: str
    min_null_shuffles: int
    max_null_macro_period_auc_p95: float
    max_null_weighted_period_auc_p95: float
    required_baseline_method: str
    min_baseline_stability_fraction: float
    schema_version: str = EVIDENCE_VALIDATION_POLICY_SCHEMA_V1

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_id", _nonempty(self.policy_id, "policy_id"))
        object.__setattr__(self, "version", _nonempty(self.version, "version"))
        object.__setattr__(
            self,
            "required_null_method",
            _nonempty(self.required_null_method, "required_null_method"),
        )
        object.__setattr__(
            self,
            "required_baseline_method",
            _nonempty(self.required_baseline_method, "required_baseline_method"),
        )
        minimum = _nonnegative_int(self.min_null_shuffles, "min_null_shuffles")
        if minimum < 1:
            raise ValueError("min_null_shuffles must be >= 1")
        object.__setattr__(self, "min_null_shuffles", minimum)
        for name in (
            "max_null_macro_period_auc_p95",
            "max_null_weighted_period_auc_p95",
            "min_baseline_stability_fraction",
        ):
            object.__setattr__(self, name, _fraction(getattr(self, name), name))
        if self.schema_version != EVIDENCE_VALIDATION_POLICY_SCHEMA_V1:
            raise ValueError("unsupported EvidenceValidationPolicy schema_version")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evidence_validation_policy_hash(policy: EvidenceValidationPolicy) -> str:
    payload = canonical_json(policy.to_dict())
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_factor_result_with_full_evidence(
    result: FactorResult,
    policy: EvidenceValidationPolicy,
    bundle: FactorValidationEvidenceBundle,
    null_control: FactorNullControlEvidence,
    baseline: FactorBaselineRelativeEvidence,
) -> ValidationDecision:
    if not isinstance(policy, EvidenceValidationPolicy):
        raise ValueError("policy must be EvidenceValidationPolicy")
    if not isinstance(bundle, FactorValidationEvidenceBundle):
        raise ValueError("bundle must be FactorValidationEvidenceBundle")
    if not isinstance(null_control, FactorNullControlEvidence):
        raise ValueError("null_control must be FactorNullControlEvidence")
    if not isinstance(baseline, FactorBaselineRelativeEvidence):
        raise ValueError("baseline must be FactorBaselineRelativeEvidence")
    if result.research_validation_status != ValidationStatus.PENDING:
        raise ValueError("validator requires research_validation_status=PENDING")

    if (
        bundle.factor_id != result.factor_id
        or bundle.factor_version != result.factor_version
    ):
        raise ValueError("factor identity mismatch between result and evidence bundle")
    if bundle.factor_result_artifact_hash != result.artifact_hash:
        raise ValueError("artifact hash mismatch between result and evidence bundle")
    if bundle.factor_result_run_id != result.run_id:
        raise ValueError("run mismatch between result and evidence bundle")

    for name, evidence in (
        ("null", null_control),
        ("baseline", baseline),
    ):
        if (
            evidence.factor_id != result.factor_id
            or evidence.factor_version != result.factor_version
        ):
            raise ValueError(f"{name} evidence factor identity mismatch")
        if evidence.factor_spec_hash != bundle.factor_spec_hash:
            raise ValueError(f"{name} evidence factor spec hash mismatch")
        if evidence.oos_predictions_hash != bundle.oos_predictions_hash:
            raise ValueError(f"{name} evidence OOS hash mismatch")

    if null_control.oos_run_id != bundle.oos_run_id:
        raise ValueError("null evidence OOS run mismatch with bundle")
    if null_control.run_id != bundle.null_control_run_id:
        raise ValueError("null evidence run mismatch with bundle")
    if (
        factor_null_control_evidence_hash(null_control)
        != bundle.null_control_evidence_hash
    ):
        raise ValueError("null evidence hash mismatch with bundle")

    if baseline.factor_result_artifact_hash != result.artifact_hash:
        raise ValueError("baseline evidence result artifact hash mismatch")
    if baseline.source_run_id != result.run_id:
        raise ValueError("baseline evidence source run mismatch")
    if baseline.run_id != bundle.baseline_relative_run_id:
        raise ValueError("baseline evidence run mismatch with bundle")
    if (
        factor_baseline_relative_evidence_hash(baseline)
        != bundle.baseline_relative_evidence_hash
    ):
        raise ValueError("baseline evidence hash mismatch with bundle")

    reasons: list[str] = []

    if result.temporal_integrity != ValidationStatus.PASS:
        reasons.append("TEMPORAL_INTEGRITY_NOT_PASS")

    if null_control.method != policy.required_null_method:
        reasons.append("NULL_CONTROL_METHOD_MISMATCH")
    if null_control.n_shuffles < policy.min_null_shuffles:
        reasons.append("NULL_SHUFFLES_BELOW_MIN")

    macro_p95 = null_control.macro_period_auc.p95
    if macro_p95 is None:
        reasons.append("NULL_MACRO_PERIOD_AUC_P95_MISSING")
    elif macro_p95 >= policy.max_null_macro_period_auc_p95:
        reasons.append("NULL_MACRO_PERIOD_AUC_P95_NOT_BELOW_MAX")

    weighted_p95 = null_control.weighted_period_auc.p95
    if weighted_p95 is None:
        reasons.append("NULL_WEIGHTED_PERIOD_AUC_P95_MISSING")
    elif weighted_p95 >= policy.max_null_weighted_period_auc_p95:
        reasons.append("NULL_WEIGHTED_PERIOD_AUC_P95_NOT_BELOW_MAX")

    if baseline.baseline_method != policy.required_baseline_method:
        reasons.append("BASELINE_METHOD_MISMATCH")

    if baseline.delta_brier is None or baseline.delta_brier <= 0.0:
        reasons.append("DELTA_BRIER_NOT_POSITIVE")
    if baseline.delta_logloss is None or baseline.delta_logloss <= 0.0:
        reasons.append("DELTA_LOGLOSS_NOT_POSITIVE")
    if (
        baseline.delta_macro_period_auc is None
        or baseline.delta_macro_period_auc <= 0.0
    ):
        reasons.append("DELTA_MACRO_PERIOD_AUC_NOT_POSITIVE")

    stability = max(
        baseline.pct_folds_brier_improved,
        baseline.pct_folds_logloss_improved,
    )
    if stability < policy.min_baseline_stability_fraction:
        reasons.append("BASELINE_STABILITY_BELOW_MIN")

    status = ValidationStatus.FAIL if reasons else ValidationStatus.PASS
    return ValidationDecision(
        factor_id=result.factor_id,
        factor_version=result.factor_version,
        research_validation_status=status,
        temporal_integrity=result.temporal_integrity,
        reasons=tuple(reasons),
        research_artifact_hash=result.artifact_hash,
        research_run_id=result.run_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        policy_hash=evidence_validation_policy_hash(policy),
        validation_evidence_bundle_hash=(
            factor_validation_evidence_bundle_hash(bundle)
        ),
    )
