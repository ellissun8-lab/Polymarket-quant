"""CF/RF gates for coverage evidence selection and rotation attribution."""
from __future__ import annotations

import hashlib
import importlib
import json

import pytest

from std0_quant.audit.coverage import SessionRecord, session_overlaps, session_time_ranges


START = 1_000_000
END = START + 300_000


def _coverage_evidence():
    return importlib.import_module("std0_quant.audit.coverage_evidence")


def _open_session() -> SessionRecord:
    return SessionRecord("btc-open", "btc_ticks", [
        {"event": "session_start", "timestamp_ms": START - 20_000},
        {"event": "connected", "timestamp_ms": START - 10_000},
        {"event": "file_open", "file": "active.ndjson", "timestamp_ms": START - 9_000},
    ])


def _closed_session() -> SessionRecord:
    return SessionRecord("btc-closed", "btc_ticks", [
        {"event": "connected", "timestamp_ms": START - 20_000},
        {"event": "disconnected", "timestamp_ms": START - 10_000},
        {"event": "session_end", "timestamp_ms": START - 9_000},
    ])


def _write_closed_raw(path) -> None:
    payload = b'{"exchange_timestamp_ms":1000000}\n'
    path.write_bytes(payload)
    meta = {
        "file": str(path), "sha256": hashlib.sha256(payload).hexdigest(),
        "integrity_status": "OK", "parse_errors": 0,
    }
    path.with_suffix(path.suffix + ".meta.json").write_text(
        json.dumps(meta), encoding="utf-8"
    )


def test_cf1_open_connection_overlaps_later_market():
    session = _open_session()
    assert session_time_ranges(session) == [(START - 10_000, None)]
    assert session_overlaps([session], START + 600_000, END + 600_000, "btc_ticks")


def test_cf2_closed_connection_does_not_overlap_later_market():
    session = _closed_session()
    assert session_time_ranges(session) == [(START - 20_000, START - 10_000)]
    assert not session_overlaps([session], START, END, "btc_ticks")


def test_cf3_active_raw_is_pending_not_zero(tmp_path):
    module = _coverage_evidence()
    active = tmp_path / "active.ndjson"
    active.write_text('{"exchange_timestamp_ms":1000000}\n', encoding="utf-8")
    result = module.classify_source_evidence([active])
    assert result["coverage_evidence_status"] == "PENDING_ACTIVE_SOURCE_FILE"
    assert result["coverage_pct"] is None
    assert result["source_files_pending"] == [str(active)]


def test_cf4_active_sensitivity_cannot_be_primary_eligible():
    module = _coverage_evidence()
    assert not module.formal_coverage_eligible({
        "coverage_evidence_status": "PENDING_ACTIVE_SOURCE_FILE",
        "provisional_coverage_pct": 1.0,
        "btc_coverage_pct": None,
        "book_coverage_pct": 1.0,
    })


def test_cf5_closed_sha_verified_file_can_finalize(tmp_path):
    module = _coverage_evidence()
    raw = tmp_path / "closed.ndjson"
    _write_closed_raw(raw)
    result = module.classify_source_evidence([raw])
    assert result["coverage_evidence_status"] == "FINAL_PASS_OR_FAIL"
    assert result["source_files_final"] == [str(raw)]
    assert result["source_sha256"][str(raw)]


def test_cf6_finalization_is_idempotent(tmp_path):
    module = _coverage_evidence()
    raw = tmp_path / "closed.ndjson"
    _write_closed_raw(raw)
    assert module.classify_source_evidence([raw]) == module.classify_source_evidence([raw])


def test_cf7_old_bad_artifact_remains_immutable(tmp_path):
    module = _coverage_evidence()
    old = tmp_path / "old.json"
    old.write_text('{"btc_coverage_pct":0}', encoding="utf-8")
    before = old.read_bytes()
    with pytest.raises(FileExistsError):
        module.write_immutable_evidence(old, {"btc_coverage_pct": 1.0})
    assert old.read_bytes() == before


def test_cf8_exact_99_percent_bucket_gate_passes():
    module = _coverage_evidence()
    assert module.coverage_bucket_gate(297, 300, 0.99)


def test_cf9_98_point_9_percent_bucket_gate_fails():
    module = _coverage_evidence()
    assert not module.coverage_bucket_gate(989, 1000, 0.99)


def test_cf10_no_candidate_is_source_not_captured():
    module = _coverage_evidence()
    result = module.classify_source_evidence([])
    assert result["coverage_evidence_status"] == "SOURCE_NOT_CAPTURED"
    assert result["coverage_pct"] is None


def test_cf11_finalized_orphan_file_does_not_cover_later_window(tmp_path):
    module = _coverage_evidence()
    raw = tmp_path / "orphan.ndjson"
    _write_closed_raw(raw)
    sidecar = raw.with_suffix(raw.suffix + ".meta.json")
    meta = json.loads(sidecar.read_text(encoding="utf-8"))
    meta.update({"first_timestamp_ms": START - 20_000,
                 "last_timestamp_ms": START - 10_000})
    sidecar.write_text(json.dumps(meta), encoding="utf-8")
    assert not module.source_file_overlaps_window(raw, START, END)


def _rotation_row(**updates):
    row = {
        "market_start_ms": START,
        "market_discovery_ms": START - 50_000,
        "subscription_ready_ms": START - 9_000,
        "book_first_valid_receive_ms": START - 8_000,
        "dual_token_valid": True,
        "rotation_gap_ms": 0,
        "mid_market_reconnects": 0,
    }
    row.update(updates)
    return row


def test_rf1_ready_before_start_is_not_rotation_failure():
    module = _coverage_evidence()
    assert module.rotation_failure_predicate(_rotation_row()) is None


def test_rf2_late_discovery_is_rotation_failure():
    module = _coverage_evidence()
    assert module.rotation_failure_predicate(
        _rotation_row(market_discovery_ms=START + 1)
    ) == "LATE_DISCOVERY"


def test_rf3_snapshot_only_after_start_is_rotation_failure():
    module = _coverage_evidence()
    assert module.rotation_failure_predicate(
        _rotation_row(book_first_valid_receive_ms=START + 1)
    ) == "SNAPSHOT_NOT_READY_AT_START"


def test_rf4_midmarket_reconnect_is_not_rotation_failure():
    module = _coverage_evidence()
    assert module.rotation_failure_predicate(
        _rotation_row(mid_market_reconnects=4)
    ) is None
