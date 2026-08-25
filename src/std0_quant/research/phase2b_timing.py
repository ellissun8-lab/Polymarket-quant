"""Phase 2B timing integrity, clock semantics and cross-market replication
primitives (research spec v3).

MEASURE THE CLOCK BEFORE MEASURING THE LAG.  Nothing here interprets a
subsecond lead-lag number before auditing what the timestamps mean:

* a timing semantics registry loaded from ``data/state/timing_semantics_registry.json``
  (every field's class/trust/origin proven from collector code and raw
  payloads, ``UNKNOWN`` when unprovable),
* per-event latency decomposition that keeps FRAME vs EVENT semantics apart
  (``receive - frame_ts`` is a FRAME_DELIVERY_DELAY, never claimed as network
  latency of the underlying order events) and keeps NETWORK/transport delay
  strictly separate from STATE_AGE,
* three clock views per market: VIEW_A source timestamps, VIEW_B local
  receive timestamps, VIEW_C state-availability timestamps,
* timing trust tiers and a frozen minimum-resolvable-lag heuristic with
  ABOVE/NEAR/BELOW_TIMING_RESOLUTION statuses,
* a cross-clock agreement matrix and the v2 +250ms reassessment vocabulary.

Frozen in this spec version (do not retune after seeing results): trust-tier
ratios 4x/2x/1x, resolution thresholds bound and 0.5*bound, the
minimum-resolvable-lag bound max(p99-p50 of BTC, p99-p50 of CLOB
receive-minus-source), coarse lag buckets 0-500/500-1000/1-2s/>2s, the
strict-majority direction replication rule, and all decision vocabularies.

Timing trust tiers are NEVER upgraded for direction consistency.  No trading,
no causal claims, no retroactive clock correction, no filtering of "ugly"
events.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

from std0_quant.audit.prospective import verify_raw_sidecars
from std0_quant.research.phase2b import PRIMARY_COLLECTOR_VERSION
from std0_quant.research.phase2b_stability import (
    classify_direction,
    collect_shock_response_values,
    method_a_peak,
    method_agreement,
    method_b_peak,
    method_c_peak,
    shock_anchor_rows,
)

RESEARCH_SPEC_VERSION_V3 = "phase2b_research_v3"
TIMING_SEMANTICS_VERSION = "phase2b_timing_semantics_v1"
DEFAULT_REGISTRY_PATH = Path("data/state/timing_semantics_registry.json")

TIMESTAMP_CLASSES = ("SOURCE_EVENT_TIME", "SOURCE_FRAME_TIME", "LOCAL_RECEIVE_TIME",
                     "LOCAL_PROCESS_TIME", "RECONSTRUCTED_STATE_TIME", "UNKNOWN")
TIMESTAMP_GRANULARITIES = ("EVENT_LEVEL", "FRAME_LEVEL", "SECOND_LEVEL",
                           "BURST_QUANTIZED", "UNKNOWN")
TRUST_LEVELS = ("HIGH", "MEDIUM", "LOW", "UNKNOWN")
CAN_ORDER_VALUES = ("YES", "LIMITED", "NO")

TRUST_TIERS = ("TIER_A", "TIER_B", "TIER_C", "TIER_D", "UNKNOWN")
RESOLUTION_STATUSES = ("ABOVE_TIMING_RESOLUTION", "NEAR_TIMING_RESOLUTION",
                       "BELOW_TIMING_RESOLUTION", "UNKNOWN")
CLOCK_VIEW_NAMES = ("VIEW_A", "VIEW_B", "VIEW_C")
CLOCK_VIEWS = {"VIEW_A": "source/exchange timestamps",
               "VIEW_B": "local receive timestamps",
               "VIEW_C": "state-availability timestamps"}

ALLOWED_TIMING_DECISIONS = ("TIMING_SEMANTICS_PASS", "TIMING_SEMANTICS_LIMITED",
                            "TIMING_RESOLUTION_INSUFFICIENT", "CLOCK_BASIS_INSTABILITY",
                            "TIMESTAMP_PARSER_FAILURE")
ALLOWED_V2_REASSESSMENTS = ("TIMING_ROBUST", "DIRECTIONALLY_ROBUST_BUT_LAG_UNRESOLVED",
                            "NOT_TIMING_ROBUST", "INSUFFICIENT_DATA")
FORBIDDEN_REASSESSMENT_TOKENS = ("CONFIRMED_250MS", "CONFIRMED_0_2S", "CONFIRMED",
                                 "EXACT_LAG")
# v3.1 interpretation statuses: direction may replicate before magnitude is
# identified; the two are reported separately and never merged
INTERPRETATION_STATUSES = ("DIRECTION_REPLICATED_EARLY", "DIRECTION_NOT_REPLICATED",
                           "LAG_MAGNITUDE_UNRESOLVED", "LAG_MAGNITUDE_RESOLVED")

COARSE_LAG_BUCKETS = ((500, "0-500ms"), (1000, "500-1000ms"), (2000, "1-2s"))

REQUIRED_REGISTRY_KEYS = ("source", "field_name", "semantic_name", "unit", "origin",
                          "trust_level", "can_order_cross_source", "notes",
                          "parser_version")


# ------------------------------------------------------------- registry

def load_timing_registry(path: Path | str = DEFAULT_REGISTRY_PATH) -> dict[str, Any]:
    """Load and validate the timing semantics registry.

    Raises ``ValueError`` when an entry lacks a required key or uses a value
    outside the pre-registered vocabularies; a missing file is an error (the
    registry must exist and be frozen before any market aggregate is viewed).
    """
    path = Path(path)
    if not path.exists():
        raise ValueError(f"timing semantics registry missing: {path}")
    registry = json.loads(path.read_text(encoding="utf-8"))
    if not str(registry.get("timing_semantics_version", "")).startswith("phase2b_timing_semantics_"):
        raise ValueError("registry timing_semantics_version missing/invalid")
    entries = registry.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("registry has no entries")
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        missing = [k for k in REQUIRED_REGISTRY_KEYS if k not in entry]
        if missing:
            raise ValueError(f"registry entry missing keys: {missing}")
        if entry["timestamp_class"] not in TIMESTAMP_CLASSES:
            raise ValueError(f"registry entry bad timestamp_class: {entry['timestamp_class']}")
        if entry["trust_level"] not in TRUST_LEVELS:
            raise ValueError(f"registry entry bad trust_level: {entry['trust_level']}")
        if entry["can_order_cross_source"] not in CAN_ORDER_VALUES:
            raise ValueError(f"registry entry bad can_order_cross_source: {entry['can_order_cross_source']}")
        if entry.get("timestamp_granularity", "UNKNOWN") not in TIMESTAMP_GRANULARITIES:
            raise ValueError(f"registry entry bad timestamp_granularity: {entry.get('timestamp_granularity')}")
        key = (entry["source"], entry["field_name"])
        if key in seen:
            raise ValueError(f"duplicate registry entry: {key}")
        seen.add(key)
    if registry.get("local_clock", {}).get("offset_ms") is None:
        if registry["local_clock"].get("status") != "LOCAL_CLOCK_OFFSET_UNKNOWN":
            raise ValueError("null clock offset must be labeled LOCAL_CLOCK_OFFSET_UNKNOWN")
    return registry


def registry_entry(registry: dict[str, Any], source: str, field_name: str) -> dict[str, Any] | None:
    return next((e for e in registry["entries"]
                 if e["source"] == source and e["field_name"] == field_name), None)


def row_timing_class(source: str, registry: dict[str, Any]) -> dict[str, Any]:
    """Classify a timeline row's source timestamps from the registry."""
    if str(source) == "BTC":
        entry = registry_entry(registry, "binance_btc", "exchange_timestamp_ms")
    else:
        entry = registry_entry(registry, "polymarket_clob", "exchange_timestamp_ms")
    if entry is None:
        return {"timestamp_class": "UNKNOWN", "timestamp_trust": "UNKNOWN",
                "timestamp_granularity": "UNKNOWN"}
    return {"timestamp_class": entry["timestamp_class"],
            "timestamp_trust": entry["trust_level"],
            "timestamp_granularity": entry.get("timestamp_granularity", "UNKNOWN")}


