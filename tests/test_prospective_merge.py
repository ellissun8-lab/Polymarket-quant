from __future__ import annotations

from pathlib import Path

import pyarrow.parquet as pq
import pytest

from std0_quant.audit.prospective import create_baseline_snapshot
from std0_quant.events.prospective_merge import (
    atomic_write_parquet,
    merge_prospective_rows,
)


def row(cid: str, *, y30: int = 1, clean: bool = True) -> dict:
    return {
        "condition_id": cid,
        "clean_flag": clean,
        "exclude_reason": None,
        "exclude_detail": None,
        "y30": y30,
        "y30_horizon_eligible": True,
        "episode_rule_version": "v1_3sec",
        "slug": f"btc-updown-5m-{cid}",
    }


def snapshot(tmp_path: Path, rows: list[dict]) -> dict:
    return create_baseline_snapshot(rows, tmp_path / "baseline.json")


def test_additive_merge_preserves_historical_baseline(tmp_path):
    historical = [row("h1"), row("h2", y30=0)]
    baseline = snapshot(tmp_path, historical)
    incoming = [row("p1")]

    merged, report = merge_prospective_rows(
        historical,
        incoming,
        baseline,
    )

    assert {r["condition_id"] for r in merged} == {"h1", "h2", "p1"}
    assert report["status"] == "PASS"
    assert report["added_rows"] == 1
    assert report["idempotent_rows"] == 0
    assert report["historical_baseline_invariance"]["status"] == "PASS"


def test_rejects_any_incoming_historical_id_even_if_identical(tmp_path):
    historical = [row("h1")]
    baseline = snapshot(tmp_path, historical)

    with pytest.raises(ValueError, match="overlaps frozen historical"):
        merge_prospective_rows(
            historical,
            [row("h1")],
            baseline,
        )


def test_existing_prospective_row_can_be_replayed_identically(tmp_path):
    historical = [row("h1")]
    published = row("p1")
    existing = historical + [published]
    baseline = snapshot(tmp_path, historical)

    merged, report = merge_prospective_rows(
        existing,
        [dict(published)],
        baseline,
    )

    assert len(merged) == 2
    assert report["added_rows"] == 0
    assert report["idempotent_rows"] == 1
    assert report["idempotent_condition_ids"] == ["p1"]


def test_rejects_mutation_of_already_published_prospective_row(tmp_path):
    historical = [row("h1")]
    existing = historical + [row("p1", y30=1)]
    baseline = snapshot(tmp_path, historical)

    with pytest.raises(ValueError, match="mutation/conflict"):
        merge_prospective_rows(
            existing,
            [row("p1", y30=0)],
            baseline,
        )


def test_rejects_existing_ledger_that_already_changed_history(tmp_path):
    frozen = [row("h1", y30=1)]
    baseline = snapshot(tmp_path, frozen)
    corrupted_existing = [row("h1", y30=0)]

    with pytest.raises(ValueError, match="already violates historical baseline"):
        merge_prospective_rows(
            corrupted_existing,
            [row("p1")],
            baseline,
        )


def test_atomic_write_parquet_replaces_target_and_leaves_no_temp(tmp_path):
    target = tmp_path / "event_ledger.parquet"

    atomic_write_parquet([row("h1")], target)
    first = pq.read_table(target).to_pylist()
    assert [r["condition_id"] for r in first] == ["h1"]

    atomic_write_parquet([row("h1"), row("p1")], target)
    second = pq.read_table(target).to_pylist()
    assert [r["condition_id"] for r in second] == ["h1", "p1"]

    leftovers = list(tmp_path.glob(f".{target.name}.*.tmp"))
    assert leftovers == []
