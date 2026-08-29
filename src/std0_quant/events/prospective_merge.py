"""Protected additive publication of prospective event-ledger rows.

Historical baseline rows are immutable.  Prospective rows may only be added.
A previously published prospective row may be seen again only when it is
exactly identical, making reruns idempotent rather than mutating history.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable

from std0_quant.audit.prospective import verify_baseline_snapshot


def _by_condition_id(
    rows: Iterable[dict[str, Any]],
    *,
    label: str,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}

    for row in rows:
        condition_id = row.get("condition_id")
        if condition_id is None:
            raise ValueError(f"{label} row missing condition_id")

        cid = str(condition_id)

        if cid in result:
            if result[cid] != row:
                raise ValueError(
                    f"{label} contains conflicting duplicate condition_id {cid}"
                )
            continue

        result[cid] = row

    return result


def merge_prospective_rows(
    existing_rows: list[dict[str, Any]],
    prospective_rows: list[dict[str, Any]],
    baseline_snapshot: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Add finalized prospective rows without permitting historical mutation."""

    before = verify_baseline_snapshot(
        baseline_snapshot,
        existing_rows,
    )
    if before["status"] != "PASS":
        raise ValueError(
            "existing ledger already violates historical baseline invariance"
        )

    existing = _by_condition_id(existing_rows, label="existing ledger")
    incoming = _by_condition_id(prospective_rows, label="prospective input")

    baseline_ids = {
        str(row.get("condition_id"))
        for row in baseline_snapshot.get("rows", [])
    }

    historical_overlap = sorted(set(incoming) & baseline_ids)
    if historical_overlap:
        raise ValueError(
            "prospective input overlaps frozen historical condition_id(s): "
            + ", ".join(historical_overlap[:10])
        )

    added: list[str] = []
    idempotent: list[str] = []
    conflicts: list[str] = []

    merged = dict(existing)

    for cid, row in incoming.items():
        prior = existing.get(cid)

        if prior is None:
            merged[cid] = row
            added.append(cid)
            continue

        if prior == row:
            idempotent.append(cid)
            continue

        conflicts.append(cid)

    if conflicts:
        raise ValueError(
            "prospective row mutation/conflict for already-published "
            "condition_id(s): "
            + ", ".join(sorted(conflicts)[:10])
        )

    merged_rows = [
        merged[cid]
        for cid in sorted(merged)
    ]

    after = verify_baseline_snapshot(
        baseline_snapshot,
        merged_rows,
    )
    if after["status"] != "PASS":
        raise AssertionError(
            "merged ledger failed historical baseline invariance"
        )

    report = {
        "status": "PASS",
        "existing_rows": len(existing),
        "incoming_unique_rows": len(incoming),
        "added_rows": len(added),
        "idempotent_rows": len(idempotent),
        "final_rows": len(merged_rows),
        "added_condition_ids": sorted(added),
        "idempotent_condition_ids": sorted(idempotent),
        "historical_baseline_invariance": after,
    }

    return merged_rows, report


def atomic_write_parquet(
    rows: list[dict[str, Any]],
    path: Path | str,
) -> Path:
    """Publish Parquet atomically via same-directory temp + fsync + replace."""

    if not rows:
        raise ValueError("refusing to publish empty ledger")

    import pyarrow as pa
    import pyarrow.parquet as pq

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    temp = target.with_name(
        f".{target.name}.{os.getpid()}.tmp"
    )

    try:
        table = pa.Table.from_pylist(rows)
        pq.write_table(table, temp, compression="zstd")

        with temp.open("rb") as fh:
            os.fsync(fh.fileno())

        os.replace(temp, target)

        # Persist the directory entry where the platform supports it.
        flags = getattr(os, "O_DIRECTORY", 0)
        try:
            fd = os.open(str(target.parent), os.O_RDONLY | flags)
        except OSError:
            fd = None

        if fd is not None:
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    finally:
        if temp.exists():
            temp.unlink()

    return target