def cross_source_ordering(registry: dict[str, Any]) -> dict[str, Any]:
    """Can BTC and PM timestamps be ordered against each other, per basis?"""
    def basis(a: dict | None, b: dict | None, require_event_level: bool) -> str:
        if not a or not b:
            return "NO"
        if "UNKNOWN" in (a["timestamp_class"], b["timestamp_class"],
                         a["trust_level"], b["trust_level"]):
            return "NO"
        if a["timestamp_class"] != b["timestamp_class"]:
            return "LIMITED"
        if require_event_level and "FRAME_LEVEL" in (a.get("timestamp_granularity"),
                                                     b.get("timestamp_granularity")):
            return "LIMITED"
        return "YES"
    btc_src = registry_entry(registry, "binance_btc", "exchange_timestamp_ms")
    pm_src = registry_entry(registry, "polymarket_clob", "exchange_timestamp_ms")
    btc_rx = registry_entry(registry, "binance_btc", "receive_timestamp_ms")
    pm_rx = registry_entry(registry, "polymarket_clob", "receive_timestamp_ms")
    source_basis = basis(btc_src, pm_src, require_event_level=True)
    receive_basis = basis(btc_rx, pm_rx, require_event_level=False)
    if source_basis == "YES" and receive_basis == "YES":
        overall = "YES"
    elif source_basis == "NO" and receive_basis == "NO":
        overall = "NO"
    else:
        overall = "LIMITED"
    return {"source_time_basis": source_basis, "receive_time_basis": receive_basis,
            "can_compare_btc_pm": overall,
            "note": ("source-time comparison is LIMITED: PM timestamps are per-frame server "
                     "times (FRAME_LEVEL, not event times) and the Binance<->Polymarket server "
                     "clock offset is unknown; receive-time comparison shares one local clock "
                     "but mixes the two transport paths and the unknown local clock offset.")}


