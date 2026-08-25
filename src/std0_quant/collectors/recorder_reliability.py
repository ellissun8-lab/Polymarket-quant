"""Recorder reliability engineering gate (Phase 2B v3.1 spec sections 6-9).

Detects and records the memory / fault-isolation hotfix state of the live
recorder. Engineering only: none of these items change raw event, parser,
book reconstruction, timestamp or coverage semantics, so the collector
version stays ``phase2a_prospective_v4`` and ``ENGINEERING_FIX_VERSION`` is
recorded instead of a collector/cohort version bump.
"""
from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, Callable

from std0_quant.collectors import live_health, live_storage, polymarket_book
from std0_quant.collectors import ws_runner

ENGINEERING_FIX_VERSION = "recorder_reliability_fix_v1"

HOTFIX_ITEMS = (
    "STREAMING_SHA256",
    "HEALTH_TAIL_READER_BOUNDED",
    "HEALTH_FAILURE_ISOLATION",
    "TASK_EXCEPTION_OWNERSHIP",
    "FAILURE_CLASSIFICATION",
    "ORPHAN_SIDECAR_STARTUP_RECOVERY",
    "MEMORY_QUEUE_TELEMETRY",
    "ANALYSIS_NEVER_STOPS_RECORDER",
)


def health_step_isolated(
    build: Callable[[], dict[str, Any]],
    publish: Callable[[dict[str, Any]], Any],
    journal: Any = None,
) -> dict[str, Any]:
    """Run one health build+publish step with failure isolation.

    A health failure (including MemoryError) is classified
    HEALTH_REPORT_FAILURE, journaled and swallowed: the caller is the
    supervisor loop that keeps the raw collectors alive, so a broken health
    path must never take the collectors down with it.
    """
    try:
        payload = build()
        publish(payload)
        return {"status": "OK", "payload": payload}
    except MemoryError as exc:
        _journal_health_failure(journal, exc, "MEMORY_ERROR")
        return {"status": "HEALTH_REPORT_FAILURE", "failure_kind": "MEMORY_ERROR",
                "error": repr(exc)}
    except BaseException as exc:  # noqa: BLE001 - isolation is the point
        _journal_health_failure(journal, exc, "HEALTH_REPORT_FAILURE")
        return {"status": "HEALTH_REPORT_FAILURE",
                "failure_kind": "HEALTH_REPORT_FAILURE", "error": repr(exc)}


def _journal_health_failure(journal: Any, exc: BaseException, kind: str) -> None:
    if journal is None:
        return
    try:
        journal.emit("health_report_failure", failure_kind=kind,
                     error=repr(exc))
    except Exception:  # noqa: BLE001 - journaling must never raise either
        pass


def _source(obj: Any) -> str:
    try:
        return inspect.getsource(obj)
    except (OSError, TypeError):
        return ""


def _script_source(name: str) -> str:
    path = Path(__file__).resolve().parents[3] / "scripts" / name
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def detect_recorder_hotfix() -> dict[str, Any]:
    """Behavioral/source detection of the recorder reliability hotfix items.

    Checks the actual live modules and scripts (not assumptions); each item
    is PASS / PARTIAL / MISSING and the overall state is MEMORY_HOTFIX_PASS /
    MEMORY_HOTFIX_PARTIAL / MEMORY_HOTFIX_MISSING.
    """
    checks: dict[str, str] = {}
    supervisor_src = _script_source("run_live_supervisor.py")
    collect_src = _script_source("collect_live.py")

    ok = (hasattr(live_storage, "streaming_sha256")
          and "readinto" in _source(live_storage.streaming_sha256))
    checks["STREAMING_SHA256"] = "PASS" if ok else "MISSING"

    ok = (hasattr(live_health, "read_last_line_bounded")
          and "splitlines" not in _source(live_health._raw_stats))
    checks["HEALTH_TAIL_READER_BOUNDED"] = "PASS" if ok else "MISSING"

    ok = ("health_step_isolated" in supervisor_src
          and "health_report_failure" in (_source(health_step_isolated)
                                          + _source(_journal_health_failure)))
    checks["HEALTH_FAILURE_ISOLATION"] = "PASS" if ok else "MISSING"

    ok = ("_close_tasks" in _source(ws_runner.ReconnectingWsSession)
          and "return_exceptions=True" in collect_src)
    checks["TASK_EXCEPTION_OWNERSHIP"] = "PASS" if ok else "MISSING"

    ok = (hasattr(live_storage, "RawWriteError")
          and hasattr(live_storage, "SidecarFinalizationError")
          and "SIDECAR_FINALIZATION_FAILURE" in _source(polymarket_book))
    checks["FAILURE_CLASSIFICATION"] = "PASS" if ok else "MISSING"

    sig = str(inspect.signature(live_storage.finalize_orphan_sidecars))
    ok = ("skip_newer_than_seconds" in sig
          and "skip_newer_than_seconds" in supervisor_src)
    checks["ORPHAN_SIDECAR_STARTUP_RECOVERY"] = "PASS" if ok else "MISSING"

    rss_ok = "process_rss_mb" in _source(live_health.build_health)
    queue_ok = "queue_backpressure" in _source(polymarket_book)
    checks["MEMORY_QUEUE_TELEMETRY"] = (
        "PASS" if rss_ok and queue_ok
        else "PARTIAL" if rss_ok or queue_ok else "MISSING")

    runner_src = _script_source("run_phase2b_research.py")
    ok = ("run_live_supervisor" not in runner_src
          and "subprocess" not in runner_src)
    checks["ANALYSIS_NEVER_STOPS_RECORDER"] = "PASS" if ok else "MISSING"

    values = set(checks.values())
    if values == {"PASS"}:
        overall = "MEMORY_HOTFIX_PASS"
    elif values == {"MISSING"}:
        overall = "MEMORY_HOTFIX_MISSING"
    else:
        overall = "MEMORY_HOTFIX_PARTIAL"
    return {
        "engineering_fix_version": ENGINEERING_FIX_VERSION,
        "collector_version_unchanged": "phase2a_prospective_v4",
        "version_bump_required": False,
        "items": checks,
        "overall": overall,
        "note": ("engineering-only fixes (memory allocation, hash "
                 "finalization, health isolation, task supervision); no raw "
                 "event/parser/book-reconstruction/timestamp/coverage "
                 "semantics changed, so no collector version bump"),
    }
