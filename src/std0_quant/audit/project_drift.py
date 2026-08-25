"""Deterministic project-drift and milestone governance primitives."""
from __future__ import annotations

from typing import Any, Iterable

DRIFT_CATEGORIES = {
    "NO_DRIFT", "EXPECTED_EVOLUTION", "GOVERNANCE_CHANGE", "ENGINEERING_FIX",
    "METHODOLOGY_CORRECTION", "INTERPRETATION_CORRECTION",
    "DOCUMENTATION_DRIFT", "VERSIONING_DRIFT", "COHORT_GOVERNANCE_CHANGE",
    "RESEARCH_DEFINITION_DRIFT", "RETROSPECTIVE_SELECTION_RISK",
    "DATA_INTEGRITY_FAILURE", "CRITICAL_RESEARCH_DRIFT",
}
MILESTONE_STATUSES = {"PASS", "PENDING", "ACCUMULATING", "FAILED",
                      "INTERRUPTED", "NOT_STARTED", "NOT_AUTHORIZED", "SUPERSEDED"}


def frozen_truth_findings(rule: str, window_seconds: int,
                          horizon_seconds: int) -> list[dict[str, Any]]:
    expected = {"episode_rule": "v1_3sec", "episode_window_seconds": 3,
                "y30_horizon_seconds": 30}
    current = {"episode_rule": rule, "episode_window_seconds": window_seconds,
               "y30_horizon_seconds": horizon_seconds}
    return [] if current == expected else [{
        "id": "P1-FROZEN-TRUTH", "category": "CRITICAL_RESEARCH_DRIFT",
        "severity": "CRITICAL", "expected": expected, "current": current}]


def coverage_gate_findings(btc_threshold: float, book_threshold: float,
                           n_required: int, days_required: int) -> list[dict[str, Any]]:
    current = (float(btc_threshold), float(book_threshold), int(n_required), int(days_required))
    return [] if current == (.99, .99, 5000, 14) else [{
        "id": "P2A-FORMAL-GATE", "category": "CRITICAL_RESEARCH_DRIFT",
        "severity": "CRITICAL", "expected": [.99, .99, 5000, 14],
        "current": list(current)}]


def governance_change(original: str, current: str) -> dict[str, Any]:
    return {"original_state": original, "current_state": current,
            "category": "GOVERNANCE_CHANGE", "severity": "LOW",
            "material_research_drift": False}


def documentation_contradictions(documents: dict[str, str],
                                  phrases: Iterable[str]) -> list[dict[str, str]]:
    return [{"document": name, "statement": phrase}
            for name,text in documents.items() for phrase in phrases
            if phrase.lower() in text.lower()]


def retrospective_expansion(pre_fix_rows: Iterable[dict[str, Any]],
                            effective_from_ms: int) -> list[str]:
    return sorted(str(row.get("condition_id")) for row in pre_fix_rows
                  if int(row.get("session_started_at_ms", -1)) < effective_from_ms
                  and bool(row.get("primary_cohort_included")))


def baseline_change_summary(snapshot_rows: Iterable[dict[str, Any]],
                            current_rows: Iterable[dict[str, Any]],
                            fields: Iterable[str]) -> dict[str, Any]:
    names=tuple(fields);before={str(r.get("condition_id")):{k:r.get(k) for k in names}
                               for r in snapshot_rows}
    after={str(r.get("condition_id")):{k:r.get(k) for k in names}
           for r in current_rows}
    changed=sorted(key for key,value in before.items()
                   if key not in after or after[key] != value)
    return {"historical_rows":len(before),"current_rows":len(after),
            "new_rows":len(set(after)-set(before)),"changed_historical_ids":changed,
            "status":"PASS" if not changed else "FAIL"}


def single_session_o3(sessions: Iterable[dict[str, Any]], target_seconds: int = 86400) -> dict[str, Any]:
    rows=[]
    for row in sessions:
        start=int(row.get("start_ms",0));end=int(row.get("end_ms",start))
        rows.append({"session_id":row.get("session_id"),
                     "runtime_seconds":max(0,(end-start)/1000)})
    longest=max(rows,key=lambda r:r["runtime_seconds"],default=None)
    runtime=longest["runtime_seconds"] if longest else 0
    return {"runtime_seconds":runtime,"target_seconds":target_seconds,
            "session_id":longest["session_id"] if longest else None,
            "status":"PASS" if runtime>=target_seconds else "PENDING",
            "stitched":False}


def milestone(milestone_id: str, track: str, description: str,
              current: Any, target: Any, status: str, *, blocking: bool,
              evidence: str | None, version: str, next_action: str,
              first_achieved_at: str | None = None,
              formal_class: str = "ENGINEERING") -> dict[str, Any]:
    if status not in MILESTONE_STATUSES: raise ValueError(status)
    return {"milestone_id":milestone_id,"track":track,"description":description,
            "required_condition":target,"current_value":current,"target_value":target,
            "status":status,"blocking":blocking,"non_blocking":not blocking,
            "evidence_artifact":evidence,"first_achieved_at":first_achieved_at,
            "version":version,"next_action":next_action,"formal_class":formal_class}


def milestone_status(current: int, target: int, *, authorized: bool = True,
                     interrupted: bool = False) -> str:
    if not authorized:return "NOT_AUTHORIZED"
    if current >= target:return "PASS"
    if interrupted:return "INTERRUPTED"
    return "ACCUMULATING"