def timing_semantics_status(registry: dict[str, Any]) -> str:
    """PASS only when every primary timestamp field is classified, trusted and
    event-level with YES cross-source ordering; anything less is LIMITED."""
    ordering = cross_source_ordering(registry)
    primary = [("binance_btc", "exchange_timestamp_ms"), ("binance_btc", "receive_timestamp_ms"),
               ("polymarket_clob", "exchange_timestamp_ms"), ("polymarket_clob", "receive_timestamp_ms")]
    entries = [registry_entry(registry, s, f) for s, f in primary]
    if any(e is None or e["trust_level"] not in ("HIGH", "MEDIUM")
           or e["timestamp_class"] == "UNKNOWN" for e in entries):
        return "TIMING_SEMANTICS_LIMITED"
    if ordering["can_compare_btc_pm"] != "YES":
        return "TIMING_SEMANTICS_LIMITED"
    return "TIMING_SEMANTICS_PASS"


# ------------------------------------------------- latency interpretation

def interpret_latency(timestamp_class: str) -> dict[str, Any]:
    """Name the quantity receive-minus-source measures for a timestamp class.

    Neither class supports a pure network-latency claim: the local clock offset
    is unknown, and frame timestamps are not event times.
    """
    if timestamp_class == "SOURCE_EVENT_TIME":
        return {"quantity": "EVENT_TRANSPORT_DELAY_PLUS_UNKNOWN_LOCAL_CLOCK_OFFSET",
                "network_latency_claim": False,
                "note": ("receive minus event time mixes true transport with the unknown local "
                         "clock offset; the two are not separable without a measured offset.")}
    if timestamp_class == "SOURCE_FRAME_TIME":
        return {"quantity": "FRAME_DELIVERY_DELAY_FRAME_TIME_BASIS",
                "network_latency_claim": False,
                "note": ("frame timestamps are per-frame server times with unknown batching; "
                         "receive minus frame time must NOT be read as network latency of the "
                         "underlying order/trade events.")}
    return {"quantity": "UNCLASSIFIED_DELAY", "network_latency_claim": False,
            "note": "timestamp class UNKNOWN; no latency interpretation is allowed."}


def robust_latency_stats(values_ms: Sequence[int | float | None]) -> dict[str, Any]:
    """Distribution stats with robust spread measures and threshold fractions."""
    arr = np.asarray([float(v) for v in values_ms if v is not None], dtype=float)
    empty = {"n": 0, "min_ms": None, "p50_ms": None, "p90_ms": None, "p95_ms": None,
             "p99_ms": None, "max_ms": None, "mad_ms": None, "iqr_ms": None,
             "p95_minus_p50_ms": None, "p99_minus_p50_ms": None,
             "frac_gt_250ms": None, "frac_gt_500ms": None,
             "frac_gt_1000ms": None, "frac_gt_5000ms": None}
    if arr.size == 0:
        return empty
    p25, p50, p75, p90, p95, p99 = np.percentile(arr, [25, 50, 75, 90, 95, 99])
    return {"n": int(arr.size), "min_ms": float(arr.min()), "p50_ms": float(p50),
            "p90_ms": float(p90), "p95_ms": float(p95), "p99_ms": float(p99),
            "max_ms": float(arr.max()),
            "mad_ms": float(np.median(np.abs(arr - p50))),
            "iqr_ms": float(p75 - p25),
            "p95_minus_p50_ms": float(p95 - p50), "p99_minus_p50_ms": float(p99 - p50),
            "frac_gt_250ms": float((arr > 250).mean()),
            "frac_gt_500ms": float((arr > 500).mean()),
            "frac_gt_1000ms": float((arr > 1000).mean()),
            "frac_gt_5000ms": float((arr > 5000).mean())}


def systematic_offset_assessment(stats: dict[str, Any]) -> dict[str, Any]:
    """A large p50 with tight spread is consistent with a constant offset
    (clock skew or buffered transport) rather than variable network latency.
    Reported as a hypothesis only -- the offset is unknown."""
    p50, spread, n = stats.get("p50_ms"), stats.get("p99_minus_p50_ms"), stats.get("n") or 0
    if p50 is None or spread is None or n == 0:
        return {"pattern": "UNKNOWN"}
    if p50 > 500 and spread < 0.5 * p50:
        return {"pattern": "CONSTANT_OFFSET_DOMINANT", "p50_ms": float(p50),
                "p99_minus_p50_ms": float(spread),
                "hypothesis": ("systematic local clock offset or buffered transport, "
                               "NOT variable network latency"),
                "conclusion": "NONE - local clock offset unknown, no decomposition possible"}
    return {"pattern": "VARIABLE_DELAY_DOMINANT", "p50_ms": float(p50),
            "p99_minus_p50_ms": float(spread),
            "hypothesis": "transport/queue delay varies on the same order as its level"}


