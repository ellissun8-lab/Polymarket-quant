"""Pure helpers for recorder network and coverage-root-cause audits."""
from __future__ import annotations

import statistics
from collections import Counter
from typing import Any, Iterable

from std0_quant.collectors.network_stability import classify_network_error


def connection_error_taxonomy(sessions: Iterable[Any], start_ms: int,
                              end_ms: int) -> list[dict[str, Any]]:
    counts: Counter[tuple[str, str, str, str]] = Counter()
    for session in sessions:
        connected = False
        source = ("BTC" if session.kind == "btc_ticks" else
                  "CLOB" if session.kind == "polymarket_book" else
                  "MARKET_DISCOVERY" if session.kind == "live_collector_network"
                  else session.kind.upper())
        for event in sorted(session.events, key=lambda row: int(row.get("timestamp_ms", 0))):
            timestamp = int(event.get("timestamp_ms", 0))
            if not start_ms <= timestamp <= end_ms:
                continue
            if event.get("event") == "connected":
                connected = True
            elif event.get("event") in {"connection_error", "market_discovery_error"}:
                detail = classify_network_error(str(event.get("error") or ""))
                reason = str(event.get("reason") or detail["reason"])
                exception_class = str(event.get("exception_class") or detail["exception_class"])
                stage = str(event.get("stage") or ("READ" if connected else "CONNECT"))
                counts[(source, stage, reason, exception_class)] += 1
                connected = False
    return [{"source": key[0], "stage": key[1], "reason": key[2],
             "exception_class": key[3], "count": count}
            for key, count in sorted(counts.items())]


def restart_taxonomy(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    starts: list[tuple[int, int]] = []
    restarts: list[dict[str, Any]] = []
    for event in sorted(events, key=lambda row: int(row.get("timestamp_ms", 0))):
        if event.get("event") == "child_started":
            starts.append((int(event["timestamp_ms"]), int(event["pid"])))
        elif event.get("event") == "child_restart":
            timestamp = int(event["timestamp_ms"])
            prior = starts[-1] if starts else (timestamp, -1)
            code = int(event.get("exit_code", -999))
            restarts.append({
                "component": event.get("component", "collect_live"),
                "restart_timestamp_ms": timestamp,
                "pid": prior[1],
                "runtime_since_previous_restart_seconds":
                    float(event.get("child_runtime_seconds",
                                    max(0, timestamp-prior[0])/1000)),
                "exit_code": code,
                "restart_reason": event.get("restart_reason") or
                    ("UNEXPECTED_NORMAL_EXIT" if code == 0 else "CHILD_EXIT_NONZERO"),
            })
    runtimes = [row["runtime_since_previous_restart_seconds"] for row in restarts]
    return {
        "count": len(restarts),
        "rows": restarts,
        "exit_code_counts": dict(Counter(str(row["exit_code"]) for row in restarts)),
        "reason_counts": dict(Counter(row["restart_reason"] for row in restarts)),
        "runtime_seconds": {
            "p10": _percentile(runtimes, .10), "p50": _percentile(runtimes, .50),
            "p90": _percentile(runtimes, .90), "max": max(runtimes, default=None),
            "under_10s": sum(value < 10 for value in runtimes),
        },
    }


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered)-1)*q
    lo, hi = int(index), min(int(index)+1, len(ordered)-1)
    return ordered[lo] + (ordered[hi]-ordered[lo])*(index-lo)


def connection_lifetime_summary(sessions: Iterable[Any], start_ms: int,
                                end_ms: int) -> dict[str, Any]:
    by_source: dict[str, list[float]] = {"BTC": [], "CLOB": []}
    successes = Counter(); failures = Counter(); reconnect_delays: dict[str, list[float]] = {"BTC": [], "CLOB": []}
    for session in sessions:
        source = "BTC" if session.kind == "btc_ticks" else "CLOB" if session.kind == "polymarket_book" else None
        if source is None:
            continue
        opened = None; error_at = None
        for event in sorted(session.events, key=lambda row: int(row.get("timestamp_ms", 0))):
            ts = int(event.get("timestamp_ms", 0))
            if not start_ms <= ts <= end_ms:
                continue
            if event.get("event") == "connected":
                successes[source] += 1
                if error_at is not None:
                    reconnect_delays[source].append((ts-error_at)/1000)
                    error_at = None
                opened = ts
            elif event.get("event") == "connection_error":
                failures[source] += 1
                if opened is not None:
                    by_source[source].append(max(0, ts-opened)/1000); opened = None
                error_at = ts
            elif event.get("event") in {"disconnected", "session_end"} and opened is not None:
                by_source[source].append(max(0, ts-opened)/1000); opened = None
    return {source: {"successful_connections": successes[source],
                     "failed_connections": failures[source],
                     "lifetime_seconds": {"p10": _percentile(values,.1),
                                          "p50": _percentile(values,.5),
                                          "p90": _percentile(values,.9)},
                     "median_reconnect_delay_seconds":
                         statistics.median(reconnect_delays[source]) if reconnect_delays[source] else None}
            for source, values in by_source.items()}


def coverage_exclusion_reasons(row: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if row.get("lifecycle") != "FULL_LIFECYCLE_MARKET":
        if not row.get("collector_ready_before_start"):
            reasons.append("PARTIAL_SESSION_START")
        if not row.get("collector_continued_through_end"):
            reasons.append("SESSION_ENDED_EARLY")
        if not reasons:
            reasons.append("PARTIAL_SESSION_MARKET")
    if float(row.get("btc_coverage_pct") or 0) < .99:
        reasons.append("BTC_COVERAGE_LT_99")
    if float(row.get("book_coverage_pct") or 0) < .99:
        reasons.append("BOOK_COVERAGE_LT_99")
    first_valid = row.get("book_first_valid_receive_ms")
    if first_valid is None or int(first_valid) > int(row["market_start_ms"])+1000:
        reasons.append("NO_VALID_SNAPSHOT_AT_START")
    if int(row.get("network_gap_count") or 0) > 0:
        reasons.append("NETWORK_GAP")
    if row.get("market_discovery_ms") is None or int(row["market_discovery_ms"]) > int(row["market_start_ms"]):
        reasons.append("MARKET_DISCOVERY_LATE")
    if int(row.get("rotation_gap_ms") or 0) > 0:
        reasons.append("ROTATION_GAP")
    if row.get("proxy_outage"):
        reasons.append("PROXY_OUTAGE")
    return list(dict.fromkeys(reasons))
