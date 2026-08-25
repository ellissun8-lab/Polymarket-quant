"""Audit 6 - negative controls (Phase 1.5, spec section 9).

Two controls, both read-only:

**Control A - within-ISO-week label shuffle.** Permute ``y30`` labels only
within each ISO week (weekly base rates are preserved, the link between
features and labels is destroyed), then fit a logistic regression on
t0-observable features and score AUC under deterministic stratified
cross-validation. If the classifier can still beat 0.55 (95th percentile
across shuffles), the feature set carries label information that survives
randomization - a leakage / structure red flag -> FAIL. Otherwise PASS.

* No random train/test split is used anywhere: the only randomness is the
  seeded within-week permutation itself; the CV folds are deterministic
  (``StratifiedKFold(shuffle=False)``).
* ``p95 > 0.55`` is the spec's FAIL rule. AUC values near 0.5 are the
  expected negative-control result, not evidence of "no pattern" in the
  real labels.

**Control B - future-window placebo labels.** Recompute the same event
definition (BUY of the FirstOpposite direction) over later windows
``(t0+30s, t0+60s]`` and ``(t0+60s, t0+90s]`` with the same censoring
rule (window must fit inside market lifetime; censored is not negative).
Reported rates show whether the Y30 number is specific to the first 30
seconds or just the base rate of opposite BUYs at any time. Descriptive
only - a flat profile is not "proof of no structure" and a decaying
profile is not "proof of memory".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from std0_quant.audit.buy_only_sensitivity import MarketFillWindows
from std0_quant.audit.temporal_stability import iso_week_key
from std0_quant.events.event_ledger import Y30_HORIZON_SECONDS

DEFAULT_N_SHUFFLES = 20
DEFAULT_RANDOM_SEED = 20260824
AUC_FAIL_P95 = 0.55
CV_FOLDS = 5

# t0-observable features (all known when the FirstOpposite ends; nothing
# after t0 may appear here - enforced by design and spot-checked in tests).
T0_FEATURE_NAMES = (
    "initial_qty",
    "seconds_from_market_start_to_initial",
    "seconds_remaining_at_initial",
    "first_opp_qty",
    "first_opp_fill_count",
    "first_opp_vwap",
    "first_opp_duration_s",
    "seconds_remaining_at_t0",
    "initial_direction_is_up",
)


# ---------------------------------------------------------------------------
# Control A: within-week label shuffle
# ---------------------------------------------------------------------------

@dataclass
class ShuffleControlResult:
    n_samples: int = 0
    n_shuffles: int = 0
    seed: int = DEFAULT_RANDOM_SEED
    auc_mean: float | None = None
    auc_std: float | None = None
    auc_p95: float | None = None
    auc_max: float | None = None
    auc_values: list[float] = field(default_factory=list)
    error: str | None = None

    @property
    def status(self) -> str:
        if self.error is not None or self.auc_p95 is None:
            return "NOT_COMPUTABLE"
        return "FAIL" if self.auc_p95 > AUC_FAIL_P95 else "PASS"


def shuffle_labels_within_week(
    labels: Sequence[int],
    week_keys: Sequence[str],
    rng: np.random.Generator,
) -> np.ndarray:
    """Permute labels inside each week group (weekly rates preserved)."""
    labels_arr = np.asarray(labels, dtype=int)
    shuffled = labels_arr.copy()
    by_week: dict[str, list[int]] = {}
    for idx, week in enumerate(week_keys):
        by_week.setdefault(week, []).append(idx)
    for week in sorted(by_week):
        positions = np.asarray(by_week[week])
        shuffled[positions] = rng.permutation(labels_arr[positions])
    return shuffled


def cross_validated_auc(
    features: np.ndarray, labels: Sequence[int], n_folds: int = CV_FOLDS
) -> float:
    """Deterministic stratified CV AUC of a scaled logistic regression."""
    labels_arr = np.asarray(labels, dtype=int)
    cv = StratifiedKFold(n_splits=n_folds, shuffle=False)
    aucs: list[float] = []
    for train_idx, test_idx in cv.split(features, labels_arr):
        if len(np.unique(labels_arr[train_idx])) < 2:
            continue
        model = make_pipeline(
            StandardScaler(), LogisticRegression(max_iter=1000)
        )
        model.fit(features[train_idx], labels_arr[train_idx])
        if len(np.unique(labels_arr[test_idx])) < 2:
            continue
        proba = model.predict_proba(features[test_idx])[:, 1]
        aucs.append(roc_auc_score(labels_arr[test_idx], proba))
    if not aucs:
        raise ValueError("no fold had both classes; cannot compute AUC")
    return float(np.mean(aucs))


def run_shuffle_control(
    features: Sequence[Sequence[float]] | np.ndarray,
    labels: Sequence[int],
    week_keys: Sequence[str],
    n_shuffles: int = DEFAULT_N_SHUFFLES,
    seed: int = DEFAULT_RANDOM_SEED,
) -> ShuffleControlResult:
    """Control A. Features/labels aligned; week_keys group the shuffles."""
    result = ShuffleControlResult(n_shuffles=n_shuffles, seed=seed)
    X = np.asarray(features, dtype=float)
    y = np.asarray(labels, dtype=int)
    weeks = list(week_keys)
    if X.ndim != 2 or X.shape[0] != len(y) or len(weeks) != len(y):
        result.error = "features/labels/week_keys length mismatch"
        return result
    if len(y) == 0 or len(np.unique(y)) < 2:
        result.error = "labels must contain both classes"
        return result
    if not np.isfinite(X).all():
        result.error = "features contain NaN/inf (imputation is not allowed)"
        return result
    result.n_samples = len(y)
    rng = np.random.default_rng(seed)
    for _ in range(n_shuffles):
        shuffled = shuffle_labels_within_week(y, weeks, rng)
        result.auc_values.append(cross_validated_auc(X, shuffled))
    arr = np.asarray(result.auc_values)
    result.auc_mean = float(arr.mean())
    result.auc_std = float(arr.std())
    result.auc_p95 = float(np.percentile(arr, 95))
    result.auc_max = float(arr.max())
    return result


def run_global_shuffle_diagnostic(
    features: Sequence[Sequence[float]] | np.ndarray,
    labels: Sequence[int],
    n_shuffles: int = 5,
    seed: int = DEFAULT_RANDOM_SEED,
) -> ShuffleControlResult:
    """Reference diagnostic: fully random permutation (across all weeks).

    NOT part of the spec's PASS/FAIL rule. Purpose: interpret a failing
    within-week shuffle control. If the global-shuffle AUC is ~0.5 while
    the within-week AUC is high, the predictability comes from BETWEEN-
    week base-rate dispersion combined with time-drifting features (a
    calendar-structure effect), not from per-sample feature leakage.
    """
    result = ShuffleControlResult(
        n_shuffles=n_shuffles, seed=seed,
        error="diagnostic only; status is not meaningful",
    )
    X = np.asarray(features, dtype=float)
    y = np.asarray(labels, dtype=int)
    if X.ndim != 2 or X.shape[0] != len(y) or len(y) == 0:
        return result
    if len(np.unique(y)) < 2 or not np.isfinite(X).all():
        return result
    result.n_samples = len(y)
    rng = np.random.default_rng(seed)
    for _ in range(n_shuffles):
        result.auc_values.append(cross_validated_auc(X, rng.permutation(y)))
    arr = np.asarray(result.auc_values)
    result.auc_mean = float(arr.mean())
    result.auc_std = float(arr.std())
    result.auc_p95 = float(np.percentile(arr, 95))
    result.auc_max = float(arr.max())
    return result


def build_t0_features(
    rows: Sequence[Mapping[str, object]],
) -> tuple[np.ndarray, np.ndarray, list[str], int]:
    """t0-observable feature matrix from clean FirstOpposite ledger rows.

    Returns ``(X, y, week_keys, n_dropped)``. Rows with missing feature
    values are excluded and COUNTED (never silently dropped); horizon-
    ineligible rows are excluded because their label is censored.
    """
    X_rows: list[list[float]] = []
    ys: list[int] = []
    weeks: list[str] = []
    dropped = 0
    for row in rows:
        if row.get("first_opp_end_ms") is None or not row.get(
            "y30_horizon_eligible"
        ):
            dropped += 1
            continue
        start = row.get("market_start_ms")
        end = row.get("market_end_ms")
        first = row.get("initial_first_timestamp_ms")
        t0 = row.get("first_opp_end_ms")
        fo_start = row.get("first_opp_start_ms")
        features = [
            row.get("initial_qty"),
            (first - start) / 1000.0 if first is not None and start is not None else None,
            (end - first) / 1000.0 if first is not None and end is not None else None,
            row.get("first_opp_qty"),
            row.get("first_opp_fill_count"),
            row.get("first_opp_vwap"),
            (t0 - fo_start) / 1000.0 if fo_start is not None else None,
            (end - t0) / 1000.0 if end is not None else None,
            1.0 if row.get("initial_direction") == "Up" else 0.0,
        ]
        if (
            any(v is None for v in features)
            or start is None
            or not all(
                isinstance(v, (int, float)) and np.isfinite(v)
                for v in features
            )
        ):
            dropped += 1
            continue
        X_rows.append([float(v) for v in features])
        ys.append(int(row.get("y30")))
        weeks.append(iso_week_key(int(start)))
    if not X_rows:
        return np.empty((0, len(T0_FEATURE_NAMES))), np.empty(0, dtype=int), [], dropped
    return np.asarray(X_rows), np.asarray(ys, dtype=int), weeks, dropped


# ---------------------------------------------------------------------------
# Control B: future-window placebo labels
# ---------------------------------------------------------------------------

@dataclass
class WindowStats:
    start_offset_s: int
    end_offset_s: int
    n_markets: int = 0
    n_eligible: int = 0
    n_positive: int = 0

    @property
    def positive_rate(self) -> float | None:
        return self.n_positive / self.n_eligible if self.n_eligible else None


@dataclass
class FutureWindowPlaceboResult:
    windows: list[WindowStats] = field(default_factory=list)

    def by_offsets(self, start_s: int, end_s: int) -> WindowStats | None:
        for w in self.windows:
            if w.start_offset_s == start_s and w.end_offset_s == end_s:
                return w
        return None

    @property
    def status(self) -> str:
        computed = [w for w in self.windows if w.positive_rate is not None]
        return "REPORTED" if computed else "NOT_COMPUTABLE"


FUTURE_WINDOWS: tuple[tuple[int, int], ...] = (
    (0, Y30_HORIZON_SECONDS),  # reference: the frozen Y30 window itself
    (30, 60),
    (60, 90),
)


def window_outcome(
    fills: MarketFillWindows,
    start_offset_s: int,
    end_offset_s: int,
) -> tuple[int | None, bool]:
    """Event label over ``(t0+start, t0+end]`` with censoring.

    ``None`` means censored (window extends past market end); eligible
    markets get a 0/1 label. Event = BUY of the direction opposing the
    initial direction, i.e. the same event family as the frozen y30.
    """
    from std0_quant.audit.buy_only_sensitivity import opposite_outcome

    window_start = fills.t0_ms + start_offset_s * 1000
    window_end = fills.t0_ms + end_offset_s * 1000
    if fills.market_end_ms is None or fills.market_end_ms < window_end:
        return None, False
    opposite = opposite_outcome(fills.initial_direction)
    events = [
        ts
        for ts in fills.buy_ts_by_outcome.get(opposite, ())
        if ts is not None and window_start < ts <= window_end
    ]
    return (1 if events else 0), True


def run_future_window_placebo(
    markets: Mapping[str, MarketFillWindows],
    windows: Sequence[tuple[int, int]] = FUTURE_WINDOWS,
) -> FutureWindowPlaceboResult:
    result = FutureWindowPlaceboResult(
        windows=[
            WindowStats(start_offset_s=s, end_offset_s=e) for s, e in windows
        ]
    )
    for stats in result.windows:
        for fills in markets.values():
            stats.n_markets += 1
            label, eligible = window_outcome(
                fills, stats.start_offset_s, stats.end_offset_s
            )
            if eligible:
                stats.n_eligible += 1
                if label == 1:
                    stats.n_positive += 1
    return result
