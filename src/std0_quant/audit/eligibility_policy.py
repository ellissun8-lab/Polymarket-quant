"""Prospective-only eligibility governance for the network stability fix.

The v2 lifecycle classifier is intentionally *not* a collector version bump.
This module keeps the policy boundary explicit and prevents sensitivity
reclassification of pre-fix markets from expanding the primary cohort.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from std0_quant.audit.prospective import atomic_json
from std0_quant.collectors.network_stability import NETWORK_ENGINEERING_FIX_VERSION

COLLECTOR_VERSION = "phase2a_prospective_v4"
ELIGIBILITY_POLICY_VERSION = "prospective_v4_eligibility_v2"
LEGACY_ELIGIBILITY_POLICY_VERSION = "prospective_v4_eligibility_v1"
RECORDER_RELIABILITY_FIX_VERSION = "recorder_reliability_fix_v1"
PRIMARY_COHORT_RETROACTIVE_RECLASSIFICATION = "FORBIDDEN"
COVERAGE_THRESHOLD = 0.99


def freeze_eligibility_policy(path: Path | str, session_id: str,
                              started_at_ms: int,
                              engineering_fix_version: str) -> dict[str, Any]:
    """Create the immutable effective-from boundary at supervisor startup."""
    if engineering_fix_version != NETWORK_ENGINEERING_FIX_VERSION:
        raise ValueError("eligibility v2 requires network_stability_fix_v1")
    frozen = {
        "schema_version": 1,
        "eligibility_policy_version": ELIGIBILITY_POLICY_VERSION,
        "effective_from_session_id": str(session_id),
        "effective_from_timestamp_ms": int(started_at_ms),
        "engineering_fix_version": engineering_fix_version,
        "collector_version": COLLECTOR_VERSION,
        "coverage_threshold": COVERAGE_THRESHOLD,
        "primary_cohort_retroactive_reclassification":
            PRIMARY_COHORT_RETROACTIVE_RECLASSIFICATION,
    }
    target = Path(path)
    if target.exists():
        existing = json.loads(target.read_text(encoding="utf-8"))
        required = {"eligibility_policy_version": ELIGIBILITY_POLICY_VERSION,
                    "engineering_fix_version": NETWORK_ENGINEERING_FIX_VERSION,
                    "collector_version": COLLECTOR_VERSION,
                    "primary_cohort_retroactive_reclassification":
                        PRIMARY_COHORT_RETROACTIVE_RECLASSIFICATION}
        if any(existing.get(key) != value for key,value in required.items()):
            raise RuntimeError("existing eligibility policy freeze is incompatible")
        # A later supervisor must load, not replace, the first-session
        # boundary. This is what makes restarts safe and auditable.
        return existing
    atomic_json(target, frozen)
    return frozen


def eligibility_decision(row: dict[str, Any], *, policy_version: str) -> dict[str, Any]:
    """Evaluate v1/v2 as an auditable decision without mutating the row."""
    reasons: list[str] = []
    if row.get("collector_version") != COLLECTOR_VERSION:
        reasons.append("COLLECTOR_VERSION_MISMATCH")
    lifecycle_field = ("legacy_lifecycle" if policy_version == LEGACY_ELIGIBILITY_POLICY_VERSION
                       else "lifecycle")
    if row.get(lifecycle_field) != "FULL_LIFECYCLE_MARKET":
        reasons.append("PARTIAL_SESSION_MARKET")
    if float(row.get("btc_coverage_pct") or 0.0) < COVERAGE_THRESHOLD:
        reasons.append("BTC_COVERAGE_LT_99")
    if float(row.get("book_coverage_pct") or 0.0) < COVERAGE_THRESHOLD:
        reasons.append("BOOK_COVERAGE_LT_99")
    return {"status": "ELIGIBLE" if not reasons else "EXCLUDED", "reasons": reasons}


def primary_policy_decision(row: dict[str, Any], freeze: dict[str, Any]) -> dict[str, Any]:
    """Apply v2 only to sessions at/after its frozen, correctly-versioned boundary."""
    started = int(row.get("session_started_at_ms", -1))
    effective = int(freeze["effective_from_timestamp_ms"])
    post_fix = (started >= effective and
                row.get("engineering_fix_version") == NETWORK_ENGINEERING_FIX_VERSION)
    if not post_fix:
        return {"status": "EXCLUDED", "reasons": ["PRE_EFFECTIVE_POLICY_SESSION"],
                "primary_cohort_included": False}
    decision = eligibility_decision(row, policy_version=ELIGIBILITY_POLICY_VERSION)
    return {**decision, "primary_cohort_included": decision["status"] == "ELIGIBLE"}


def eligibility_migration_audit(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Compare classifiers for sensitivity; never authorize historical backfill."""
    records = []
    transitions: Counter[str] = Counter()
    for source in rows:
        old = ({"status": source["eligibility_v1_status"],
                "reasons": list(source.get("eligibility_v1_reasons", []))}
               if source.get("eligibility_v1_status") in {"ELIGIBLE", "EXCLUDED"}
               else eligibility_decision(source, policy_version=LEGACY_ELIGIBILITY_POLICY_VERSION))
        new = eligibility_decision(source, policy_version=ELIGIBILITY_POLICY_VERSION)
        transition = f"{old['status']} -> {new['status']}"
        transitions[transition] += 1
        records.append({
            "condition_id": source.get("condition_id"),
            "market_start_ms": source.get("market_start_ms"),
            "old_status": old["status"], "new_status": new["status"],
            "old_reason": old["reasons"], "new_reason": new["reasons"],
            "transition": transition,
            "primary_cohort_action": "PRESERVE_ORIGINAL_DECISION",
        })
    changed = [r for r in records if r["old_status"] != r["new_status"]]
    return {
        "old_policy_version": LEGACY_ELIGIBILITY_POLICY_VERSION,
        "new_policy_version": ELIGIBILITY_POLICY_VERSION,
        "market_count": len(records), "market_count_changed": len(changed),
        "transition_counts": dict(sorted(transitions.items())),
        "changed_markets": changed, "markets": records,
        "primary_cohort_retroactive_reclassification":
            PRIMARY_COHORT_RETROACTIVE_RECLASSIFICATION,
    }


def o3_candidate_status(sessions: Iterable[dict[str, Any]], freeze: dict[str, Any],
                        now_ms: int) -> dict[str, Any]:
    """Return the latest valid post-fix session; sessions are never stitched."""
    effective = int(freeze["effective_from_timestamp_ms"])
    valid = [s for s in sessions
             if int(s.get("started_at_ms", -1)) >= effective
             and s.get("engineering_fix_version") == NETWORK_ENGINEERING_FIX_VERSION]
    if not valid:
        return {"status": "O3_CANDIDATE_NOT_STARTED", "session_id": None,
                "runtime_seconds": 0.0, "resets": 0}
    valid.sort(key=lambda s: int(s["started_at_ms"]))
    current = valid[-1]
    end = int(current.get("ended_at_ms") or now_ms)
    runtime = max(0.0, (end-int(current["started_at_ms"]))/1000)
    return {"status": ("24H_OPERATIONS_PASS" if runtime >= 86400 and
                        not current.get("integrity_failure") else
                        "24H_OPERATIONS_FAIL_INTEGRITY" if runtime >= 86400 else
                        "24H_OPERATIONS_PENDING_NOT_ENOUGH_RUNTIME"),
            "session_id": current.get("session_id"), "runtime_seconds": runtime,
            "resets": max(0, len(valid)-1)}