# ----------------------------------------------------- latency decomposition

DECOMPOSITION_COLUMNS = ("source", "session_id", "connection_id", "market",
                         "source_event_ts", "frame_ts", "receive_ts", "process_ts",
                         "receive_minus_source_ms", "process_minus_receive_ms",
                         "state_age_ms", "timestamp_class", "timestamp_trust",
                         "event_type", "is_frame_child")


def latency_decomposition(frame: pd.DataFrame, condition_id: str,
                           registry: dict[str, Any]) -> pd.DataFrame:
    """Per-event timing rows for ``timing_diagnostics_<run_id>.parquet``.

    BTC rows carry a true source event time (T); PM rows carry NO event-level
    source time -- only the parent frame timestamp -- so their
    ``receive_minus_source_ms`` is a frame-basis delivery delay, never event
    network latency (see ``interpret_latency``).  ``process_ts`` is null: the
    collector never recorded a separate LOCAL_PROCESS_TIME.  ``state_age_ms``
    is the age of the state information at availability (PM only).
    """
    btc_class = row_timing_class("BTC", registry)
    pm_class = row_timing_class("PM", registry)
    n = len(frame)
    out = pd.DataFrame({
        "source": frame["source"].astype(str).values,
        "session_id": frame["session_id"].astype(str).values if "session_id" in frame else [None] * n,
        "connection_id": frame["connection_id"].astype(str).values if "connection_id" in frame else [None] * n,
        "market": [str(condition_id)] * n,
        "source_event_ts": (frame["source_event_ts_ms"].astype("float64").values
                            if "source_event_ts_ms" in frame else np.full(n, np.nan)),
        "frame_ts": (frame["frame_ts_ms"].astype("float64").values
                     if "frame_ts_ms" in frame else np.full(n, np.nan)),
        "receive_ts": frame["receive_timestamp_ms"].astype("int64").values,
        "process_ts": np.full(n, np.nan),
        "event_type": (frame["event_type"].astype(str).values if "event_type" in frame else [None] * n),
        "is_frame_child": (frame["is_frame_child"].astype(bool).values
                           if "is_frame_child" in frame else np.zeros(n, dtype=bool)),
    })
    basis_ts = out["source_event_ts"].fillna(out["frame_ts"])
    out["receive_minus_source_ms"] = out["receive_ts"] - basis_ts
    out["process_minus_receive_ms"] = np.full(n, np.nan)
    out["state_age_ms"] = np.where(out["source"].values == "PM",
                                   out["receive_ts"] - out["frame_ts"], np.nan)
    is_btc = out["source"].values == "BTC"
    out["timestamp_class"] = np.where(is_btc, btc_class["timestamp_class"], pm_class["timestamp_class"])
    out["timestamp_trust"] = np.where(is_btc, btc_class["timestamp_trust"], pm_class["timestamp_trust"])
    out["timestamp_granularity"] = np.where(is_btc, btc_class["timestamp_granularity"], pm_class["timestamp_granularity"])
    return out[list(DECOMPOSITION_COLUMNS) + ["timestamp_granularity"]]


def latency_explain_by(decomposition: pd.DataFrame, by: Sequence[str]) -> list[dict[str, Any]]:
    """Robust latency stats grouped by arbitrary dimensions (event type,
    market, session, connection) -- the 'explain the drift' breakdown."""
    rows: list[dict[str, Any]] = []
    if decomposition is None or not len(decomposition):
        return rows
    for key, group in decomposition.groupby(list(by), observed=True, dropna=False):
        key_values = key if isinstance(key, tuple) else (key,)
        dims = {name: (None if pd.isna(v) else v) for name, v in zip(by, key_values)}
        for source, sub in group.groupby("source", observed=True):
            rows.append({**dims, "source": source,
                         **robust_latency_stats(sub["receive_minus_source_ms"].tolist())})
    return sorted(rows, key=lambda r: (str(r.get("source")), str(tuple(r.get(k) for k in by))))


# ------------------------------------------------------- local clock / integrity

def local_clock_health(health_payload: dict[str, Any] | None) -> dict[str, Any]:
    offset = (health_payload or {}).get("estimated_clock_offset_ms")
    return {"local_clock_offset_ms": offset,
            "status": "LOCAL_CLOCK_OFFSET_UNKNOWN" if offset is None else "LOCAL_CLOCK_OFFSET_RECORDED",
            "correction_applied": False,
            "retroactive_correction": "FORBIDDEN",
            "note": ("offset never measured => receive-minus-source mixes transport with "
                     "unknown clock offset; no retroactive correction is applied to any "
                     "raw or derived timestamp.")}


