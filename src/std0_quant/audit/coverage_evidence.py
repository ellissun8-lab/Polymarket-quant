"""Versioned, immutable formal coverage-evidence selection.

Formal evidence is restricted to closed, sidecar-backed, SHA-verified raw
files.  Active files may be scanned provisionally for operations, but pending
evidence is never encoded as zero coverage and can never authorize primary
eligibility.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from std0_quant.collectors.live_storage import streaming_sha256

COVERAGE_EVIDENCE_VERSION = "coverage_evidence_v2"
COVERAGE_SELECTION_FIX_VERSION = "coverage_selection_fix_v1"

FINAL_PASS_OR_FAIL = "FINAL_PASS_OR_FAIL"
PENDING_ACTIVE_SOURCE_FILE = "PENDING_ACTIVE_SOURCE_FILE"
SOURCE_NOT_CAPTURED = "SOURCE_NOT_CAPTURED"
PROVISIONAL_ACTIVE_FILE = "PROVISIONAL_ACTIVE_FILE"


def coverage_bucket_gate(filled_buckets: int, total_buckets: int,
                         threshold: float | str = 0.99) -> bool:
    """Compare integer bucket counts without a floating-point boundary leak."""
    if total_buckets <= 0 or filled_buckets < 0:
        return False
    return (Decimal(filled_buckets) >=
            Decimal(str(threshold)) * Decimal(total_buckets))


def source_file_overlaps_window(path: Path | str, start_ms: int, end_ms: int,
                                active_file_open_ms: int | None = None) -> bool:
    """Use finalized file bounds, or an explicit open time for an active file."""
    raw = Path(path)
    sidecar = raw.with_suffix(raw.suffix + ".meta.json")
    if sidecar.exists():
        try:
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
            first = meta.get("first_timestamp_ms")
            last = meta.get("last_timestamp_ms")
            return (first is not None and last is not None and
                    int(first) <= end_ms and int(last) >= start_ms)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return False
    return (raw.exists() and active_file_open_ms is not None and
            int(active_file_open_ms) <= end_ms)


def _verified_sidecar(
    path: Path,
) -> tuple[bool, str | None, int | None, str | None]:
    sidecar = path.with_suffix(path.suffix + ".meta.json")
    if not sidecar.exists():
        return False, None, None, None
    try:
        meta = json.loads(sidecar.read_text(encoding="utf-8"))
        expected = str(meta.get("sha256") or "")
        if (meta.get("integrity_status") not in (None, "OK") or
                int(meta.get("parse_errors") or 0) != 0 or not expected):
            return False, expected or None, None, "SIDECAR_INTEGRITY_FAILURE"
        actual = streaming_sha256(path)
        if actual != expected:
            return False, expected, None, "SHA256_MISMATCH"
        closed_at = meta.get("closed_at_ms")
        return True, actual, int(closed_at) if closed_at is not None else None, None
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return False, None, None, f"SIDECAR_READ_FAILURE:{type(exc).__name__}"


def classify_source_evidence(candidate_files: Iterable[Path | str]) -> dict[str, Any]:
    """Classify source files without reading active data as formal evidence."""
    candidates = sorted({str(Path(item)) for item in candidate_files})
    final: list[str] = []
    pending: list[str] = []
    missing: list[str] = []
    failures: dict[str, str] = {}
    sha: dict[str, str] = {}
    closed_at_ms: dict[str, int] = {}
    for name in candidates:
        path = Path(name)
        if not path.exists():
            missing.append(name)
            continue
        sidecar = path.with_suffix(path.suffix + ".meta.json")
        if not sidecar.exists():
            pending.append(name)
            continue
        verified, digest, closed_at, error = _verified_sidecar(path)
        if verified:
            final.append(name)
            sha[name] = str(digest)
            if closed_at is not None:
                closed_at_ms[name] = closed_at
        else:
            failures[name] = str(error or "INTEGRITY_FAILURE")
    if pending:
        status = PENDING_ACTIVE_SOURCE_FILE
    elif final and not missing and not failures:
        status = FINAL_PASS_OR_FAIL
    else:
        status = SOURCE_NOT_CAPTURED
    return {
        "coverage_evidence_status": status,
        "coverage_evidence_version": COVERAGE_EVIDENCE_VERSION,
        "coverage_selection_fix_version": COVERAGE_SELECTION_FIX_VERSION,
        "coverage_pct": None,
        "source_files_candidate": candidates,
        "source_files_final": final,
        "source_files_pending": pending,
        "source_files_missing": missing,
        "source_integrity_failures": failures,
        "source_sha256": sha,
        "source_closed_at_ms": closed_at_ms,
    }


def formal_coverage_eligible(report: dict[str, Any], threshold: float = 0.99) -> bool:
    """Provisional active-file coverage is deliberately ignored."""
    if report.get("coverage_evidence_status") != FINAL_PASS_OR_FAIL:
        return False
    try:
        btc = Decimal(str(report["btc_coverage_pct"]))
        book = Decimal(str(report["book_coverage_pct"]))
    except (KeyError, TypeError, ValueError):
        return False
    gate = Decimal(str(threshold))
    return btc >= gate and book >= gate


def rotation_failure_predicate(row: dict[str, Any]) -> str | None:
    """Endpoint-only rotation predicate; mid-market gaps belong to coverage."""
    start = int(row["market_start_ms"])
    discovered = row.get("market_discovery_ms")
    if discovered is None or int(discovered) > start:
        return "LATE_DISCOVERY"
    subscribed = row.get("subscription_ready_ms")
    if subscribed is None or int(subscribed) > start:
        return "LATE_SUBSCRIPTION"
    if row.get("dual_token_valid") is False:
        return "DUAL_TOKEN_NOT_READY"
    first_valid = row.get("book_first_valid_receive_ms")
    if first_valid is None or int(first_valid) > start:
        return "SNAPSHOT_NOT_READY_AT_START"
    if int(row.get("rotation_gap_ms") or 0) > 0:
        return "ROTATION_GAP"
    return None


def write_immutable_evidence(path: Path | str, payload: dict[str, Any]) -> Path:
    """Write once; an identical rerun is idempotent, a mutation is rejected."""
    target = Path(path)
    encoded = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
    if target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise FileExistsError(f"immutable artifact already exists: {target}") from exc
        if existing == payload:
            return target
        raise FileExistsError(f"immutable artifact already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + f".{os.getpid()}.tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)
    return target
