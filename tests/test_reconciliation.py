"""Spec Test H: reconciliation must hold exactly.

Scenario mirrors the spec example: 110 raw markets, 108 clean,
1 FIELD_INCOMPLETE + 1 SAME_SECOND_DIRECTION_AMBIGUITY.
"""

from __future__ import annotations

import pytest

from std0_quant.audit.reconciliation import (
    ReconciliationError,
    assert_reconciles,
    build_reconciliation,
    write_reconciliation_report,
)


def ledger_row(condition_id: str, *, clean: bool = True, reason: str | None = None,
               first_opp: bool = False, y30: int | None = None,
               eligible: bool | None = None, detail: str | None = None) -> dict:
    return {
        "condition_id": condition_id,
        "clean_flag": clean,
        "exclude_reason": reason,
        "exclude_detail": detail,
        "first_opp_start_ms": 1 if first_opp else None,
        "y30": y30,
        "y30_horizon_eligible": eligible,
    }


def make_fixture(n_clean: int = 108) -> tuple[set[str], set[str], list[dict]]:
    """n_clean clean markets + 1 FIELD_INCOMPLETE + 1 SAME_SECOND ambiguity."""
    clean_ids = sorted(f"0xmarket{i:03d}" for i in range(n_clean))
    raw_ids = set(clean_ids) | {"0xbadfields", "0xambiguous"}
    rows = [ledger_row(cid, clean=True) for cid in clean_ids]
    rows.append(ledger_row("0xbadfields", clean=False, reason="FIELD_INCOMPLETE"))
    rows.append(ledger_row("0xambiguous", clean=False,
                           reason="SAME_SECOND_DIRECTION_AMBIGUITY"))
    return raw_ids, set(raw_ids), rows


def test_reconciliation_totals_match_spec_example() -> None:
    raw_ids, buy_ids, rows = make_fixture()
    report = build_reconciliation(raw_ids, buy_ids, rows)
    assert report.raw_markets == 110
    assert report.clean_markets == 108
    assert report.excluded_markets == 2
    assert report.exclude_reason_counts == {
        "FIELD_INCOMPLETE": 1,
        "SAME_SECOND_DIRECTION_AMBIGUITY": 1,
    }
    assert report.problems == []
    assert_reconciles(report)  # must not raise


def test_missing_ledger_row_breaks_reconciliation() -> None:
    raw_ids, buy_ids, rows = make_fixture()
    rows = rows[:-1]  # drop one row -> a raw market has no ledger row
    report = build_reconciliation(raw_ids, buy_ids, rows)
    assert report.problems
    with pytest.raises(ReconciliationError):
        assert_reconciles(report)


def test_excluded_market_without_reason_breaks_reconciliation() -> None:
    raw_ids, buy_ids, rows = make_fixture()
    rows[0] = ledger_row(rows[0]["condition_id"], clean=False, reason=None)
    report = build_reconciliation(raw_ids, buy_ids, rows)
    assert any("no exclude_reason" in p for p in report.problems)
    with pytest.raises(ReconciliationError):
        assert_reconciles(report)


def test_other_without_detail_breaks_reconciliation() -> None:
    raw_ids, buy_ids, rows = make_fixture()
    rows[0] = ledger_row(rows[0]["condition_id"], clean=False, reason="OTHER")
    report = build_reconciliation(raw_ids, buy_ids, rows)
    assert any("OTHER exclusion missing detail" in p for p in report.problems)


def test_duplicate_rows_break_reconciliation() -> None:
    raw_ids, buy_ids, rows = make_fixture()
    rows.append(dict(rows[0]))  # duplicate row for an existing market
    report = build_reconciliation(raw_ids, buy_ids, rows)
    assert any("duplicate" in p for p in report.problems)


def test_extra_ledger_market_breaks_reconciliation() -> None:
    raw_ids, buy_ids, rows = make_fixture()
    rows.append(ledger_row("0xghost", clean=True))
    report = build_reconciliation(raw_ids, buy_ids, rows)
    assert any("no raw market" in p for p in report.problems)


def test_y30_counts_partition() -> None:
    raw_ids, buy_ids, rows = make_fixture(n_clean=5)
    # rows[0..4] clean: make 2 positives, 1 eligible negative, 1 censored,
    # 1 no-first-opposite (y30 None)
    rows[0] = ledger_row(rows[0]["condition_id"], first_opp=True, y30=1, eligible=True)
    rows[1] = ledger_row(rows[1]["condition_id"], first_opp=True, y30=1, eligible=False)
    rows[2] = ledger_row(rows[2]["condition_id"], first_opp=True, y30=0, eligible=True)
    rows[3] = ledger_row(rows[3]["condition_id"], first_opp=True, y30=0, eligible=False)
    rows[4] = ledger_row(rows[4]["condition_id"], first_opp=False, y30=None)
    report = build_reconciliation(raw_ids, buy_ids, rows)
    assert report.y30_positives == 2
    assert report.y30_negatives == 1
    assert report.y30_censored == 1
    assert report.problems == []


def test_report_files_written(tmp_path) -> None:
    raw_ids, buy_ids, rows = make_fixture()
    report = build_reconciliation(raw_ids, buy_ids, rows)
    md_path = write_reconciliation_report(report, tmp_path, "20260823T000000Z")
    json_path = tmp_path / "reconciliation_20260823T000000Z.json"
    assert json_path.is_file()
    assert md_path.is_file()
    text = md_path.read_text(encoding="utf-8")
    assert "raw markets: 110" in text
    assert "SAME_SECOND_DIRECTION_AMBIGUITY: 1" in text
    assert "clean markets: 108" in text
