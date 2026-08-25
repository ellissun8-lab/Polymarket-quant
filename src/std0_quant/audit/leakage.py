"""Future-leakage guard (spec Test G).

Phase 1 has no feature pipeline yet, but the assertion utility exists now so
that every future feature builder fails LOUDLY when it tries to use
information stamped after the prediction timestamp:

    assert_no_future_leakage(feature_ts_ms, prediction_ts_ms, context=...)

Rule: any market state / feature observation with
``feature_ts_ms > prediction_ts_ms`` is future information and raises
:class:`FutureLeakageError`.
"""

from __future__ import annotations

from std0_quant.timeutil import ms_to_iso


class FutureLeakageError(RuntimeError):
    """Raised when a feature timestamp exceeds the prediction timestamp."""


def assert_no_future_leakage(
    feature_ts_ms: int | None,
    prediction_ts_ms: int | None,
    context: str = "",
    *,
    strict: bool = True,
) -> None:
    """Assert *feature_ts_ms <= prediction_ts_ms*.

    ``strict=True`` (default) also rejects equal timestamps: an observation
    stamped exactly at the prediction time cannot be proven to have been
    available before the decision. Pass ``strict=False`` to allow equality
    (e.g. when the exchange guarantees in-second ordering).

    ``None`` timestamps are rejected as well: unknown availability is not
    evidence of pre-trade availability.
    """
    if feature_ts_ms is None or prediction_ts_ms is None:
        raise FutureLeakageError(
            f"missing timestamp (feature={feature_ts_ms}, "
            f"prediction={prediction_ts_ms}) context={context!r}: "
            "availability cannot be established"
        )
    leaked = feature_ts_ms > prediction_ts_ms or (strict and feature_ts_ms == prediction_ts_ms)
    if leaked:
        relation = ">" if feature_ts_ms > prediction_ts_ms else "=="
        raise FutureLeakageError(
            f"future leakage: feature timestamp {ms_to_iso(feature_ts_ms)} "
            f"({feature_ts_ms}) {relation} prediction timestamp "
            f"{ms_to_iso(prediction_ts_ms)} ({prediction_ts_ms}); context={context!r}"
        )


def assert_ordered_non_increasing(timestamps_ms: list[int], context: str = "") -> None:
    """Utility: assert a list of timestamps is non-increasing (e.g. a
    snapshot history must never claim observations from the future)."""
    for prev, curr in zip(timestamps_ms, timestamps_ms[1:]):
        if curr > prev:
            raise FutureLeakageError(
                f"timestamps not non-increasing at {curr} after {prev}; "
                f"context={context!r}"
            )
