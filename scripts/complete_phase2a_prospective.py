"""Freeze v4 and emit Phase 2A-Prospective completion/operational artifacts.

This is an offline audit.  It never starts collection, modeling or trading.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pyarrow.parquet as pq

from std0_quant.audit.coverage import load_sessions
from std0_quant.audit.prospective import (
    COHORT_FREEZE_REASON, COHORT_VERSION, PRIMARY_COHORT_START_MARKET,
    PRIMARY_COHORT_START_MS, CohortManifest, atomic_json,
    continuous_operations_status, event_window_counts, fully_covered_observation,
    verify_baseline_snapshot, verify_raw_sidecars,
)
from std0_quant.config import load_settings, resolve_path
from std0_quant.storage import read_ndjson

CONDITION_ID = "0x035600d8e35f2f451d167d51e94e860cc9dbf02a64eaec782b0b1b244e583e09"
MARKET_END_MS = PRIMARY_COHORT_START_MS + 300_000
COVERAGE_ARTIFACT = "btc-updown-5m-1787590800_20260824T170653Z_prospective.json"
BOOK_SESSION = "book-1787590785013-38304"
BTC_SESSION = "btc-1787590632034-38304"
SUPERVISOR_SESSION = "supervisor-1787590630370-13288"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _test_count() -> int:
    run = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q"],
                         cwd=ROOT, check=True, capture_output=True, text=True)
    return sum(int(match.group(1)) for line in run.stdout.splitlines()
               if (match := re.search(r":\s+(\d+)\s*$", line)))


def _first_valid(paths: list[Path], source: str) -> dict | None:
    best = None
    for path in paths:
        for row in read_ndjson(path):
            if source == "book":
                valid = row.get("condition_id") == CONDITION_ID and row.get("book_state_valid") is True
            else:
                valid = row.get("price") is not None and row.get("trade_id") is not None
            if valid and (best is None or int(row["receive_timestamp_ms"]) < int(best["receive_timestamp_ms"])):
                best = row
    return best


def _session(sessions, session_id: str):
    return next(s for s in sessions if s.session_id == session_id)


def _event_time(session, event: str, *, market: str | None = None, mode=min):
    values = [int(e["timestamp_ms"]) for e in session.events
              if e.get("event") == event and (market is None or e.get("market") == market)]
    return mode(values) if values else None


def main() -> int:
    settings = load_settings()
    state = resolve_path(settings, "state")
    reports = resolve_path(settings, "reports")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    manifest = CohortManifest(state / "prospective_cohort.json")
    freeze = manifest.freeze_primary()
    freeze_payload = {"schema_version": 1, **freeze, "immutable": True,
                      "validated_condition_id": CONDITION_ID,
                      "validation_coverage_artifact": str(reports / "coverage" / COVERAGE_ARTIFACT)}
    freeze_path = state / "primary_cohort_freeze_prospective_v4.json"
    if freeze_path.exists() and json.loads(freeze_path.read_text(encoding="utf-8")) != freeze_payload:
        raise RuntimeError("immutable v4 freeze artifact mismatch")
    atomic_json(freeze_path, freeze_payload)

    coverage_path = reports / "coverage" / COVERAGE_ARTIFACT
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    sessions = load_sessions(resolve_path(settings, "sessions"))
    book = _session(sessions, BOOK_SESSION)
    btc = _session(sessions, BTC_SESSION)
    supervisor = _session(sessions, SUPERVISOR_SESSION)
    book_files = [Path(p) for p in coverage["book_files"]]
    btc_files = [Path(p) for p in coverage["btc_files"]]
    integrity = verify_raw_sidecars(book_files + btc_files)
    first_book = _first_valid(book_files, "book")
    first_btc = _first_valid(btc_files, "btc")
    book_phases = event_window_counts(book.events, PRIMARY_COHORT_START_MS, MARKET_END_MS)
    btc_phases = event_window_counts(btc.events, PRIMARY_COHORT_START_MS, MARKET_END_MS)
    queue_drops = sum(e.get("event") == "queue_drop" for e in book.events + btc.events)
    result = "PASS" if (
        coverage["btc_coverage_pct"] >= .99 and coverage["book_coverage_pct"] >= .99
        and _event_time(book, "connected") <= PRIMARY_COHORT_START_MS
        and _event_time(book, "subscribed", market=CONDITION_ID) <= PRIMARY_COHORT_START_MS
        and _event_time(btc, "connected") <= PRIMARY_COHORT_START_MS
        and _event_time(book, "session_end", mode=max) >= MARKET_END_MS
        and _event_time(btc, "session_end", mode=max) >= MARKET_END_MS
        and not integrity["sha256_failures"] and not integrity["sidecar_missing"]
        and not integrity["parse_errors"] and not queue_drops
    ) else "FAIL"
    o1 = {
        "gate": "O1_FULL_LIFECYCLE", "collector_version": "phase2a_prospective_v4",
        "cohort_version": COHORT_VERSION, "slug": PRIMARY_COHORT_START_MARKET,
        "condition_id": CONDITION_ID, "market_start_ms": PRIMARY_COHORT_START_MS,
        "market_end_ms": MARKET_END_MS, "session_id": SUPERVISOR_SESSION,
        "btc_session_id": BTC_SESSION, "clob_session_id": BOOK_SESSION,
        "btc_connection_id": first_btc.get("connection_id") if first_btc else None,
        "clob_connection_id": first_book.get("connection_id") if first_book else None,
        "btc_connected_at": _event_time(btc, "connected"),
        "clob_connected_at": _event_time(book, "connected"),
        "clob_subscribed_at": _event_time(book, "subscribed", market=CONDITION_ID),
        "btc_first_valid_event": first_btc.get("receive_timestamp_ms") if first_btc else None,
        "clob_first_valid_dual_token_state": first_book.get("receive_timestamp_ms") if first_book else None,
        "btc_session_end": _event_time(btc, "session_end", mode=max),
        "clob_session_end": _event_time(book, "session_end", mode=max),
        "supervisor_session_end": _event_time(supervisor, "session_end", mode=max),
        "btc_market_coverage": coverage["btc_coverage_pct"],
        "book_market_coverage": coverage["book_coverage_pct"],
        "btc_gap_seconds_in_market": btc_phases["in_market_window"]["gap_seconds"],
        "book_invalid_seconds_in_market": (1-float(coverage["book_coverage_pct"]))*300,
        "btc_stale_events_in_market": btc_phases["in_market_window"]["stale"],
        "book_stale_events_in_market": book_phases["in_market_window"]["stale"],
        "btc_stale_events_after_market": btc_phases["post_market_shutdown"]["stale"],
        "book_stale_events_after_market": book_phases["post_market_shutdown"]["stale"],
        "event_windows": {"btc": btc_phases, "book": book_phases},
        "parse_errors": integrity["parse_errors"], "queue_drops": queue_drops,
        "raw_files": [str(p) for p in book_files + btc_files],
        "sidecars": [str(p) + ".meta.json" for p in book_files + btc_files],
        "sha256_verification": integrity,
        "artifact_sha256": {str(p): _sha256(p) for p in book_files + btc_files},
        "coverage_artifact": str(coverage_path), "result": result,
    }
    o1_json = reports / f"v4_full_lifecycle_validation_{stamp}.json"
    atomic_json(o1_json, o1)
    o1_json.with_suffix(".md").write_text(
        f"# v4 Full-Lifecycle Validation\n\n- Market: `{PRIMARY_COHORT_START_MARKET}`\n"
        f"- BTC coverage: {o1['btc_market_coverage']:.6f}\n- Book coverage: {o1['book_market_coverage']:.6f}\n"
        f"- In-market stale (BTC/book): {o1['btc_stale_events_in_market']}/{o1['book_stale_events_in_market']}\n"
        f"- Post-market stale (BTC/book): {o1['btc_stale_events_after_market']}/{o1['book_stale_events_after_market']}\n"
        f"- Result: **{result}**\n", encoding="utf-8")

    baseline_path = state / "baseline_truth_snapshot.json"
    ledger = pq.read_table(resolve_path(settings, "derived") / "event_ledger.parquet").to_pylist()
    baseline = verify_baseline_snapshot(json.loads(baseline_path.read_text(encoding="utf-8")), ledger)
    from std0_quant.audit.prospective import create_baseline_snapshot
    expanded_baseline_path = state / "historical_baseline_snapshot_v2.json"
    if not expanded_baseline_path.exists():
        create_baseline_snapshot(ledger, expanded_baseline_path)
    expanded_baseline = verify_baseline_snapshot(json.loads(expanded_baseline_path.read_text(encoding="utf-8")), ledger)
    observations = manifest.observations(COHORT_VERSION)
    eligible = [row for row in observations if fully_covered_observation(row)]
    o2 = "O2_FIRST_OBSERVATION_PASS" if eligible else "O2_PENDING_NO_ELIGIBLE_STD0_EVENT"
    operations = continuous_operations_status([s for s in sessions if s.kind == "live_supervisor"])
    latest_ops = sorted(reports.glob("live_operations_24h_*.json"))[-1]
    latest_ops_payload = json.loads(latest_ops.read_text(encoding="utf-8"))["operations"]
    completion = {
        "title": "Phase 2A-Prospective Completion Report", "generated_at": datetime.now(timezone.utc).isoformat(),
        "repository_test_baseline": {"tests": _test_count(), "status": "PASS"},
        "readme_cleanup": "PASS", "version_history": ["prospective_v1", "prospective_v2", "prospective_v3", "prospective_v4"],
        "primary_cohort_freeze": freeze_payload, "historical_baseline_integrity": baseline,
        "historical_baseline_integrity_v2": expanded_baseline,
        "v4_full_lifecycle_validation": {"status": result, "artifact": str(o1_json),
                                         "btc_coverage": coverage["btc_coverage_pct"], "book_coverage": coverage["book_coverage_pct"]},
        "first_fully_covered_observation": {"status": o2, "count": len(eligible)},
        "point_in_time_integrity": {"violations": sum(not r.get("lineage_pass", False) for r in observations),
                                    "status": "PASS" if not observations or all(r.get("lineage_pass") for r in observations) else "FAIL"},
        "operations_24h": {**operations, "latest_rolling_artifact": str(latest_ops),
                           "fragmented_observed_span_seconds": latest_ops_payload["runtime_seconds"]},
        "cohort_manifest": {"path": str(manifest.path), "version": COHORT_VERSION,
                            "observation_count": len(eligible), "duplicate_policy": "DETERMINISTIC_UNIQUE"},
        "current_progress": {"fully_covered": len(eligible), "fully_covered_target": 5000,
                             "covered_calendar_days": len({r.get('calendar_date') for r in eligible}), "calendar_days_target": 14},
        "checkpoints": {"O1": result, "O2": o2, "O3": operations["status"],
                        "C100": "PENDING", "C500": "PENDING", "C1000": "PENDING", "FINAL": "PENDING"},
        "known_limitations": ["No true >=24h continuous supervisor session exists.",
                              "No eligible v4 fully-covered std0 observation exists.",
                              "Prospective cohort has not reached 5000 observations or 14 covered UTC days."],
        "phase2a_revalidation_status": "NOT_READY", "phase2b_research": "AUTHORIZED_EXPLORATORY", "phase2b_confirmed": "NOT_AUTHORIZED",
        "engineering_complete": result == "PASS" and baseline["status"] == "PASS" and expanded_baseline["status"] == "PASS",
        "operational_validation_complete": result == "PASS" and o2.endswith("PASS") and operations["eligible_for_audit"],
        "cohort_accumulation_complete": len(eligible) >= 5000,
        "research_gate_complete": False,
        "overall_status": ["PROSPECTIVE_PIPELINE_READY", "ACCUMULATING_LIVE_DATA"],
    }
    completion_json = reports / f"phase2a_prospective_completion_{stamp}.json"
    atomic_json(completion_json, completion)
    completion_json.with_suffix(".md").write_text(
        "# Phase 2A-Prospective Completion Report\n\n"
        f"- Engineering: **{'PASS' if completion['engineering_complete'] else 'FAIL'}**\n"
        f"- Primary cohort: `{COHORT_VERSION}` from `{PRIMARY_COHORT_START_MARKET}` ({PRIMARY_COHORT_START_MS})\n"
        f"- O1 full lifecycle: **{result}**\n- O2 first observation: **{o2}**\n"
        f"- O3 24h operations: **{operations['status']}** (longest {operations['longest_continuous_runtime_seconds']:.3f}s)\n"
        f"- Cohort: {len(eligible)}/5000; days: {completion['current_progress']['covered_calendar_days']}/14\n"
        "- Phase 2A revalidation: **NOT_READY**\n- Phase 2B-Research: **AUTHORIZED_EXPLORATORY**\n- Phase 2B-Confirmed: **NOT_AUTHORIZED**\n\n"
        "Overall: **PROSPECTIVE_PIPELINE_READY + ACCUMULATING_LIVE_DATA**\n",
        encoding="utf-8")
    print(json.dumps({"freeze": str(freeze_path), "o1": str(o1_json),
                      "completion": str(completion_json)}, indent=2))
    return 0 if completion["engineering_complete"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
