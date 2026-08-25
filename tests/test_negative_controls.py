"""Tests N - Audit 6: negative controls (shuffle + future-window placebo)."""

from __future__ import annotations

import pytest

numpy = pytest.importorskip("numpy")
sklearn = pytest.importorskip("sklearn")

import numpy as np  # noqa: E402

from std0_quant.audit.buy_only_sensitivity import MarketFillWindows  # noqa: E402
from std0_quant.audit.negative_controls import (  # noqa: E402
    T0_FEATURE_NAMES,
    build_t0_features,
    cross_validated_auc,
    run_future_window_placebo,
    run_global_shuffle_diagnostic,
    run_shuffle_control,
    shuffle_labels_within_week,
    window_outcome,
)

T0 = 1_787_500_000_000
END = T0 + 300_000

# A fixed Monday: 2026-01-05T00:00:00Z
WEEK0 = 1_767_571_200_000
WEEK = 7 * 86_400_000


class TestShuffleWithinWeek:
    def test_preserves_weekly_label_counts(self) -> None:
        labels = [1] * 30 + [0] * 70 + [1] * 90 + [0] * 10
        weeks = ["w0"] * 100 + ["w1"] * 100
        rng = np.random.default_rng(7)
        shuffled = shuffle_labels_within_week(labels, weeks, rng)
        assert list(shuffled[:100]).count(1) == 30
        assert list(shuffled[100:]).count(1) == 90

    def test_permutes_only_within_groups(self) -> None:
        labels = [1, 0, 1, 0, 1, 1, 0, 0]
        weeks = ["a", "a", "a", "a", "b", "b", "b", "b"]
        rng = np.random.default_rng(3)
        shuffled = shuffle_labels_within_week(labels, weeks, rng)
        assert sorted(shuffled[:4]) == [0, 0, 1, 1]
        assert sorted(shuffled[4:]) == [0, 0, 1, 1]

    def test_deterministic_given_seed(self) -> None:
        labels = ([1, 0] * 50) + ([1] * 5 + [0] * 45)
        weeks = ["w0"] * 100 + ["w1"] * 50
        s1 = shuffle_labels_within_week(labels, weeks, np.random.default_rng(11))
        s2 = shuffle_labels_within_week(labels, weeks, np.random.default_rng(11))
        assert list(s1) == list(s2)

    def test_identical_labels_unchanged(self) -> None:
        labels = [1] * 10
        weeks = ["w"] * 10
        shuffled = shuffle_labels_within_week(labels, weeks, np.random.default_rng(1))
        assert list(shuffled) == labels


class TestCrossValidatedAuc:
    def test_perfect_feature_high_auc(self) -> None:
        rng = np.random.default_rng(0)
        X = np.column_stack([np.arange(200, dtype=float)])
        y = (X[:, 0] < 100).astype(int)
        assert cross_validated_auc(X, y) > 0.9

    def test_noise_feature_near_half(self) -> None:
        rng = np.random.default_rng(0)
        X = rng.normal(size=(1000, 3))
        y = rng.integers(0, 2, size=1000)
        assert 0.4 < cross_validated_auc(X, y) < 0.6


