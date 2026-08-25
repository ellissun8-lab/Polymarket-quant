"""Storage layer for std0-quant.

Design rules enforced here:

* Raw data is **append-only** NDJSON. Nothing in this module can overwrite,
  truncate, or rewrite an existing raw file; the only write operation is
  ``append`` (file opened in ``"ab"`` mode, flushed and fsynced).
* Sync/dedupe state lives in a small SQLite database (WAL mode) under
  ``data/state/``. Derived data is always rebuildable from raw.
* Normalized/derived outputs are Parquet files written from plain dicts.
* Every raw record is stored inside an envelope that preserves the original
  payload byte-for-byte (``record``) plus provenance metadata (``source``,
  ``fetched_at_ms``, ``sync_run_id``).
"""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
import os
import sqlite3
import threading
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from std0_quant.timeutil import utc_now_ms

logger = logging.getLogger(__name__)

RUN_ID_UNIQUENESS_FIX_VERSION = "run_id_uniqueness_fix_v1"
_run_id_counter = itertools.count()
_run_id_lock = threading.Lock()


def canonical_json(record: dict[str, Any]) -> str:
    """Deterministic JSON serialization (sorted keys, no whitespace)."""
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def new_run_id(prefix: str) -> str:
    """Return a readable run id unique within a process, even in one millisecond."""
    with _run_id_lock:
        sequence = next(_run_id_counter)
    return f"{prefix}-{utc_now_ms()}-{os.getpid()}-{sequence:016x}"


class AppendOnlyNDJSON:
    """Append-only NDJSON file with flush+fsync durability.

    The file is opened in append-binary mode only. There is deliberately no
    API to modify or remove lines: raw history is immutable.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "ab")

    def append(self, record: dict[str, Any]) -> None:
        self._fh.write((canonical_json(record) + "\n").encode("utf-8"))

    def append_many(self, records: Iterable[dict[str, Any]]) -> int:
        count = 0
        for record in records:
            self.append(record)
            count += 1
        self.flush()
        return count

    def flush(self) -> None:
        self._fh.flush()
        os.fsync(self._fh.fileno())

    def close(self) -> None:
        self.flush()
        self._fh.close()

    def __enter__(self) -> AppendOnlyNDJSON:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def read_ndjson(path: Path | str) -> Iterator[dict[str, Any]]:
    """Stream records from an NDJSON file. Raises on malformed lines."""
    with open(Path(path), "r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"malformed NDJSON at {path}:{line_no}: {exc}") from exc


class SqliteState:
    """Small SQLite-backed state store for sync cursors and dedupe keys."""

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS kv (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at_ms INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS dedupe_keys (
        namespace TEXT NOT NULL,
        key TEXT NOT NULL,
        first_seen_run_id TEXT NOT NULL,
        first_seen_at_ms INTEGER NOT NULL,
        PRIMARY KEY (namespace, key)
    );
    CREATE TABLE IF NOT EXISTS sync_runs (
        run_id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        started_at_ms INTEGER NOT NULL,
        finished_at_ms INTEGER,
        status TEXT NOT NULL,
        params_json TEXT
    );
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(self._SCHEMA)
        self._conn.commit()

    # -- kv cursors ---------------------------------------------------------

    def get_kv(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_kv(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO kv (key, value, updated_at_ms) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at_ms = excluded.updated_at_ms",
            (key, value, utc_now_ms()),
        )
        self._conn.commit()

    # -- dedupe --------------------------------------------------------------

    def filter_new_keys(self, namespace: str, keys: Iterable[str]) -> list[str]:
        """Return the subset of *keys* not yet registered under *namespace*."""
        keys = list(keys)
        if not keys:
            return []
        new: list[str] = []
        for start in range(0, len(keys), 500):
            chunk = keys[start : start + 500]
            placeholders = ",".join("?" for _ in chunk)
            rows = self._conn.execute(
                f"SELECT key FROM dedupe_keys WHERE namespace = ? AND key IN ({placeholders})",
                (namespace, *chunk),
            ).fetchall()
            existing = {row[0] for row in rows}
            new.extend(k for k in chunk if k not in existing)
        return new

    def register_keys(self, namespace: str, keys: Iterable[str], run_id: str) -> int:
        """Register keys (idempotent). Returns how many were newly inserted."""
        now = utc_now_ms()
        inserted = 0
        for key in keys:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO dedupe_keys (namespace, key, first_seen_run_id, first_seen_at_ms) "
                "VALUES (?, ?, ?, ?)",
                (namespace, key, run_id, now),
            )
            inserted += cur.rowcount
        self._conn.commit()
        return inserted

    def known_keys(self, namespace: str) -> set[str]:
        rows = self._conn.execute(
            "SELECT key FROM dedupe_keys WHERE namespace = ?", (namespace,)
        ).fetchall()
        return {row[0] for row in rows}

    # -- sync runs -----------------------------------------------------------

    def start_run(self, run_id: str, kind: str, params: dict[str, Any] | None = None) -> None:
        self._conn.execute(
            "INSERT INTO sync_runs (run_id, kind, started_at_ms, status, params_json) "
            "VALUES (?, ?, ?, 'running', ?)",
            (run_id, kind, utc_now_ms(), json.dumps(params or {}, sort_keys=True)),
        )
        self._conn.commit()

    def finish_run(self, run_id: str, status: str, params: dict[str, Any] | None = None) -> None:
        self._conn.execute(
            "UPDATE sync_runs SET finished_at_ms = ?, status = ?, params_json = COALESCE(?, params_json) "
            "WHERE run_id = ?",
            (utc_now_ms(), status, json.dumps(params, sort_keys=True) if params else None, run_id),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> SqliteState:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class RawPageStore:
    """Persists full raw API responses (audit trail for every page fetched)."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save_page(self, run_id: str, page_index: int, url: str, params: dict[str, Any],
                  status_code: int, body: str) -> Path:
        run_dir = self.root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / f"page_{page_index:05d}.json"
        if path.exists():
            # Pages are content-addressed per run; a re-run uses a new run_id.
            raise FileExistsError(f"refusing to overwrite raw page {path}")
        payload = {
            "url": url,
            "params": params,
            "status_code": status_code,
            "fetched_at_ms": utc_now_ms(),
            "body": body,
        }
        path.write_text(canonical_json(payload), encoding="utf-8")
        return path


def write_parquet(rows: list[dict[str, Any]], path: Path | str) -> Path:
    """Write a list of dicts to Parquet (normalized/derived outputs only).

    Nested lists (e.g. ``constituent_fill_ids``) are preserved as list columns.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise ValueError(f"refusing to write empty parquet to {path}")
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, path, compression="zstd")
    return path


def read_parquet(path: Path | str) -> list[dict[str, Any]]:
    import pyarrow.parquet as pq

    table = pq.read_table(Path(path))
    return table.to_pylist()


def envelope(source: str, record: dict[str, Any], sync_run_id: str,
             fetched_at_ms: int | None = None) -> dict[str, Any]:
    """Build the raw-store envelope around an untouched API record."""
    return {
        "source": source,
        "sync_run_id": sync_run_id,
        "fetched_at_ms": fetched_at_ms if fetched_at_ms is not None else utc_now_ms(),
        "record": record,
    }