def raw_input_integrity(input_files: Sequence[Path | str],
                        active_files: Iterable[Path | str] = ()) -> dict[str, Any]:
    """Separate ACTIVE_FILE_NO_SIDECAR (an exclusion) from SHA failure.

    Only closed, SHA-verified files are formal analysis inputs; active
    (never-closed, sidecar-less) files are excluded with a reason and are NOT
    counted as integrity failures.
    """
    active = {str(p) for p in active_files}
    inputs = [p for p in input_files if str(p) not in active]
    integrity = verify_raw_sidecars(inputs)
    status = ("RAW_INTEGRITY_FAILURE"
              if integrity["sha256_failures"] or integrity["sidecar_missing"] or integrity["parse_errors"]
              else "PASS")
    return {"status": status, "n_input_files": len(list(input_files)),
            "n_formal_inputs": len(inputs),
            "active_excluded_files": sorted(str(p) for p in input_files if str(p) in active),
            "active_exclusion_reason": "ACTIVE_FILE_NO_SIDECAR",
            "closed_missing_sidecar_files": integrity["sidecar_missing"],
            "closed_sha_failure_files": integrity["sha256_failures"],
            "parse_errors": integrity["parse_errors"],
            "note": ("ACTIVE_FILE_NO_SIDECAR is an exclusion, not a SHA failure; only closed "
                     "SHA-verified files are formal inputs.")}


# ------------------------------------------------ resolution / trust tiers

def minimum_resolvable_lag_ms(btc_stats: dict[str, Any], clob_stats: dict[str, Any]) -> float | None:
    """Frozen heuristic bound: max(p99-p50 of BTC, p99-p50 of CLOB)
    receive-minus-source delay.  A lag smaller than this bound cannot be
    distinguished from timing noise."""
    parts = [float(s["p99_minus_p50_ms"]) for s in (btc_stats, clob_stats)
             if s.get("p99_minus_p50_ms") is not None]
    return max(parts) if parts else None


def resolution_status(lag_ms: int | float | None, bound_ms: float | None) -> str:
    if lag_ms is None or bound_ms is None or bound_ms < 0:
        return "UNKNOWN"
    lag = abs(float(lag_ms))
    if bound_ms == 0:
        return "ABOVE_TIMING_RESOLUTION"
    if lag >= bound_ms:
        return "ABOVE_TIMING_RESOLUTION"
    if lag >= 0.5 * bound_ms:
        return "NEAR_TIMING_RESOLUTION"
    return "BELOW_TIMING_RESOLUTION"


def timing_trust_tier(lag_ms: int | float | None, ambiguity_ms: float | None) -> str:
    """TIER ratios are frozen; direction consistency NEVER upgrades a tier
    (the function deliberately takes no direction argument)."""
    if lag_ms is None or ambiguity_ms is None or ambiguity_ms < 0:
        return "UNKNOWN"
    if ambiguity_ms == 0:
        return "TIER_A"
    ratio = abs(float(lag_ms)) / float(ambiguity_ms)
    if ratio >= 4:
        return "TIER_A"
    if ratio >= 2:
        return "TIER_B"
    if ratio >= 1:
        return "TIER_C"
    return "TIER_D"


def coarse_lag_bucket(lag_ms: int | float | None) -> str | None:
    if lag_ms is None:
        return None
    value = abs(float(lag_ms))
    for hi, label in COARSE_LAG_BUCKETS:
        if value < hi:
            return label
    return ">2s"


# --------------------------------------------------------- clock views

def view_method_estimates(grid_primary: pd.DataFrame, grid_fine: pd.DataFrame) -> dict[str, Any]:
    """METHOD_A/B/C estimates on one clock view's grids."""
    a = method_a_peak(grid_primary)
    anchors = shock_anchor_rows(grid_primary)
    values = collect_shock_response_values(anchors, grid_fine)
    b = method_b_peak(values)
    c = method_c_peak(grid_primary)
    directions = [classify_direction(a["lag_ms"]), classify_direction(b["lag_ms"]),
                  classify_direction(c["lag_ms"])]
    agreement = method_agreement(directions)
    return {"method_a_lag_ms": a["lag_ms"], "method_a_correlation": a["correlation"],
            "method_b_lag_ms": b["lag_ms"], "method_b_signed_mean": b["signed_mean"],
            "method_b_n": b["n"], "method_c_lag_ms": c["lag_ms"],
            "method_c_horizon_ms": c["horizon_ms"], "method_c_r2": c["r2"],
            "direction": directions[0], "direction_method_b": directions[1],
            "direction_method_c": directions[2],
            "method_agreement": agreement["agreement"]}


