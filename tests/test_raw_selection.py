from __future__ import annotations

import json
from pathlib import Path

from std0_quant.features.raw_selection import (
    closed_raw_index,
    files_for_sessions,
    files_for_time_window,
)


def _closed_raw(
    root: Path,
    name: str,
    *,
    session_id: str,
    first_ms: int,
    last_ms: int,
) -> Path:
    raw = root / name
    raw.write_text('{"x":1}\n', encoding="utf-8")

    sidecar = Path(str(raw) + ".meta.json")
    sidecar.write_text(
        json.dumps(
            {
                "file": str(raw),
                "source": "binance_btc",
                "opened_at_ms": first_ms,
                "closed_at_ms": last_ms + 1,
                "record_count": 1,
                "first_timestamp_ms": first_ms,
                "last_timestamp_ms": last_ms,
                "sha256": "deadbeef",
                "session_id": session_id,
                "recovered_after_unclean_exit": False,
                "parse_errors": 0,
                "integrity_status": "OK",
            }
        ),
        encoding="utf-8",
    )
    return raw


def test_closed_raw_index_ignores_active_file_without_sidecar(tmp_path):
    closed = _closed_raw(
        tmp_path,
        "closed.ndjson",
        session_id="btc-a",
        first_ms=1000,
        last_ms=2000,
    )
    active = tmp_path / "active.ndjson"
    active.write_text('{"x":2}\n', encoding="utf-8")

    index = closed_raw_index(tmp_path)

    assert [item.path for item in index] == [closed]
    assert active not in [item.path for item in index]


def test_time_window_predecessor_never_crosses_session(tmp_path):
    prior_other = _closed_raw(
        tmp_path,
        "other.ndjson",
        session_id="btc-other",
        first_ms=1000,
        last_ms=1999,
    )
    prior_same = _closed_raw(
        tmp_path,
        "same_prior.ndjson",
        session_id="btc-main",
        first_ms=2000,
        last_ms=2999,
    )
    current = _closed_raw(
        tmp_path,
        "same_current.ndjson",
        session_id="btc-main",
        first_ms=3000,
        last_ms=3999,
    )

    index = closed_raw_index(tmp_path)
    selected = files_for_time_window(
        index,
        3200,
        3500,
        session_id="btc-main",
        include_predecessor=True,
    )

    assert selected == [prior_same, current]
    assert prior_other not in selected


def test_same_session_rotated_files_are_selected(tmp_path):
    part1 = _closed_raw(
        tmp_path,
        "part1.ndjson",
        session_id="book-1",
        first_ms=1000,
        last_ms=1999,
    )
    part2 = _closed_raw(
        tmp_path,
        "part2.ndjson",
        session_id="book-1",
        first_ms=2000,
        last_ms=2999,
    )
    other = _closed_raw(
        tmp_path,
        "other.ndjson",
        session_id="book-2",
        first_ms=1000,
        last_ms=2999,
    )

    index = closed_raw_index(tmp_path)

    assert files_for_sessions(index, ["book-1"]) == [part1, part2]

    selected = files_for_time_window(
        index,
        1500,
        2500,
        session_id="book-1",
    )
    assert selected == [part1, part2]
    assert other not in selected
