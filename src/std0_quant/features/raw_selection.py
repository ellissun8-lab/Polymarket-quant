"""Low-memory selection of closed raw files from immutable sidecars.

This module selects candidate raw files only. It does not change feature
semantics and it never stitches data across recorder sessions.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class ClosedRawFile:
    path: Path
    source: str | None
    session_id: str
    first_timestamp_ms: int
    last_timestamp_ms: int
    record_count: int
    sha256: str


def closed_raw_index(root: Path | str) -> list[ClosedRawFile]:
    """Index finalized, sidecar-backed raw files without reading raw NDJSON."""
    root = Path(root)
    rows: list[ClosedRawFile] = []

    if not root.is_dir():
        return rows

    for sidecar in sorted(root.rglob("*.ndjson.meta.json")):
        try:
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue

        if meta.get("integrity_status") not in (None, "OK"):
            continue
        if int(meta.get("parse_errors") or 0) != 0:
            continue

        first = meta.get("first_timestamp_ms")
        last = meta.get("last_timestamp_ms")
        session_id = meta.get("session_id")
        digest = meta.get("sha256")

        if first is None or last is None or not session_id or not digest:
            continue

        raw = Path(str(sidecar)[: -len(".meta.json")])
        if not raw.is_file():
            continue

        rows.append(
            ClosedRawFile(
                path=raw,
                source=str(meta.get("source")) if meta.get("source") is not None else None,
                session_id=str(session_id),
                first_timestamp_ms=int(first),
                last_timestamp_ms=int(last),
                record_count=int(meta.get("record_count") or 0),
                sha256=str(digest),
            )
        )

    return sorted(
        rows,
        key=lambda x: (
            x.session_id,
            x.first_timestamp_ms,
            x.last_timestamp_ms,
            str(x.path),
        ),
    )


def files_for_sessions(
    index: Iterable[ClosedRawFile],
    session_ids: Iterable[str],
) -> list[Path]:
    """Return finalized rotated files belonging to explicitly allowed sessions."""
    allowed = {str(x) for x in session_ids}
    return [
        item.path
        for item in index
        if item.session_id in allowed
    ]


def files_for_time_window(
    index: Iterable[ClosedRawFile],
    start_ms: int,
    end_ms: int,
    *,
    session_id: str,
    include_predecessor: bool = False,
) -> list[Path]:
    """Select files overlapping [start_ms, end_ms] within one session only.

    ``include_predecessor`` adds at most one earlier file from the SAME
    session. This supports last-observation-before-target lookups without
    violating the no-session-stitching rule.
    """
    if end_ms < start_ms:
        raise ValueError("end_ms must be >= start_ms")

    same_session = sorted(
        (item for item in index if item.session_id == session_id),
        key=lambda x: (
            x.first_timestamp_ms,
            x.last_timestamp_ms,
            str(x.path),
        ),
    )

    selected = [
        item
        for item in same_session
        if item.first_timestamp_ms <= end_ms
        and item.last_timestamp_ms >= start_ms
    ]

    if include_predecessor:
        previous = [
            item for item in same_session
            if item.last_timestamp_ms < start_ms
        ]
        if previous:
            predecessor = max(
                previous,
                key=lambda x: (
                    x.last_timestamp_ms,
                    x.first_timestamp_ms,
                    str(x.path),
                ),
            )
            selected.append(predecessor)

    unique = {
        str(item.path): item.path
        for item in selected
    }
    return [
        unique[name]
        for name in sorted(
            unique,
            key=lambda name: (
                next(x.first_timestamp_ms for x in same_session
                     if str(x.path) == name),
                name,
            ),
        )
    ]
