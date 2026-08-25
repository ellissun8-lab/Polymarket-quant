"""Tests K - Audit 3: within-page collision sensitivity."""

from __future__ import annotations

import pytest

from std0_quant.audit.collision_sensitivity import (
    CollisionConcentration,
    compare_datasets,
    collision_concentration,
    scan_page_for_collisions,
    scan_pages,
)


def rec(cid="0x1", side="BUY", outcome="Up", ts=1_787_500_000,
        size=10.0, price=0.5, tx="0xtx", asset="0xasset", idx=0):
    return {
        "conditionId": cid, "side": side, "outcome": outcome,
        "timestamp": ts, "size": size, "price": price,
        "transactionHash": tx, "asset": asset, "outcomeIndex": idx,
    }


def row(cid, first_opp=True, y30=1, eligible=True, initial_qty=100.0):
    return {
        "condition_id": cid,
        "first_opp_end_ms": 1_787_500_000_000 if first_opp else None,
        "y30": y30 if first_opp else None,
        "y30_horizon_eligible": eligible if first_opp else False,
        "initial_qty": initial_qty if first_opp else None,
    }


class TestScanPage:
    def test_no_collisions(self) -> None:
        records = [rec(tx=f"0x{i}") for i in range(5)]
        assert scan_page_for_collisions(records) == []

    def test_identical_records_collapse(self) -> None:
        records = [rec(), rec(), rec(tx="0xother")]
        collisions = scan_page_for_collisions(records, page_label="p0")
        assert len(collisions) == 1
        c = collisions[0]
        assert c.occurrences == 2
        assert c.excess_records == 1
        assert c.condition_id == "0x1"
        assert c.side == "BUY"
        assert c.outcome == "Up"
        assert c.timestamp_ms == 1_787_500_000_000

    def test_distinct_size_not_collision(self) -> None:
        # same tx/asset but different size -> different identity
        records = [rec(size=10.0), rec(size=11.0)]
        assert scan_page_for_collisions(records) == []

    def test_triple_occurrence(self) -> None:
        records = [rec(), rec(), rec()]
        c = scan_page_for_collisions(records)[0]
        assert c.occurrences == 3
        assert c.excess_records == 2

    def test_missing_fields_tolerated(self) -> None:
        records = [{"conditionId": "0x1"}, {"conditionId": "0x1"}]
        c = scan_page_for_collisions(records)[0]
        assert c.timestamp_ms is None
        assert c.size is None

    def test_bad_size_tolerated(self) -> None:
        records = [rec(size="not-a-number"), rec(size="not-a-number")]
        c = scan_page_for_collisions(records)[0]
        assert c.size is None


class TestScanPages:
    def test_aggregation(self) -> None:
        pages = [
            ("run1/page_00000", [rec(), rec(), rec(tx="0xa")]),
            ("run1/page_00001", [rec(tx="0xb")]),
            ("run2/page_00000", [rec(cid="0x2"), rec(cid="0x2"),
                                  rec(cid="0x2")]),
        ]
        result = scan_pages(pages)
        assert result.n_pages == 3
        assert result.n_records == 7
        assert result.n_pages_with_collisions == 2
        assert result.n_collisions == 2
        assert result.excess_records == 3
        assert result.affected_condition_ids == {"0x1", "0x2"}
        by_cond = result.collision_fill_count_by_condition()
        assert by_cond["0x1"] == 2
        assert by_cond["0x2"] == 3

    def test_empty(self) -> None:
        result = scan_pages([])
        assert result.n_pages == 0
        assert result.excess_records == 0
        assert result.affected_condition_ids == set()