def availability_state_age(frame: pd.DataFrame, start_ms: int, end_ms: int,
                           grid_ms: int = 250, book_stale_ms: int = 5000) -> pd.DataFrame:
    """VIEW_C construction: grid buckets stamped by state availability.

    ``pm_state_availability_ts`` is the receive timestamp of the event whose
    state value the bucket carries (for this collector availability == the
    constructing frame's receive time; no LOCAL_PROCESS_TIME was recorded).
    Reports both ``pm_state_availability_age_ms`` (bucket minus availability,
    the carry-forward gap) and ``pm_state_info_age_ms`` (bucket minus the
    state's source frame timestamp -- how old the information is).  A bucket
    whose information is older than ``book_stale_ms`` is marked invalid even
    though it is available: VALID is not FRESH.
    """
    grid = pd.DataFrame({"timestamp_ms": np.arange(int(start_ms), int(end_ms), int(grid_ms), dtype="int64")})
    pm = frame[frame["source"] == "PM"] if len(frame) else frame
    if not len(pm):
        grid["pm_state_availability_ts"] = np.nan
        grid["pm_state_source_ts"] = np.nan
        grid["pm_mid"] = np.nan
    else:
        left = pm[["receive_timestamp_ms", "exchange_timestamp_ms", "pm_mid"]] \
            .sort_values("receive_timestamp_ms") \
            .rename(columns={"receive_timestamp_ms": "event_timestamp_ms"})
        grid = pd.merge_asof(grid, left, left_on="timestamp_ms",
                             right_on="event_timestamp_ms", direction="backward")
        grid = grid.rename(columns={"event_timestamp_ms": "pm_state_availability_ts",
                                    "exchange_timestamp_ms": "pm_state_source_ts"})
    grid["pm_state_availability_age_ms"] = grid["timestamp_ms"] - grid["pm_state_availability_ts"]
    grid["pm_state_info_age_ms"] = grid["timestamp_ms"] - grid["pm_state_source_ts"]
    invalid = grid["pm_state_info_age_ms"].isna() | (grid["pm_state_info_age_ms"] > book_stale_ms)
    grid.loc[invalid, "pm_mid"] = np.nan
    grid["book_valid"] = ~invalid
    grid["grid_ms"] = grid_ms
    return grid


