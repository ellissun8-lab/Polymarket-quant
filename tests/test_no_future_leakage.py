"""Spec Test G: timestamp assertion utility must reject future information."""

from __future__ import annotations

import pytest

from std0_quant.audit.leakage import (
    FutureLeakageError,
    assert_no_future_leakage,
    assert_ordered_non_increasing,
)

PREDICTION_TS = 1_700_000_100_000


def test_feature_after_prediction_raises() -> None:
    with pytest.raises(FutureLeakageError, match="future leakage"):
        assert_no_future_leakage(PREDICTION_TS + 1, PREDICTION_TS, "book_mid")


def test_feature_before_prediction_passes() -> None:
    assert_no_future_leakage(PREDICTION_TS - 1, PREDICTION_TS, "book_mid")


def test_equal_timestamp_rejected_in_strict_mode() -> None:
    """Strict mode: an observation stamped exactly at prediction time cannot
    be proven to predate the decision."""
    with pytest.raises(FutureLeakageError):
        assert_no_future_leakage(PREDICTION_TS, PREDICTION_TS, "book_mid")
    # explicitly relaxed
    assert_no_future_leakage(
        PREDICTION_TS, PREDICTION_TS, "book_mid", strict=False
    )


def test_missing_timestamps_rejected() -> None:
    with pytest.raises(FutureLeakageError, match="missing timestamp"):
        assert_no_future_leakage(None, PREDICTION_TS)
    with pytest.raises(FutureLeakageError, match="missing timestamp"):
        assert_no_future_leakage(PREDICTION_TS - 1, None)


def test_error_message_carries_context() -> None:
    with pytest.raises(FutureLeakageError, match="y30_feature"):
        assert_no_future_leakage(PREDICTION_TS + 30_000, PREDICTION_TS,
                                 context="y30_feature")


def test_non_increasing_history_accepted_and_violation_rejected() -> None:
    assert_ordered_non_increasing([300, 200, 100], "snapshots")
    with pytest.raises(FutureLeakageError):
        assert_ordered_non_increasing([100, 200, 300], "snapshots")
