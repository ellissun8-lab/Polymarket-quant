"""Tests for the point-in-time integrity utility (spec section 10 / test O)."""

from __future__ import annotations

import pytest

from std0_quant.audit.point_in_time import (
    CUTOFF_MODES,
    CUTOFF_PREDICTION_MINUS_1000MS,
    CUTOFF_PREDICTION_MINUS_2000MS,
    CUTOFF_STRICT_BEFORE_SECOND,
    PointInTimeViolation,
    assert_feature_precedes_prediction,
    is_same_second,
    resolve_safety_gap_ms,
    self_check,
)

P = 1_787_480_700_000  # a fill timestamp (second boundary, ms)


class TestCutoffModes:
    def test_modes_map_to_expected_gaps(self) -> None:
        assert CUTOFF_MODES[CUTOFF_STRICT_BEFORE_SECOND] == 0
        assert CUTOFF_MODES[CUTOFF_PREDICTION_MINUS_1000MS] == 1000
        assert CUTOFF_MODES[CUTOFF_PREDICTION_MINUS_2000MS] == 2000

    def test_resolve_defaults_to_1000ms(self) -> None:
        assert resolve_safety_gap_ms() == 1000

    def test_unknown_mode_rejected(self) -> None:
        with pytest.raises(ValueError):
            resolve_safety_gap_ms(mode="whenever")


class TestStrictBeforeSecond:
    def test_equal_timestamps_rejected(self) -> None:
        with pytest.raises(PointInTimeViolation):
            assert_feature_precedes_prediction(P, P, 0)

    def test_future_feature_rejected(self) -> None:
        with pytest.raises(PointInTimeViolation):
            assert_feature_precedes_prediction(P + 1, P, 0)

    def test_past_feature_passes(self) -> None:
        assert assert_feature_precedes_prediction(P - 1, P, 0) == 0


class TestSafetyGaps:
    def test_gap_1000_boundary(self) -> None:
        # exactly prediction-1000ms: NOT strictly before prediction-1000 -> reject
        with pytest.raises(PointInTimeViolation):
            assert_feature_precedes_prediction(P - 1000, P, 1000)
        # one ms earlier passes
        assert assert_feature_precedes_prediction(P - 1001, P, 1000) == 1000

    def test_gap_2000_boundary(self) -> None:
        with pytest.raises(PointInTimeViolation):
            assert_feature_precedes_prediction(P - 2000, P, 2000)
        assert assert_feature_precedes_prediction(P - 2001, P, 2000) == 2000

    def test_same_second_tick_rejected_under_default_gap(self) -> None:
        # a millisecond tick inside the fill's own second must never pass
        with pytest.raises(PointInTimeViolation):
            assert_feature_precedes_prediction(P + 500, P, 1000)
        with pytest.raises(PointInTimeViolation):
            assert_feature_precedes_prediction(P, P, 1000)

    def test_mode_equivalent_to_explicit_gap(self) -> None:
        assert assert_feature_precedes_prediction(
            P - 1500, P, mode=CUTOFF_PREDICTION_MINUS_1000MS) == 1000
        with pytest.raises(PointInTimeViolation):
            assert_feature_precedes_prediction(
                P - 500, P, mode=CUTOFF_PREDICTION_MINUS_1000MS)

    def test_gap_and_mode_disagreement_rejected(self) -> None:
        with pytest.raises(ValueError):
            assert_feature_precedes_prediction(P - 5000, P, 2000,
                                               mode=CUTOFF_PREDICTION_MINUS_1000MS)


class TestMissingTimestamps:
    def test_missing_feature_rejected(self) -> None:
        with pytest.raises(PointInTimeViolation):
            assert_feature_precedes_prediction(None, P, 1000)

    def test_missing_prediction_rejected(self) -> None:
        with pytest.raises(PointInTimeViolation):
            assert_feature_precedes_prediction(P - 5000, None, 1000)


class TestSameSecondHelper:
    def test_detects_same_second(self) -> None:
        assert is_same_second(P, P + 999) is True
        assert is_same_second(P, P + 1000) is False  # next second
        assert is_same_second(P - 1, P) is False  # P is a second boundary


class TestSelfCheck:
    def test_self_check_reports_no_failures(self) -> None:
        assert self_check() == []

    def test_self_check_detects_breakage(self) -> None:
        # sanity: the checker itself must be able to fail
        from std0_quant.audit import point_in_time as pit
        original = pit.assert_feature_precedes_prediction

        def broken(feature, prediction, safety_gap_ms=None, **_kw):
            return 0  # never rejects -> self-check must flag it

        pit.assert_feature_precedes_prediction = broken
        try:
            failures = pit.self_check()
        finally:
            pit.assert_feature_precedes_prediction = original
        assert failures != []
