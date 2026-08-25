"""Point-in-time integrity utilities (Phase 1.5, spec section 10).

std0's public fills carry **epoch-second** timestamps only: the true fill
time lies somewhere inside ``[ts, ts+1000)`` ms. Millisecond-resolution
market data (Binance ticks / CLOB book events) that falls in the *same
second* as a fill therefore CANNOT be assumed to precede (or follow) that
fill. This module enforces that rule mechanically.

Semantics (frozen for this audit):

    assert_feature_precedes_prediction(f, p, safety_gap_ms=g) passes
    if and only if    f < p - g
    (strict inequality; ``f == p`` and ``f > p`` always reject).

Supported cutoff modes (tests / callers):

* ``strict_before_second``      -> safety_gap_ms = 0     (f < p)
* ``prediction_minus_1000ms``   -> safety_gap_ms = 1000  (f < p - 1000)
* ``prediction_minus_2000ms``   -> safety_gap_ms = 2000  (f < p - 2000)

Recommended default for Phase 2 feature work: ``safety_gap_ms = 1000``.

This module is a read-only audit utility: it never mutates data.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Named cutoff modes (spec section 10).
CUTOFF_STRICT_BEFORE_SECOND = "strict_before_second"
CUTOFF_PREDICTION_MINUS_1000MS = "prediction_minus_1000ms"
CUTOFF_PREDICTION_MINUS_2000MS = "prediction_minus_2000ms"

CUTOFF_MODES: dict[str, int] = {
    CUTOFF_STRICT_BEFORE_SECOND: 0,
    CUTOFF_PREDICTION_MINUS_1000MS: 1000,
    CUTOFF_PREDICTION_MINUS_2000MS: 2000,
}

# Default recommended gap: fills are second-granular, so features must be
# strictly older than the previous second boundary relative to the fill.
DEFAULT_SAFETY_GAP_MS = 1000


class PointInTimeViolation(AssertionError):
    """Raised when a feature timestamp does not strictly precede the
    prediction timestamp by at least the configured safety gap."""


def resolve_safety_gap_ms(safety_gap_ms: int | None = None,
                          mode: str | None = None) -> int:
    """Resolve the effective gap from an explicit value or a named mode."""
    if mode is not None:
        if mode not in CUTOFF_MODES:
            raise ValueError(
                f"unknown cutoff mode {mode!r}; expected one of "
                f"{sorted(CUTOFF_MODES)}"
            )
        if safety_gap_ms is not None and safety_gap_ms != CUTOFF_MODES[mode]:
            raise ValueError(
                "safety_gap_ms and mode disagree: "
                f"{safety_gap_ms} vs {CUTOFF_MODES[mode]}"
            )
        return CUTOFF_MODES[mode]
    return DEFAULT_SAFETY_GAP_MS if safety_gap_ms is None else int(safety_gap_ms)


def assert_feature_precedes_prediction(
    feature_source_timestamp_ms: int | None,
    prediction_timestamp_ms: int | None,
    safety_gap_ms: int | None = None,
    *,
    mode: str | None = None,
    context: dict[str, Any] | None = None,
) -> int:
    """Assert the feature timestamp strictly precedes the prediction.

    Passes iff ``feature_ts < prediction_ts - safety_gap_ms``. Rejects
    (raises :class:`PointInTimeViolation`):

    * either timestamp missing (never guess);
    * ``feature_ts == prediction_ts`` (same instant / same second boundary);
    * ``feature_ts > prediction_ts`` (future information);
    * ``feature_ts`` within the safety gap of the prediction (ambiguous
      ordering for second-granularity fill timestamps).

    Returns the effective safety gap used (for logging/audit trails).
    """
    gap = resolve_safety_gap_ms(safety_gap_ms, mode)
    detail = context or {}
    if feature_source_timestamp_ms is None:
        raise PointInTimeViolation(
            "feature timestamp is None; refusing to assume it precedes "
            f"prediction {prediction_timestamp_ms!r} (context={detail})"
        )
    if prediction_timestamp_ms is None:
        raise PointInTimeViolation(
            "prediction timestamp is None; cannot verify feature "
            f"{feature_source_timestamp_ms!r} (context={detail})"
        )
    if feature_source_timestamp_ms >= prediction_timestamp_ms:
        raise PointInTimeViolation(
            f"feature timestamp {feature_source_timestamp_ms} is not strictly "
            f"before prediction {prediction_timestamp_ms} (gap={gap}ms, "
            f"context={detail})"
        )
    if feature_source_timestamp_ms >= prediction_timestamp_ms - gap:
        raise PointInTimeViolation(
            f"feature timestamp {feature_source_timestamp_ms} is within the "
            f"{gap}ms safety gap of prediction {prediction_timestamp_ms} "
            f"(requires feature < prediction - {gap}; context={detail})"
        )
    return gap


def is_same_second(feature_ts_ms: int, prediction_ts_ms: int) -> bool:
    """True when both timestamps fall inside the same epoch second.

    Same-second millisecond market data must never be assumed to precede a
    second-granularity fill timestamp (README / Phase 1.5 report rule).
    """
    return feature_ts_ms // 1000 == prediction_ts_ms // 1000


def self_check() -> list[str]:
    """Runtime self-validation of the rejection rules (used by the audit
    report's timestamp-integrity row). Returns a list of failure
    descriptions; empty list means all rules behave correctly."""
    failures: list[str] = []
    cases = [
        # (feature, prediction, gap, should_pass, label)
        (1000, 1000, 0, False, "equal timestamps strict mode"),
        (1001, 1000, 0, False, "future feature strict mode"),
        (999, 1000, 0, True, "feature just before prediction strict mode"),
        (1000, 2000, 1000, False, "feature exactly prediction-1000ms"),
        (999, 2000, 1000, True, "feature prediction-1001ms passes 1000ms gap"),
        (1500, 2000, 1000, False, "feature inside 1000ms gap"),
        (1500, 2000, 2000, False, "feature inside 2000ms gap"),
        (None, 1000, 1000, False, "missing feature timestamp"),
        (1000, None, 1000, False, "missing prediction timestamp"),
    ]
    for feature, prediction, gap, should_pass, label in cases:
        try:
            assert_feature_precedes_prediction(feature, prediction, gap)
            passed = True
        except PointInTimeViolation:
            passed = False
        if passed != should_pass:
            failures.append(
                f"self-check failed: {label} (feature={feature}, "
                f"prediction={prediction}, gap={gap})"
            )
    return failures
