"""Prospective cohort and operations audits (research/data quality only).

This module intentionally contains no model fitting.  It supplies stable,
testable primitives for lifecycle classification, historical truth
invariance, point-in-time lineage, versioned cohort indexing and checkpoint
state.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from std0_quant.storage import canonical_json, read_ndjson

BASELINE_FIELDS = (
    "condition_id", "clean_flag", "exclude_reason", "exclude_detail", "y30",
    "y30_horizon_eligible", "episode_rule_version",
)
CHECKPOINTS = (100, 500, 1000, 5000)
COHORT_VERSION = "prospective_v4"
PRIMARY_COLLECTOR_VERSION = "phase2a_prospective_v4"
PRIMARY_COHORT_START_MS = 1_787_590_800_000
PRIMARY_COHORT_START_MARKET = "btc-updown-5m-1787590800"
COHORT_FREEZE_REASON = "ENGINEERING_VALIDATION_COMPLETE"
OPERATIONS_MIN_RUNTIME_MS = 86_400_000


def observation_identity(row: dict[str, Any], cohort_version: str = COHORT_VERSION) -> str:
    key = "|".join((str(row.get("condition_id")), str(row.get("prediction_ts_ms")),
                    str(row.get("cutoff_mode", "cutoff_1")), cohort_version))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def atomic_json(path: Path | str, payload: dict[str, Any]) -> Path:
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.flush(); os.fsync(handle.fileno())
    os.replace(tmp, path)
    return path


def semantic_row(row: dict[str, Any], fields: Iterable[str] = BASELINE_FIELDS) -> dict[str, Any]:
    return {name: row.get(name) for name in fields}


def baseline_semantic_hash(rows: Iterable[dict[str, Any]]) -> str:
    normalized = sorted((semantic_row(row) for row in rows), key=lambda r: str(r["condition_id"]))
    digest = hashlib.sha256()
    for row in normalized:
        digest.update((canonical_json(row) + "\n").encode("utf-8"))
    return digest.hexdigest()


def create_baseline_snapshot(rows: list[dict[str, Any]], path: Path | str) -> dict[str, Any]:
    compact = sorted((semantic_row(row) for row in rows), key=lambda r: str(r["condition_id"]))
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "fields": list(BASELINE_FIELDS),
        "row_count": len(compact),
        "semantic_sha256": baseline_semantic_hash(compact),
        "rows": compact,
    }
    atomic_json(path, payload)
    return payload


def verify_baseline_snapshot(snapshot: dict[str, Any], current_rows: list[dict[str, Any]]) -> dict[str, Any]:
    fields = tuple(snapshot.get("fields") or BASELINE_FIELDS)
    current = {str(row.get("condition_id")): semantic_row(row, fields) for row in current_rows}
    changed, missing = [], []
    for baseline in snapshot.get("rows", []):
        condition_id = str(baseline.get("condition_id"))
        if condition_id not in current:
            missing.append(condition_id)
        elif current[condition_id] != baseline:
            changed.append({"condition_id": condition_id, "before": baseline,
                            "after": current[condition_id]})
    return {
        "status": "PASS" if not changed and not missing else "FAIL",
        "baseline_rows": len(snapshot.get("rows", [])),
        "current_rows": len(current_rows),
        "new_rows": max(0, len(current_rows) - len(snapshot.get("rows", []))),
        "changed_historical_rows": changed,
        "missing_historical_rows": missing,
    }


def connection_intervals(events: list[dict[str, Any]]) -> list[tuple[int, int]]:
    intervals: list[tuple[int, int]] = []; opened: int | None = None
    for event in sorted(events, key=lambda e: int(e.get("timestamp_ms", 0))):
        timestamp = int(event.get("timestamp_ms", 0))
        if event.get("event") == "connected":
            if opened is not None:
                intervals.append((opened, timestamp))
            opened = timestamp
        elif event.get("event") in {"disconnected", "connection_error", "session_end"} and opened is not None:
            intervals.append((opened, timestamp)); opened = None
    if opened is not None:
        intervals.append((opened, int(max((e.get("timestamp_ms", opened) for e in events), default=opened))))
    return intervals


def classify_market_lifecycle(
    condition_id: str, start_ms: int, end_ms: int,
    book_session_events: list[list[dict[str, Any]]],
    btc_session_events: list[list[dict[str, Any]]],
) -> dict[str, Any]:
    # Lifecycle is endpoint readiness, while gaps/reconnects are assessed by
    # the independent 99% coverage gate. Requiring one uninterrupted socket
    # interval conflated network health with lifecycle and marked a recorder
    # that was ready at both endpoints PARTIAL after any mid-market reconnect.
    def book_endpoint(timestamp_ms: int) -> dict[str, Any] | None:
        for events in book_session_events:
            subscribed = [int(e["timestamp_ms"]) for e in events
                          if e.get("event") == "subscribed"
                          and e.get("market") == condition_id
                          and int(e.get("timestamp_ms", 0)) <= timestamp_ms]
            interval = next(((lo, hi) for lo, hi in connection_intervals(events)
                             if lo <= timestamp_ms <= hi), None)
            if subscribed and interval:
                return {"connected_ms": interval[0],
                        "subscribed_ms": min(subscribed),
                        "ended_ms": interval[1]}
        return None
    def btc_endpoint(timestamp_ms: int) -> bool:
        return any(lo <= timestamp_ms <= hi for events in btc_session_events
                   for lo, hi in connection_intervals(events))
    book_start = book_endpoint(start_ms);book_end = book_endpoint(end_ms)
    book_ready = bool(book_start and book_end)
    btc_ready = btc_endpoint(start_ms) and btc_endpoint(end_ms)
    book_evidence = ({"start": book_start, "end": book_end}
                     if book_start or book_end else None)
    return {
        "condition_id": condition_id,
        "lifecycle": "FULL_LIFECYCLE_MARKET" if book_ready and btc_ready else "PARTIAL_SESSION_MARKET",
        "book_full_lifecycle": book_ready,
        "btc_full_lifecycle": btc_ready,
        "book_evidence": book_evidence,
    }


def percentile_summary(values: Iterable[float | None]) -> dict[str, Any]:
    data = np.asarray([float(v) for v in values if v is not None and math.isfinite(float(v))])
    if not len(data):
        return {k: None for k in ("count", "min", "p01", "p05", "median", "p95", "p99", "max", "pct_ge_99", "pct_ge_999")}
    return {
        "count": int(len(data)), "min": float(data.min()),
        "p01": float(np.percentile(data, 1)), "p05": float(np.percentile(data, 5)),
        "median": float(np.median(data)), "p95": float(np.percentile(data, 95)),
        "p99": float(np.percentile(data, 99)), "max": float(data.max()),
        "pct_ge_99": float(np.mean(data >= .99)), "pct_ge_999": float(np.mean(data >= .999)),
    }


def coverage_quality(btc_values: Iterable[float | None], book_values: Iterable[float | None]) -> dict[str, Any]:
    btc = percentile_summary(btc_values); book = percentile_summary(book_values)
    warning = any(summary["count"] and summary["pct_ge_99"] < 1.0 for summary in (btc, book))
    return {"btc": btc, "book": book,
            "status": "COVERAGE_QUALITY_WARNING" if warning else "PASS"}


def expected_market_slugs(start_ms: int, end_ms: int, prefix: str = "btc-updown-5m-",
                          window_seconds: int = 300) -> list[str]:
    window_ms = window_seconds * 1000
    first = ((start_ms + window_ms - 1) // window_ms) * window_ms
    return [f"{prefix}{stamp//1000}" for stamp in range(first, end_ms-window_ms+1, window_ms)]


def build_operations_summary(start_ms: int, end_ms: int,
                             sessions: Iterable[Any], market_audits: list[dict[str, Any]],
                             raw_integrity: dict[str, Any], raw_meta: list[dict[str, Any]]) -> dict[str, Any]:
    session_list = list(sessions); events = [e for s in session_list for e in s.events
                                             if start_ms <= int(e.get("timestamp_ms", 0)) <= end_ms]
    discovered = {str(e.get("slug")) for e in events if e.get("event") == "market_discovered" and e.get("slug")}
    expected = set(expected_market_slugs(start_ms, end_ms))
    by_kind = Counter(s.kind for s in session_list if any(start_ms <= int(e.get("timestamp_ms",0)) <= end_ms for e in s.events))
    connected = Counter(s.kind for s in session_list for e in s.events if e.get("event") == "connected" and start_ms <= int(e.get("timestamp_ms",0)) <= end_ms)
    disconnected = Counter(s.kind for s in session_list for e in s.events if e.get("event") in {"disconnected","connection_error"} and start_ms <= int(e.get("timestamp_ms",0)) <= end_ms)
    records = Counter()
    for meta in raw_meta:
        first = meta.get("first_timestamp_ms"); last = meta.get("last_timestamp_ms")
        if first is not None and last is not None and int(first) <= end_ms and int(last) >= start_ms:
            records[str(meta.get("source"))] += int(meta.get("record_count", 0))
    full = [m for m in market_audits if m.get("lifecycle") == "FULL_LIFECYCLE_MARKET"]
    partial = [m for m in market_audits if m.get("lifecycle") != "FULL_LIFECYCLE_MARKET"]
    quality = coverage_quality([m.get("btc_coverage_pct") for m in full],
                               [m.get("book_coverage_pct") for m in full])
    by_version={}
    for version in sorted({str(m.get("collector_version")) for m in full}):
        group=[m for m in full if str(m.get("collector_version"))==version]
        by_version[version]=coverage_quality([m.get("btc_coverage_pct") for m in group],[m.get("book_coverage_pct") for m in group])
    btc_gap_seconds = sum(float(e.get("duration_ms",0))/1000 for e in events if e.get("event")=="gap_detected" and e.get("source")=="BINANCE_BTC")
    integrity_fail = bool(raw_integrity.get("sidecar_missing") or raw_integrity.get("corrupt_raw_files") or raw_integrity.get("sha256_failures") or raw_integrity.get("parse_errors") or any(e.get("event")=="queue_drop" for e in events))
    phase_counts = operations_event_phase_counts(events, ((m["market_start_ms"], m["market_end_ms"])
                                                          for m in market_audits
                                                          if m.get("market_start_ms") is not None and m.get("market_end_ms") is not None))
    operations_24h = continuous_operations_status((s for s in session_list if s.kind == "live_supervisor"), integrity_fail)
    status = "RECORDER_INTEGRITY_FAILURE" if integrity_fail else "DATA_QUALITY_WARNING" if quality["status"]!="PASS" or expected-discovered else "PASS"
    return {"session_start":start_ms,"session_end":end_ms,"runtime_seconds":(end_ms-start_ms)/1000,
            "expected_btc5m_markets":len(expected),"discovered_markets":len(discovered),
            "missed_markets":sorted(expected-discovered),"market_rotations":sum(e.get("event")=="market_rotate" for e in events),
            "full_lifecycle_markets":len(full),"partial_session_markets":len(partial),
            "btc_records":records["binance_btc"],"book_records":records["polymarket_book"],
            "btc_disconnects":disconnected["btc_ticks"],"btc_reconnects":max(0,connected["btc_ticks"]-by_kind["btc_ticks"]),
            "book_disconnects":disconnected["polymarket_book"],"book_reconnects":max(0,connected["polymarket_book"]-by_kind["polymarket_book"]),
            "btc_gap_seconds":btc_gap_seconds,"book_invalid_seconds":sum((1-float(m.get("book_coverage_pct") or 0))*300 for m in full),
            **phase_counts,"operations_24h":operations_24h,
            "watchdog_events":sum(e.get("event")=="stale_feed_detected" for e in events),"stale_events":sum(e.get("event")=="stale_feed_detected" for e in events),
            "parse_errors":raw_integrity.get("parse_errors",0),"internal_queue_drops":sum(e.get("event")=="queue_drop" for e in events),
            **raw_integrity,"clock_warnings":sum(e.get("event")=="clock_warning" for e in events),"disk_warnings":sum(e.get("event")=="disk_warning" for e in events),
            "unexpected_process_exits":sum(e.get("event")=="child_restart" for e in events),
            "full_lifecycle_coverage":quality,"full_lifecycle_coverage_by_collector_version":by_version,"status":status}


def verify_raw_sidecars(raw_files: Iterable[Path]) -> dict[str, Any]:
    missing, corrupt, sha_failures, parse_errors = [], [], [], 0
    files = list(raw_files)
    for path in files:
        sidecar = Path(str(path) + ".meta.json")
        if not sidecar.exists():
            missing.append(str(path)); continue
        try:
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
            hasher = hashlib.sha256()
            with open(path, "rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    hasher.update(chunk)
            digest = hasher.hexdigest()
            if meta.get("sha256") != digest: sha_failures.append(str(path))
            parse_errors += int(meta.get("parse_errors", 0))
            if meta.get("integrity_status") not in (None, "OK"): corrupt.append(str(path))
        except (OSError, ValueError, json.JSONDecodeError):
            corrupt.append(str(path))
    return {"raw_file_count": len(files), "sidecar_count": len(files)-len(missing),
            "sidecar_missing": missing, "corrupt_raw_files": corrupt,
            "sha256_failures": sha_failures, "parse_errors": parse_errors}


def schema_profile(rows: Iterable[dict[str, Any]], expected: set[str]) -> dict[str, Any]:
    observed: set[str] = set(); count = 0; versions = Counter()
    for row in rows:
        count += 1; observed.update(row); versions[str(row.get("schema_version"))] += 1
    missing = sorted(expected - observed); unknown = sorted(observed - expected)
    return {"rows": count, "schema_versions": dict(versions),
            "observed_fields": sorted(observed), "missing_expected_fields": missing,
            "unknown_fields": unknown,
            "status": "SCHEMA_DRIFT_WARNING" if missing else "PASS"}


def sanity_audit(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    for row in rows:
        for name in ("opp_best_bid", "opp_best_ask", "opp_mid"):
            value = row.get(name)
            if value is not None and not 0 <= float(value) <= 1:
                failures.append({"condition_id": row.get("condition_id"), "field": name, "value": value})
        bid, ask = row.get("opp_best_bid"), row.get("opp_best_ask")
        if bid is not None and ask is not None and float(bid) > float(ask):
            failures.append({"condition_id": row.get("condition_id"), "field": "crossed_book", "value": [bid, ask]})
        for name, value in row.items():
            if ("depth" in name and value is not None and float(value) < 0) or ("obi" in name and value is not None and not -1 <= float(value) <= 1):
                failures.append({"condition_id": row.get("condition_id"), "field": name, "value": value})
        for name in ("btc_start_price", "btc_last_price", "btc_cutoff_price"):
            value = row.get(name)
            if value is not None and float(value) <= 0:
                failures.append({"condition_id": row.get("condition_id"), "field": name, "value": value})
    return {"status": "PASS" if not failures else "DATA_SANITY_WARNING",
            "violation_count": len(failures), "violations": failures[:100]}


def lineage_audit(feature: dict[str, Any], provenance: list[dict[str, Any]],
                  required_source_types: set[str] | None = None) -> dict[str, Any]:
    required = required_source_types or {"binance_btc", "polymarket_book", "phase1_truth"}
    cutoff = int(feature["feature_cutoff_ms"]); prediction = int(feature["prediction_ts_ms"])
    public_violations, truth_violations, missing_files = [], [], []
    observed_sources = {str(row.get("source_type")) for row in provenance}
    for row in provenance:
        maximum = row.get("source_timestamp_max_ms")
        if maximum is not None and row.get("source_type") in {"binance_btc", "polymarket_book"} and int(maximum) > cutoff:
            public_violations.append(row.get("feature_name"))
        if maximum is not None and row.get("source_type") == "phase1_truth" and int(maximum) > prediction:
            truth_violations.append(row.get("feature_name"))
        for name in str(row.get("source_file") or "").split(";"):
            if name and not Path(name).exists(): missing_files.append(name)
    missing_sources = sorted(required - observed_sources)
    if public_violations:
        status = "POINT_IN_TIME_FAILURE"
    elif truth_violations:
        status = "TRUTH_TIMESTAMP_FAILURE"
    elif missing_files:
        missing_text = " ".join(missing_files).lower()
        status = ("LINEAGE_FAIL_MISSING_BTC_RAW" if "btc_ticks" in missing_text else
                  "LINEAGE_FAIL_MISSING_BOOK_RAW" if "polymarket_book" in missing_text else
                  "LINEAGE_FAIL_MISSING_FILL_SOURCE")
    elif missing_sources:
        status = "LINEAGE_FAIL_PROVENANCE"
    else:
        status = "LINEAGE_PASS"
    return {"status": status,
            "public_timestamp_violations": public_violations,
            "truth_timestamp_violations": truth_violations,
            "missing_source_files": sorted(set(missing_files)),
            "missing_source_types": missing_sources,
            "provenance_row_count": len(provenance)}


class CohortManifest:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.payload = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {"schema_version": 2, "cohorts": {}}

    def freeze_primary(
        self, version: str = COHORT_VERSION,
        start_ms: int = PRIMARY_COHORT_START_MS,
        start_market: str = PRIMARY_COHORT_START_MARKET,
        reason: str = COHORT_FREEZE_REASON,
    ) -> dict[str, Any]:
        frozen = {
            "primary_cohort_version": version,
            "primary_cohort_start_ms": int(start_ms),
            "primary_cohort_start_market": start_market,
            "cohort_freeze_reason": reason,
        }
        existing = self.payload.get("primary_cohort")
        if existing is not None and existing != frozen:
            raise RuntimeError("primary cohort metadata is immutable once frozen")
        self.payload["schema_version"] = max(2, int(self.payload.get("schema_version", 1)))
        self.payload["primary_cohort"] = frozen
        for old_version, cohort in self.payload.setdefault("cohorts", {}).items():
            cohort["preserved"] = True
            cohort["primary_model_eligible"] = old_version == version
        atomic_json(self.path, self.payload)
        return frozen

    def observations(self, version: str = COHORT_VERSION) -> list[dict[str, Any]]:
        return self.payload.get("cohorts", {}).get(version, {}).get("observations", [])

    def upsert(self, rows: Iterable[dict[str, Any]], version: str = COHORT_VERSION) -> dict[str, int]:
        cohort = self.payload.setdefault("cohorts", {}).setdefault(version, {
            "created_at": datetime.now(timezone.utc).isoformat(), "observations": []})
        primary = self.payload.get("primary_cohort", {})
        existing = {row["observation_id"] for row in cohort["observations"]}
        inserted = duplicates = excluded_version = excluded_before_start = 0
        for row in rows:
            item = dict(row); item["cohort_version"] = version
            if primary and version == primary.get("primary_cohort_version"):
                if item.get("collector_version") != PRIMARY_COLLECTOR_VERSION:
                    excluded_version += 1; continue
                if int(item.get("market_start_ms", -1)) < int(primary.get("primary_cohort_start_ms", PRIMARY_COHORT_START_MS)):
                    excluded_before_start += 1; continue
            item["observation_id"] = observation_identity(item, version)
            if item["observation_id"] in existing:
                duplicates += 1; continue
            cohort["observations"].append(item); existing.add(item["observation_id"]); inserted += 1
        atomic_json(self.path, self.payload)
        return {"inserted": inserted, "duplicates": duplicates,
                "excluded_version": excluded_version,
                "excluded_before_start": excluded_before_start,
                "total": len(cohort["observations"])}


def fully_covered_observation(row: dict[str, Any]) -> bool:
    return all(bool(row.get(name)) for name in
               ("coverage_pass", "provenance_pass", "sanity_pass", "lineage_pass"))


def event_window_counts(events: Iterable[dict[str, Any]], start_ms: int, end_ms: int) -> dict[str, Any]:
    """Keep pre-market, in-market and post-market operational events separate."""
    result: dict[str, Any] = {
        "pre_market": {"stale": 0, "gap": 0, "gap_seconds": 0.0},
        "in_market_window": {"stale": 0, "gap": 0, "gap_seconds": 0.0},
        "post_market_shutdown": {"stale": 0, "gap": 0, "gap_seconds": 0.0},
    }
    for event in events:
        timestamp = int(event.get("timestamp_ms", 0))
        bucket = ("pre_market" if timestamp < start_ms else
                  "in_market_window" if timestamp < end_ms else
                  "post_market_shutdown")
        if event.get("event") == "stale_feed_detected":
            result[bucket]["stale"] += 1
        elif event.get("event") == "gap_detected":
            result[bucket]["gap"] += 1
            result[bucket]["gap_seconds"] += float(event.get("duration_ms", 0)) / 1000
    return result


def operations_event_phase_counts(events: Iterable[dict[str, Any]],
                                  market_windows: Iterable[tuple[int, int]]) -> dict[str, Any]:
    """Classify each operational event once against the latest market window."""
    windows = sorted((int(lo), int(hi)) for lo, hi in market_windows)
    result = {"in_market_gap_seconds": 0.0, "post_market_gap_seconds": 0.0,
              "in_market_stale": 0, "post_market_stale": 0}
    for event in events:
        timestamp = int(event.get("timestamp_ms", 0))
        prior = [window for window in windows if window[0] <= timestamp]
        if not prior:
            continue
        lo, hi = prior[-1]
        phase = "in_market" if lo <= timestamp < hi else "post_market"
        if event.get("event") == "stale_feed_detected":
            result[f"{phase}_stale"] += 1
        elif event.get("event") == "gap_detected":
            result[f"{phase}_gap_seconds"] += float(event.get("duration_ms", 0)) / 1000
    return result


def continuous_operations_status(supervisor_sessions: Iterable[Any], integrity_failure: bool = False) -> dict[str, Any]:
    """Audit true individual supervisor sessions; never stitch across downtime."""
    evidence = []
    for session in supervisor_sessions:
        starts = [int(e["timestamp_ms"]) for e in session.events if e.get("event") == "session_start"]
        ends = [int(e["timestamp_ms"]) for e in session.events if e.get("event") == "session_end"]
        if starts and ends:
            start, end = min(starts), max(ends)
            evidence.append({"session_id": session.session_id, "start_ms": start,
                             "end_ms": end, "runtime_seconds": (end-start)/1000})
    longest = max((e["runtime_seconds"] for e in evidence), default=0.0)
    eligible = longest >= OPERATIONS_MIN_RUNTIME_MS / 1000
    if integrity_failure and eligible:
        status = "24H_OPERATIONS_FAIL_INTEGRITY"
    elif eligible:
        status = "24H_OPERATIONS_PASS"
    else:
        status = "24H_OPERATIONS_PENDING_NOT_ENOUGH_RUNTIME"
    return {"status": status, "minimum_runtime_seconds": 86400,
            "longest_continuous_runtime_seconds": longest,
            "eligible_for_audit": eligible, "sessions": evidence}


def covered_calendar_days(observations: Iterable[dict[str, Any]]) -> int:
    return len({str(row.get("calendar_date")) for row in observations
                if row.get("coverage_pass") and row.get("calendar_date")})


def prospective_status(state_dir: Path | str, reports_dir: Path | str) -> dict[str, Any]:
    state_dir=Path(state_dir);reports_dir=Path(reports_dir);manifest=CohortManifest(state_dir/"prospective_cohort.json")
    observations=manifest.observations(COHORT_VERSION);fully=sum(fully_covered_observation(r) for r in observations);days=covered_calendar_days(r for r in observations if fully_covered_observation(r))
    checkpoint_state=state_dir/"prospective_checkpoint_state.json";triggered=json.loads(checkpoint_state.read_text(encoding="utf-8")).get("triggered",[]) if checkpoint_state.exists() else []
    next_checkpoint=next((point for point in CHECKPOINTS if fully<point),None);provenance_violations=sum(not r.get("provenance_pass",False) for r in observations);sanity_warnings=sum(not r.get("sanity_pass",False) for r in observations)
    daily_files=sorted(reports_dir.glob("prospective_daily_*.json"));daily=json.loads(daily_files[-1].read_text(encoding="utf-8")) if daily_files else {};quality=daily.get("readiness_status","ACCUMULATING_LIVE_DATA")
    healthy_days=sum(bool(json.loads(path.read_text(encoding="utf-8")).get("healthy_recorder_day")) for path in daily_files)
    readiness="READY_FOR_PHASE2A_REVALIDATION" if fully>=5000 and days>=14 and provenance_violations==0 and quality!="RECORDER_INTEGRITY_FAILURE" else "RECORDER_INTEGRITY_FAILURE" if quality=="RECORDER_INTEGRITY_FAILURE" else "ACCUMULATING_LIVE_DATA"
    completion_files=sorted(reports_dir.glob("phase2a_prospective_completion_*.json"));completion=json.loads(completion_files[-1].read_text(encoding="utf-8")) if completion_files else {}
    gates=completion.get("checkpoints",{})
    return {"cohort_version":COHORT_VERSION,"primary_cohort":manifest.payload.get("primary_cohort"),"engineering":"PASS" if completion.get("engineering_complete") else "PENDING","o1_full_lifecycle":gates.get("O1","PENDING"),"o2_first_observation":gates.get("O2","PENDING_NO_ELIGIBLE_STD0_EVENT"),"o3_24h":gates.get("O3","24H_OPERATIONS_PENDING_NOT_ENOUGH_RUNTIME"),"fully_covered":fully,"covered_calendar_days":days,"healthy_recorder_days":healthy_days,"next_checkpoint":next_checkpoint,"last_checkpoint":max(triggered,default=None),"provenance_violations":provenance_violations,"sanity_warnings":sanity_warnings,"point_in_time_violations":sum(not r.get("lineage_pass",False) for r in observations),"data_quality":quality,"readiness_status":readiness,"phase2b_research":"AUTHORIZED_EXPLORATORY","phase2b_confirmed":"NOT_AUTHORIZED","strategy_research":"NOT_AUTHORIZED","pnl_execution":"NOT_AUTHORIZED"}


def trigger_checkpoints(count: int, state_path: Path | str, manual: int | None = None) -> list[int]:
    path = Path(state_path)
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"triggered": []}
    triggered = set(int(x) for x in state.get("triggered", []))
    due = [manual] if manual is not None else [point for point in CHECKPOINTS if count >= point and point not in triggered]
    if manual is None:
        triggered.update(due); state["triggered"] = sorted(triggered); atomic_json(path, state)
    return [int(x) for x in due]


def feature_missingness(rows: list[dict[str, Any]], metadata: set[str]) -> list[dict[str, Any]]:
    result = []
    for name in sorted(set().union(*(row.keys() for row in rows)) - metadata if rows else []):
        missing = sum(row.get(name) is None for row in rows)
        reasons = Counter(str(row.get("model_ineligible_reason")) for row in rows if row.get(name) is None)
        result.append({"feature": name, "missing_count": missing,
                       "missing_rate": missing / len(rows), "missing_reason_counts": dict(reasons)})
    return result


def distribution_summary(rows: list[dict[str, Any]], fields: Iterable[str], group_fields: Iterable[str]) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    group_fields = tuple(group_fields)
    for row in rows: groups.setdefault(tuple(row.get(g) for g in group_fields), []).append(row)
    output = []
    for key, group in groups.items():
        labels = dict(zip(group_fields, key))
        for field in fields:
            values = np.asarray([float(r[field]) for r in group if r.get(field) is not None and math.isfinite(float(r[field]))])
            output.append({**labels, "feature": field, "count": int(len(values)),
                           "mean": float(values.mean()) if len(values) else None,
                           "std": float(values.std(ddof=1)) if len(values)>1 else None,
                           "p01": float(np.percentile(values,1)) if len(values) else None,
                           "p05": float(np.percentile(values,5)) if len(values) else None,
                           "median": float(np.median(values)) if len(values) else None,
                           "p95": float(np.percentile(values,95)) if len(values) else None,
                           "p99": float(np.percentile(values,99)) if len(values) else None})
    return output