class TestShuffleControl:
    def test_noise_features_pass(self) -> None:
        rng = np.random.default_rng(42)
        n = 2000
        X = rng.normal(size=(n, 3))
        y = rng.integers(0, 2, size=n)
        weeks = (["2026-W02"] * (n // 2)) + (["2026-W03"] * (n // 2))
        result = run_shuffle_control(X, y, weeks, n_shuffles=10, seed=1)
        assert result.status == "PASS"
        assert result.auc_mean == pytest.approx(0.5, abs=0.05)
        assert result.auc_p95 <= 0.55

    def test_week_structure_leak_fails(self) -> None:
        # A feature identifying the week + very different weekly base rates:
        # even after within-week shuffling the model beats 0.55, exactly the
        # red flag this control exists to catch.
        rng = np.random.default_rng(5)
        n_per_week = 400
        week0_y = np.array([1] * int(n_per_week * 0.9) +
                           [0] * (n_per_week - int(n_per_week * 0.9)))
        week1_y = np.array([1] * int(n_per_week * 0.1) +
                           [0] * (n_per_week - int(n_per_week * 0.1)))
        y = np.concatenate([week0_y, week1_y])
        X = np.column_stack([
            np.concatenate([np.zeros(n_per_week), np.ones(n_per_week)]),
            rng.normal(scale=0.01, size=2 * n_per_week),
        ])
        weeks = (["2026-W02"] * n_per_week) + (["2026-W03"] * n_per_week)
        result = run_shuffle_control(X, y, weeks, n_shuffles=5, seed=2)
        assert result.auc_p95 > 0.55
        assert result.status == "FAIL"

    def test_determinism(self) -> None:
        rng = np.random.default_rng(9)
        X = rng.normal(size=(400, 2))
        y = rng.integers(0, 2, size=400)
        weeks = (["w0"] * 200) + (["w1"] * 200)
        r1 = run_shuffle_control(X, y, weeks, n_shuffles=3, seed=123)
        r2 = run_shuffle_control(X, y, weeks, n_shuffles=3, seed=123)
        assert r1.auc_values == r2.auc_values

    def test_nan_features_rejected(self) -> None:
        X = np.array([[1.0, np.nan], [1.0, 2.0], [3.0, 1.0], [2.0, 2.0]])
        y = [1, 0, 1, 0]
        result = run_shuffle_control(X, y, ["w"] * 4, n_shuffles=1)
        assert result.status == "NOT_COMPUTABLE"
        assert "NaN" in result.error

    def test_single_class_rejected(self) -> None:
        X = np.ones((4, 2))
        result = run_shuffle_control(X, [1, 1, 1, 1], ["w"] * 4)
        assert result.status == "NOT_COMPUTABLE"

    def test_length_mismatch_rejected(self) -> None:
        X = np.ones((4, 2))
        result = run_shuffle_control(X, [1, 0], ["w"] * 4)
        assert result.status == "NOT_COMPUTABLE"


class TestGlobalShuffleDiagnostic:
    def test_noise_features_mean_near_half(self) -> None:
        rng = np.random.default_rng(4)
        X = rng.normal(size=(1000, 2))
        y = rng.integers(0, 2, size=1000)
        result = run_global_shuffle_diagnostic(X, y, n_shuffles=3, seed=1)
        assert result.auc_mean == pytest.approx(0.5, abs=0.06)
        # diagnostic carries a note; its status is not a verdict
        assert result.error is not None
        assert result.status == "NOT_COMPUTABLE"

    def test_single_class_returns_empty(self) -> None:
        X = np.ones((4, 2))
        result = run_global_shuffle_diagnostic(X, [1, 1, 1, 1])
        assert result.auc_values == []

    def test_nan_rejected(self) -> None:
        X = np.array([[1.0, np.nan], [1.0, 1.0]])
        result = run_global_shuffle_diagnostic(X, [1, 0])
        assert result.auc_values == []


class TestBuildT0Features:
    def _row(self, cid, **kw):
        start = kw.get("start_ms", WEEK0)
        base = {
            "condition_id": cid,
            "market_start_ms": start,
            "market_end_ms": start + 300_000,
            "initial_direction": kw.get("direction", "Up"),
            "initial_first_timestamp_ms": start + 10_000,
            "initial_qty": kw.get("initial_qty", 100.0),
            "first_opp_start_ms": start + 60_000,
            "first_opp_end_ms": start + 70_000,
            "first_opp_qty": kw.get("fo_qty", 50.0),
            "first_opp_fill_count": kw.get("fo_fills", 3),
            "first_opp_vwap": kw.get("fo_vwap", 0.55),
            "y30": kw.get("y30", 1),
            "y30_horizon_eligible": kw.get("eligible", True),
        }
        return base

    def test_features_and_week_keys(self) -> None:
        rows = [self._row("a"), self._row("b", start_ms=WEEK0 + WEEK,
                                          direction="Down", y30=0)]
        X, y, weeks, dropped = build_t0_features(rows)
        assert dropped == 0
        assert X.shape == (2, len(T0_FEATURE_NAMES))
        assert list(y) == [1, 0]
        assert weeks == ["2026-W02", "2026-W03"]
        # feature order matches T0_FEATURE_NAMES
        r = self._row("a")
        assert X[0][0] == pytest.approx(100.0)     # initial_qty
        assert X[0][1] == pytest.approx(10.0)      # s from start to initial
        assert X[0][2] == pytest.approx(290.0)     # s remaining at initial
        assert X[0][3] == pytest.approx(50.0)      # first_opp_qty
        assert X[0][4] == pytest.approx(3.0)       # first_opp_fill_count
        assert X[0][5] == pytest.approx(0.55)      # first_opp_vwap
        assert X[0][6] == pytest.approx(10.0)      # first_opp_duration_s
        assert X[0][7] == pytest.approx(230.0)     # s remaining at t0
        assert X[0][8] == pytest.approx(1.0)       # initial_direction_is_up
        assert X[1][8] == pytest.approx(0.0)       # Down

    def test_censored_and_incomplete_rows_counted(self) -> None:
        rows = [
            self._row("ok"),
            self._row("censored", eligible=False),
            self._row("missing_qty", initial_qty=None),
            self._row("no_fo", fo_qty=None, first_opp_start_ms=None,
                      first_opp_end_ms=None, y30=None, eligible=False),
        ]
        X, y, weeks, dropped = build_t0_features(rows)
        assert X.shape[0] == 1
        assert dropped == 3

    def test_empty_rows(self) -> None:
        X, y, weeks, dropped = build_t0_features([])
        assert X.shape[0] == 0
        assert dropped == 0


class TestFutureWindows:
    def mk(self, buys=None, sells=None, end=END, initial="Up", t0=T0):
        return MarketFillWindows(
            t0_ms=t0, initial_direction=initial,
            buy_ts_by_outcome=buys or {}, sell_ts_by_outcome=sells or {},
            market_end_ms=end,
        )

    def test_reference_window_matches_frozen_y30(self) -> None:
        fills = self.mk(buys={"Down": [T0 + 20_000]})
        label, eligible = window_outcome(fills, 0, 30)
        assert (label, eligible) == (1, True)

    def test_boundary_at_window_start_excluded(self) -> None:
        # (t0+30, t0+60]: event exactly at t0+30s is OUTSIDE
        fills = self.mk(buys={"Down": [T0 + 30_000]})
        label, eligible = window_outcome(fills, 30, 60)
        assert (label, eligible) == (0, True)

    def test_boundary_at_window_end_included(self) -> None:
        fills = self.mk(buys={"Down": [T0 + 60_000]})
        label, eligible = window_outcome(fills, 30, 60)
        assert (label, eligible) == (1, True)

    def test_beyond_window_end_excluded(self) -> None:
        fills = self.mk(buys={"Down": [T0 + 60_001]})
        label, eligible = window_outcome(fills, 30, 60)
        assert (label, eligible) == (0, True)

    def test_censoring(self) -> None:
        # market ends at t0+50s: (30,60] window does not fit -> censored
        fills = self.mk(buys={"Down": [T0 + 40_000]}, end=T0 + 50_000)
        label, eligible = window_outcome(fills, 30, 60)
        assert eligible is False
        assert label is None
        # but the frozen (0,30] window still fits (no event inside it)
        label, eligible = window_outcome(fills, 0, 30)
        assert (label, eligible) == (0, True)

    def test_censoring_boundary_exact_end(self) -> None:
        # market ends exactly at t0+60s: window fits
        fills = self.mk(end=T0 + 60_000)
        label, eligible = window_outcome(fills, 30, 60)
        assert eligible is True
        assert label == 0

    def test_run_future_window_placebo(self) -> None:
        markets = {
            "a": self.mk(buys={"Down": [T0 + 10_000]}),   # y30=1 only
            "b": self.mk(buys={"Down": [T0 + 45_000]}),   # Y30_60=1 only
            "c": self.mk(buys={"Down": [T0 + 75_000]}),   # Y60_90=1 only
            "d": self.mk(),                                # nothing
        }
        result = run_future_window_placebo(markets)
        ref = result.by_offsets(0, 30)
        w2 = result.by_offsets(30, 60)
        w3 = result.by_offsets(60, 90)
        assert ref.n_eligible == 4 and ref.n_positive == 1
        assert w2.n_eligible == 4 and w2.n_positive == 1
        assert w3.n_eligible == 4 and w3.n_positive == 1
        assert all(w.positive_rate == pytest.approx(0.25)
                   for w in (ref, w2, w3))
        assert result.status == "REPORTED"

    def test_all_censored_not_computable(self) -> None:
        markets = {"a": self.mk(end=T0 + 10_000)}
        result = run_future_window_placebo(markets)
        assert result.status == "NOT_COMPUTABLE"

    def test_empty(self) -> None:
        result = run_future_window_placebo({})
        assert result.status == "NOT_COMPUTABLE"