class TestCompareDatasets:
    def _rows(self):
        # 9 base markets + 1 collision-affected market (0xc, y30=1) = 10
        rows = []
        for i in range(9):
            rows.append(row(f"0x{i}", first_opp=True, y30=1 if i < 4 else 0,
                            initial_qty=float(100 + i)))
        rows.append(row("0xc", first_opp=True, y30=1, initial_qty=50.0))
        return rows

    def test_low_sensitivity(self) -> None:
        rows = self._rows()
        result = compare_datasets(rows, [], affected_condition_ids={"0xc"})
        a, b = result.stats_a, result.stats_b
        assert a.n_clean == 10
        assert b.n_clean == 9
        assert result.n_clean_affected == 1
        assert result.share_clean_affected == pytest.approx(0.1)
        # A: 5 positive / 10 eligible; B: 4 / 9 -> delta ~5.6pp
        assert a.y30_positive == 5 and b.y30_positive == 4
        assert result.delta_y30_pp == pytest.approx(
            abs(0.5 - 4 / 9) * 100, abs=1e-6
        )
        assert result.status == "HIGH_SENSITIVITY"

    def test_low_sensitivity_when_delta_small(self) -> None:
        # 1 affected market out of 1000, y30=1, base rate 50%
        rows = [row(f"0x{i}", y30=1 if i % 2 else 0) for i in range(1000)]
        rows.append(row("0xhit", y30=1))
        result = compare_datasets(rows, [], {"0xhit"})
        assert result.delta_y30_pp < 1.0
        assert result.delta_first_opp_pp == 0.0
        assert result.status == "LOW_SENSITIVITY"

    def test_median_initial_qty(self) -> None:
        rows = [row("0x1", initial_qty=10.0), row("0x2", initial_qty=20.0),
                row("0x3", initial_qty=30.0)]
        result = compare_datasets(rows, [], set())
        assert result.stats_a.median_initial_qty == 20.0
        assert result.stats_b.median_initial_qty == 20.0

    def test_no_first_opp_markets_counted(self) -> None:
        rows = [row("0x1", first_opp=False), row("0x2", first_opp=False)]
        result = compare_datasets(rows, [], set())
        assert result.stats_a.n_clean == 2
        assert result.stats_a.n_first_opp == 0
        assert result.stats_a.first_opp_rate == 0.0

    def test_empty_clean_set(self) -> None:
        result = compare_datasets([], [], {"0x1"})
        assert result.status == "NOT_COMPUTABLE"

    def test_excluded_affected_markets_tracked(self) -> None:
        excluded = [row("0x9"), row("0x8")]
        result = compare_datasets([], excluded, {"0x9"})
        assert result.n_excluded_affected == 1
        assert result.n_clean_affected == 0

    def test_collision_fill_count_only_for_clean(self) -> None:
        rows = [row("0x1")]
        excluded = [row("0x9")]
        result = compare_datasets(
            rows, excluded, {"0x1", "0x9"},
            collision_fill_count_by_condition={"0x1": 3, "0x9": 7},
        )
        assert result.collision_fill_count == 3

    def test_censored_markets_not_in_y30_rate(self) -> None:
        rows = [row("0x1", y30=1), row("0x2", y30=1, eligible=False)]
        result = compare_datasets(rows, [], set())
        assert result.stats_a.n_eligible == 1
        assert result.stats_a.y30_positive_rate_observable == 1.0


class TestConcentration:
    def test_buckets(self) -> None:
        from std0_quant.audit.collision_sensitivity import PageCollision
        collisions = [
            PageCollision("p0", "id1", 2, "0x1", "BUY", "Up",
                          1_787_500_000_000, 10.0),
            PageCollision("p1", "id2", 2, "0x1", "SELL", "Down",
                          1_787_500_000_000 + 3_600_000, 5.0),
            PageCollision("p2", "id3", 3, "0x2", "BUY", "Down",
                          1_787_500_000_000, 7.0),
        ]
        conc = collision_concentration(collisions)
        assert sum(conc.by_utc_date.values()) == 3
        assert conc.by_side == {"BUY": 2, "SELL": 1}
        assert conc.by_outcome == {"Up": 1, "Down": 2}
        assert conc.by_market_top[0] == ("0x1", 2)

    def test_labels_of_affected_clean(self) -> None:
        from std0_quant.audit.collision_sensitivity import PageCollision
        collisions = [PageCollision("p", "id", 2, "0x1", "BUY", "Up",
                                    None, None),
                      PageCollision("p", "id2", 2, "0x2", "BUY", "Up",
                                    None, None)]
        rows = [row("0x1", y30=1), row("0x2", first_opp=False),
                row("0x3", y30=0, eligible=False)]
        conc = collision_concentration(collisions, clean_rows=rows)
        assert conc.affected_clean_labels == {"y30=1": 1, "no_first_opp": 1}