def validate_no_backdating(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """HARD RULE: state availability can never precede the local receive time
    of its construction inputs, and a state can never be used before it is
    available.  Rows may carry ``availability_ts``, ``receive_ts`` (of the
    constructing event) and ``bucket_ts``."""
    violations: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        availability = row.get("pm_state_availability_ts", row.get("availability_ts"))
        receive = row.get("receive_ts") or row.get("receive_timestamp_ms")
        bucket = row.get("bucket_ts") or row.get("timestamp_ms")
        if availability is not None and receive is not None and int(availability) < int(receive):
            violations.append({"row": index, "kind": "AVAILABILITY_BEFORE_RECEIVE",
                               "availability_ts": int(availability), "receive_ts": int(receive)})
        if availability is not None and bucket is not None and int(availability) > int(bucket):
            violations.append({"row": index, "kind": "STATE_USED_BEFORE_AVAILABLE",
                               "availability_ts": int(availability), "bucket_ts": int(bucket)})
    return {"status": "BACKDATING_DETECTED" if violations else "PASS",
            "n_violations": len(violations), "violations": violations[:50],
            "rule": "state_availability_ts >= receive_ts of every construction input; "
                    "state used only at/after availability"}


def agreement_matrix(view_rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Pairwise direction agreement of the three clock views per market."""
    pairs = (("VIEW_A", "VIEW_B"), ("VIEW_A", "VIEW_C"), ("VIEW_B", "VIEW_C"))
    pairwise: dict[str, dict[str, int]] = {}
    any_disagree = False
    for a, b in pairs:
        agree = disagree = 0
        for row in view_rows:
            da, db = row.get(a), row.get(b)
            if da is None or db is None:
                continue
            if da == db:
                agree += 1
            else:
                disagree += 1
                any_disagree = True
        pairwise[f"{a}_vs_{b}"] = {"agree": agree, "disagree": disagree}
    complete = [r for r in view_rows
                if all(r.get(v) is not None for v in CLOCK_VIEW_NAMES)]
    all_three = sum(1 for r in complete
                    if len({r[v] for v in CLOCK_VIEW_NAMES}) == 1)
    if any_disagree:
        status = "CLOCK_BASIS_INSTABILITY"
    elif view_rows:
        status = "CLOCK_BASIS_STABLE"
    else:
        status = "UNKNOWN"
    return {"n_markets": len(view_rows), "pairwise": pairwise,
            "all_three_agree": int(all_three), "status": status,
            "rule": "any pairwise direction disagreement on any market => CLOCK_BASIS_INSTABILITY"}


# ------------------------------------------------------- decisions

def timing_decision(semantics_status: str, parser_failure: bool = False,
                    clock_instability: bool = False, n_with_estimate: int = 0,
                    n_not_above_resolution: int = 0) -> list[str]:
    """Composable pre-registered timing vocabulary; never presupposes PASS."""
    decisions: list[str] = []
    if parser_failure:
        decisions.append("TIMESTAMP_PARSER_FAILURE")
    decisions.append("TIMING_SEMANTICS_PASS" if semantics_status == "TIMING_SEMANTICS_PASS"
                     else "TIMING_SEMANTICS_LIMITED")
    if clock_instability:
        decisions.append("CLOCK_BASIS_INSTABILITY")
    if n_with_estimate and n_not_above_resolution * 2 >= n_with_estimate:
        decisions.append("TIMING_RESOLUTION_INSUFFICIENT")
    return decisions


def v2_reassessment(n_markets: int, n_with_estimate: int, btc_lead_count: int,
                    n_resolved: int) -> str:
    """Re-evaluate the v2 +250ms observation under strict timing semantics.

    ``btc_lead_count`` counts markets whose primary (VIEW_A METHOD_A)
    direction is BTC_LEAD; ``n_resolved`` counts markets whose primary lag is
    ABOVE_TIMING_RESOLUTION.  Direction must replicate by strict majority and
    the lag must be resolvable for TIMING_ROBUST; a replicated direction with
    an unresolvable lag is DIRECTIONALLY_ROBUST_BUT_LAG_UNRESOLVED.
    """
    if n_markets < 2 or n_with_estimate <= 0:
        return "INSUFFICIENT_DATA"
    replicated = btc_lead_count * 2 > n_with_estimate
    if not replicated:
        return "NOT_TIMING_ROBUST"
    if n_resolved >= n_with_estimate:
        return "TIMING_ROBUST"
    return "DIRECTIONALLY_ROBUST_BUT_LAG_UNRESOLVED"


def assert_allowed_timing_decision(decisions: Sequence[str]) -> None:
    for token in decisions:
        if token not in ALLOWED_TIMING_DECISIONS:
            raise ValueError(f"timing decision outside pre-registered vocabulary: {token}")


def assert_allowed_v2_reassessment(outcome: str) -> None:
    if outcome not in ALLOWED_V2_REASSESSMENTS:
        raise ValueError(f"v2 reassessment outcome outside vocabulary: {outcome}")
    for token in FORBIDDEN_REASSESSMENT_TOKENS:
        if token in outcome:
            raise ValueError(f"forbidden v2 reassessment token: {token}")


# ------------------------------------------------------------ cache keys

def market_cache_key_v3(file_shas: Sequence[tuple[str, str]],
                        collector_version: str = PRIMARY_COLLECTOR_VERSION) -> str:
    """v3 cache key: raw SHAs + collector version + research spec + timing
    semantics version (v2 and v3 caches can never collide)."""
    payload = "\n".join(f"{path}|{sha}" for path, sha in sorted(file_shas))
    payload = f"{payload}\n{collector_version}|{RESEARCH_SPEC_VERSION_V3}|{TIMING_SEMANTICS_VERSION}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ------------------------------------------- interpretation hierarchy (v3.1)

def timing_hierarchy(direction: str | None, peak_lag_ms: int | float | None,
                     resolution_ms: int | float | None) -> dict[str, Any]:
    """Strict three-level interpretation of a lead-lag estimate.

    Level 1 is the direction; Level 2 the coarse lag magnitude, interpretable
    ONLY when |peak| >= resolution (otherwise UNRESOLVED); Level 3 the exact
    grid peak, always retained as a descriptive statistic and only allowed as
    a timing conclusion when the resolution supports it.
    """
    peak = None if peak_lag_ms is None else abs(float(peak_lag_ms))
    resolved = (peak is not None and resolution_ms is not None
                and peak >= float(resolution_ms))
    return {
        "level_1_direction": direction,
        "level_2_lag_magnitude": coarse_lag_bucket(peak_lag_ms) if resolved else "UNRESOLVED",
        "lag_magnitude_status": "LAG_MAGNITUDE_RESOLVED" if resolved else "LAG_MAGNITUDE_UNRESOLVED",
        "level_3_peak_ms": peak_lag_ms,
        "level_3_role": "TIMING_CONCLUSION_ALLOWED" if resolved else "DESCRIPTIVE_ONLY",
    }


# magnitude-claim patterns that may NOT appear in a supported conclusion
# while the peak is below the timing-resolution bound
FORBIDDEN_MAGNITUDE_PATTERNS = (
    r"leads?\s+by\s+[\d.]+",             # "leads by 250ms"
    r"within\s+(?:roughly\s+)?[\d.]+",   # "within 500ms" / "within roughly 0-2s"
    r"\b0\s*-\s*2\s*s\b",                # "0-2s"
    r"[\d.]+\s*ms\b",                    # any concrete millisecond magnitude
)


class InterpretationOverreach(ValueError):
    """A supported conclusion claims a lag magnitude that the timing
    resolution cannot support (v3.1 hard interpretation rule)."""


def interpretation_guard(conclusion: str, peak_lag_ms: int | float | None,
                         resolution_ms: int | float | None) -> dict[str, Any]:
    """If |peak| < resolution, the supported-conclusion text must not contain
    a lag magnitude claim; the magnitude must be reported as UNRESOLVED and
    exact numeric peaks confined to descriptive_metrics."""
    import re
    peak = None if peak_lag_ms is None else abs(float(peak_lag_ms))
    unresolved = (peak is None or resolution_ms is None
                  or peak < float(resolution_ms))
    if unresolved:
        for pattern in FORBIDDEN_MAGNITUDE_PATTERNS:
            if re.search(pattern, str(conclusion), flags=re.IGNORECASE):
                raise InterpretationOverreach(
                    f"unsupported lag magnitude in supported conclusion "
                    f"(peak {peak} ms < resolution {resolution_ms} ms): "
                    f"{conclusion!r}")
    return {"lag_magnitude_status":
            "LAG_MAGNITUDE_UNRESOLVED" if unresolved else "LAG_MAGNITUDE_RESOLVED"}


def market_bootstrap_fractions(rows: Sequence[dict[str, Any]],
                               seed: int = 20260824,
                               n_resamples: int = 2000,
                               min_markets: int = 10) -> dict[str, Any]:
    """Exploratory market-level bootstrap for the M10 question.

    Bootstrap unit is the MARKET (never the shock): markets are resampled
    with replacement, giving percentile CIs for the raw BTC_LEAD fraction,
    the median peak (descriptive) lag and the median METHOD_B response sign.
    A lag CI can never upgrade a timing conclusion: when the observed median
    |lag| is below the median per-market timing bound, the lag magnitude
    status stays LAG_MAGNITUDE_UNRESOLVED regardless of the CI.
    """
    rng = np.random.default_rng(seed)
    estimates = [r for r in rows if r.get("method_a_lag_ms") is not None]
    n = len(estimates)
    if n < min_markets:
        return {"status": "NOT_COMPUTED_N_BELOW_10", "n_markets": n,
                "seed": seed, "bootstrap_unit": "MARKET"}
    leads = np.array([bool(r.get("raw_btc_lead")) or r.get("direction") == "BTC_LEAD"
                      for r in estimates])
    lags = np.array([float(r["method_a_lag_ms"]) for r in estimates])
    signs = np.array([np.nan if r.get("method_b_signed_mean") is None
                      else float(np.sign(r["method_b_signed_mean"]))
                      for r in estimates])
    frac, med_lag, med_sign = [], [], []
    for _ in range(n_resamples):
        idx = rng.integers(0, n, size=n)  # resample MARKETS, not shocks
        frac.append(leads[idx].mean())
        med_lag.append(float(np.median(lags[idx])))
        s = signs[idx]
        s = s[~np.isnan(s)]
        med_sign.append(float(np.median(s)) if len(s) else np.nan)

    def ci(values: Sequence[float]) -> dict[str, float]:
        arr = np.asarray(values, dtype=float)
        arr = arr[~np.isnan(arr)]
        return {"p2_5": float(np.percentile(arr, 2.5)),
                "p97_5": float(np.percentile(arr, 97.5))}

    bounds = [r.get("timing_ambiguity_ms") for r in estimates
              if r.get("timing_ambiguity_ms") is not None]
    median_bound = float(np.median(bounds)) if bounds else None
    observed_med_lag = float(np.median(lags))
    lag_resolved = (median_bound is not None
                    and abs(observed_med_lag) >= median_bound)
    return {"status": "COMPUTED", "bootstrap_unit": "MARKET", "seed": seed,
            "n_markets": n, "n_resamples": n_resamples,
            "btc_lead_fraction": float(leads.mean()),
            "btc_lead_fraction_ci": ci(frac),
            "median_peak_lag_ms": observed_med_lag,
            "median_peak_lag_ms_ci": ci(med_lag),
            "median_response_sign": (None if np.all(np.isnan(signs))
                                     else float(np.nanmedian(signs))),
            "median_response_sign_ci": ci(med_sign),
            "median_timing_bound_ms": median_bound,
            "lag_magnitude_status": ("LAG_MAGNITUDE_RESOLVED" if lag_resolved
                                     else "LAG_MAGNITUDE_UNRESOLVED"),
            "note": ("bootstrap CIs are exploratory; a lag CI narrower than "
                     "the timing bound never upgrades a timing conclusion")}
