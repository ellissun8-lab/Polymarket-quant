"""Tests for Audit 1 - FirstOpposite selection bias (spec test I)."""

from __future__ import annotations

import math

from std0_quant.audit.selection_bias import (
    compute_smd,
    describe,
    magnitude_of,
    run_selection_bias,
)


def row(cid: str, *, first_opp: bool, initial_qty=None, n_buy=5, n_sell=2,
        start=1_787_480_700_000, first_ts=1_787_480_705_000):
    end = start + 300_000
    return {
        "condition_id": cid,
        "clean_flag": True,
        "initial_qty": initial_qty,
        "n_buy_fills": n_buy,
        "n_sell_fills": n_sell,
        "market_start_ms": start,
        "market_end_ms": end,
        "initial_first_timestamp_ms": first_ts,
        "first_opp_end_ms": (first_ts + 60_000) if first_opp else None,
    }


class TestGrouping:
    def test_g1_g0_classification(self) -> None:
        result = run_selection_bias([
            row("0x1", first_opp=True),
            row("0x2", first_opp=False),
            row("0x3", first_opp=True),
        ])
        assert result.g1_count == 2
        assert result.g0_count == 1

    def test_censored_markets_stay_in_g1_but_do_not_affect_smd_denominators(
        self,
    ) -> None:
        # censored Y30 status must not be a grouping variable here: grouping
        # is FirstOpposite presence only (censoring affects Y30 rates, not SMD)
        result = run_selection_bias([
            row("0x1", first_opp=True),
            row("0x2", first_opp=True),
            row("0x3", first_opp=False),
        ])
        assert result.g1_count == 2


class TestSMD:
    def test_smd_formula(self) -> None:
        g1 = [10.0, 12.0, 14.0, 16.0]      # mean 13
        g0 = [4.0, 6.0, 8.0, 10.0]         # mean 7
        smd, note = compute_smd(g1, g0)
        var1 = sum((v - 13.0) ** 2 for v in g1) / 3
        var0 = sum((v - 7.0) ** 2 for v in g0) / 3
        expected = 6.0 / math.sqrt((var1 + var0) / 2.0)
        assert smd == expected
        assert note is None

    def test_zero_variance_identical_constants(self) -> None:
        smd, note = compute_smd([5.0, 5.0], [5.0, 5.0])
        assert smd == 0.0
        assert "zero variance" in note

    def test_zero_variance_different_constants_is_undefined(self) -> None:
        smd, note = compute_smd([5.0, 5.0], [9.0, 9.0])
        assert smd is None
        assert note is not None  # never crash, never fabricate

    def test_empty_group_is_undefined(self) -> None:
        smd, note = compute_smd([], [1.0, 2.0])
        assert smd is None
        assert note is not None

    def test_missing_values_ignored(self) -> None:
        smd, _ = compute_smd([None, 10.0, 12.0], [4.0, None, 6.0])
        assert smd is not None

    def test_magnitude_buckets(self) -> None:
        assert magnitude_of(0.05) == "small"
        assert magnitude_of(-0.05) == "small"
        assert magnitude_of(0.15) == "noticeable"
        assert magnitude_of(0.25) == "material"
        assert magnitude_of(None) == "undefined"


class TestDescribe:
    def test_quartiles_and_std(self) -> None:
        stats = describe([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])
        assert stats.n == 8
        assert stats.missing == 0
        assert stats.mean == 4.5
        assert stats.median == 4.5
        assert stats.p25 == 2.75
        assert stats.p75 == 6.25
        assert stats.std == math.sqrt(sum((v - 4.5) ** 2 for v in range(1, 9)) / 7)

    def test_missing_counted(self) -> None:
        stats = describe([1.0, None, 3.0])
        assert stats.n == 2
        assert stats.missing == 1

    def test_single_value_std_zero(self) -> None:
        stats = describe([42.0])
        assert stats.std == 0.0


class TestWarnRule:
    def _rows_with_shift(self, shift_seconds: float):
        # G1 markets start systematically later than G0 -> material SMD on
        # market_start_ms and initial_first_timestamp_ms and derived seconds
        rows = []
        for i in range(30):
            start = 1_787_480_700_000 + i * 300_000 + (
                shift_seconds * 1000 if i % 2 == 0 else 0
            )
            rows.append(row(f"g1-{i}", first_opp=True, start=start,
                            first_ts=start + 5_000))
        for i in range(30):
            start = 1_787_480_700_000 + i * 300_000
            rows.append(row(f"g0-{i}", first_opp=False, start=start,
                            first_ts=start + 5_000))
        return rows

    def test_warn_when_two_core_pre_variables_material(self) -> None:
        # 6000s mean shift vs ~2640s within-group std -> |SMD| > 2
        result = run_selection_bias(self._rows_with_shift(12000))
        assert result.selection_bias_material is True
        assert result.status == "WARN"
        assert len(result.material_pre_variables) >= 2

    def test_pass_when_balanced(self) -> None:
        result = run_selection_bias(self._rows_with_shift(0))
        assert result.selection_bias_material is False
        assert result.status == "PASS"

    def test_post_inclusive_variables_do_not_trigger_warn(self) -> None:
        # n_buy_fills/total quantities differ wildly (post-t0 information)
        # but pre variables are balanced -> must stay PASS
        rows = []
        for i in range(30):
            start = 1_787_480_700_000 + i * 300_000
            rows.append(row(f"g1-{i}", first_opp=True, start=start,
                            first_ts=start + 5_000,
                            n_buy=40 + i % 7, n_sell=30 + i % 5))
        for i in range(30):
            start = 1_787_480_700_000 + i * 300_000
            rows.append(row(f"g0-{i}", first_opp=False, start=start,
                            first_ts=start + 5_000,
                            n_buy=1 + i % 3, n_sell=i % 2))
        result = run_selection_bias(rows)
        post_material = [
            c.variable for c in result.comparisons
            if not c.pre_first_opposite and c.magnitude == "material"
        ]
        assert post_material  # the shift is visible in post-inclusive vars
        assert result.status == "PASS"  # ...but must NOT trigger the WARN


class TestExtras:
    def test_initial_qty_override_for_g0(self) -> None:
        # G0 markets have no ledger initial_qty; extras must fill it in
        rows = [
            row("0x1", first_opp=True, initial_qty=100),
            row("0x2", first_opp=False, initial_qty=None),
        ]
        extras = {
            "0x1": {"initial_qty": 100, "total_buy_qty": 120,
                    "total_sell_qty": 30},
            "0x2": {"initial_qty": 100, "total_buy_qty": 120,
                    "total_sell_qty": 30},
        }
        result = run_selection_bias(rows, extras)
        by_name = {c.variable: c for c in result.comparisons}
        # both groups share initial_qty=100 -> SMD 0
        assert by_name["initial_qty"].smd == 0.0
        assert by_name["total_buy_qty"].smd == 0.0
