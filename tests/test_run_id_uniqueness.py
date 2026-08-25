"""Deterministic regression coverage for run-id collision resistance."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

import std0_quant.storage as storage


def _freeze_identity_inputs(monkeypatch) -> None:
    monkeypatch.setattr(storage, "utc_now_ms", lambda: 1_787_676_877_131)
    monkeypatch.setattr(storage.os, "getpid", lambda: 2_626)


def test_same_millisecond_same_pid_creates_two_sync_runs(monkeypatch, tmp_path) -> None:
    _freeze_identity_inputs(monkeypatch)

    first = storage.new_run_id("sync-trades")
    second = storage.new_run_id("sync-trades")

    assert first != second
    pattern = re.compile(r"^sync-trades-1787676877131-2626-[0-9a-f]{16}$")
    assert pattern.fullmatch(first)
    assert pattern.fullmatch(second)

    with storage.SqliteState(tmp_path / "state.db") as state:
        state.start_run(first, "std0_trades_sync")
        state.start_run(second, "std0_trades_sync")
        rows = state._conn.execute(
            "SELECT run_id FROM sync_runs ORDER BY run_id"
        ).fetchall()

    assert {row[0] for row in rows} == {first, second}


def test_fixed_timestamp_pid_generates_10000_unique_ids_thread_safely(
    monkeypatch,
) -> None:
    _freeze_identity_inputs(monkeypatch)

    with ThreadPoolExecutor(max_workers=16) as pool:
        run_ids = list(pool.map(lambda _: storage.new_run_id("sync-trades"), range(10_000)))

    assert len(run_ids) == 10_000
    assert len(set(run_ids)) == 10_000
    assert storage.RUN_ID_UNIQUENESS_FIX_VERSION == "run_id_uniqueness_fix_v1"
