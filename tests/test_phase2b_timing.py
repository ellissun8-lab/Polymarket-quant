"""Phase 2B-Research v3 timing integrity tests (PT1-PT18 + fixtures A-E).

CORE PRINCIPLE: DO NOT INTERPRET SUBSECOND LEAD-LAG UNTIL TIMING SEMANTICS
ARE AUDITED.  These tests pin the timing semantics: frame vs event
timestamps, NETWORK_LATENCY vs STATE_AGE separation, no-backdating, trust
tiers, resolution statuses, clock-view agreement, cache version separation,
determinism, and the frozen Phase 2A / v2 definitions.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from std0_quant.research import phase2b as p2b
from std0_quant.research import phase2b_stability as p2s
from std0_quant.research import phase2b_timing as pt
from std0_quant.research.phase2b import add_market_features, build_grid
from std0_quant.research.phase2b_stability import (
    RESEARCH_SPEC_VERSION_V2,
    b2_milestone_payload,
    cache_reusable,
    file_sha256,
    frozen_invariant_check,
    markout_horizon_timestamp,
    market_cache_key,
)

ROOT = Path(__file__).resolve().parents[1]


def load_registry() -> dict:
    return pt.load_timing_registry(ROOT / "data" / "state" / "timing_semantics_registry.json")


# --------------------------------------------------------------- helpers

def synthetic_market(lag_ms=500, n=1200, grid_ms=100, seed=11,
                     btc_delay_ms=2, pm_delay_ms=3, ret_scale=5e-4):
    """BTC random walk; PM mid follows BTC by exactly ``lag_ms`` (BTC leads)."""
    rng = np.random.default_rng(seed)
    t0 = 1_787_590_800_000
    ts = t0 + np.arange(n) * grid_ms
    rets = rng.normal(0, ret_scale, n)
    btc = 50_000.0 * np.cumprod(1 + rets)
    steps = lag_ms // grid_ms
    pm = btc.copy()
    if steps > 0:
        pm[steps:] = btc[:-steps]
        pm[:steps] = btc[0]
    return {"ts": ts.astype("int64"), "btc": btc, "pm_mid": pm / 100_000.0,
            "btc_receive": (ts + btc_delay_ms).astype("int64"),
            "pm_receive": (ts + pm_delay_ms).astype("int64"),
            "t0": t0, "end": t0 + n * grid_ms}


def btc_basis(data, basis="exchange"):
    col = data["ts"] if basis == "exchange" else data["btc_receive"]
    return pd.DataFrame({"event_timestamp_ms": col, "btc_price": data["btc"]})


def book_basis(data, basis="exchange"):
    col = data["ts"] if basis == "exchange" else data["pm_receive"]
    mid = data["pm_mid"]
    return pd.DataFrame({"event_timestamp_ms": col, "pm_mid": mid,
                         "pm_best_bid": mid - 0.001, "pm_best_ask": mid + 0.001,
                         "pm_spread": 0.002, "pm_bid_depth_top3": 100.0,
                         "pm_ask_depth_top3": 100.0, "pm_obi_top3": 0.0})


def make_grid(data, basis="exchange", grid_ms=250):
    grid = build_grid(btc_basis(data, basis), book_basis(data, basis),
                      data["t0"], data["end"], grid_ms)
    return add_market_features(grid, data["t0"], data["end"])


def timeline_frame(data):
    n = len(data["ts"])
    btc_rows = pd.DataFrame({
        "source": "BTC", "exchange_timestamp_ms": data["ts"],
        "receive_timestamp_ms": data["btc_receive"], "event_type": "trade",
        "session_id": "s1", "connection_id": "c1",
        "source_event_ts_ms": data["ts"].astype("float64"),
        "frame_ts_ms": data["ts"].astype("float64"),
        "is_frame_child": False, "btc_price": data["btc"], "pm_mid": np.nan})
    pm_rows = pd.DataFrame({
        "source": "PM", "exchange_timestamp_ms": data["ts"],
        "receive_timestamp_ms": data["pm_receive"], "event_type": "price_change",
        "session_id": "s1", "connection_id": "c1",
        "source_event_ts_ms": np.nan,
        "frame_ts_ms": data["ts"].astype("float64"),
        "is_frame_child": False, "btc_price": np.nan, "pm_mid": data["pm_mid"]})
    return pd.concat([btc_rows, pm_rows], ignore_index=True)


def pm_row(source_ts, receive_ts, mid=0.5, event_type="price_change", child=False):
    return {"source": "PM", "exchange_timestamp_ms": int(source_ts),
            "receive_timestamp_ms": int(receive_ts), "event_type": event_type,
            "session_id": "s1", "connection_id": "c1",
            "source_event_ts_ms": np.nan, "frame_ts_ms": float(source_ts),
            "is_frame_child": child, "btc_price": np.nan, "pm_mid": mid}


# ------------------------------------------------------------- registry

def test_registry_loads_and_classifies():
    registry = load_registry()
    btc = pt.row_timing_class("BTC", registry)
    assert btc == {"timestamp_class": "SOURCE_EVENT_TIME",
                   "timestamp_trust": "HIGH",
                   "timestamp_granularity": "EVENT_LEVEL"}
    pm = pt.row_timing_class("PM", registry)
    assert pm["timestamp_class"] == "SOURCE_FRAME_TIME"
    assert pm["timestamp_granularity"] == "FRAME_LEVEL"
    ordering = pt.cross_source_ordering(registry)
    assert ordering["source_time_basis"] == "LIMITED"
    assert ordering["receive_time_basis"] == "YES"
    assert ordering["can_compare_btc_pm"] == "LIMITED"
    assert pt.timing_semantics_status(registry) == "TIMING_SEMANTICS_LIMITED"
    health = pt.local_clock_health({"estimated_clock_offset_ms": None})
    assert health["status"] == "LOCAL_CLOCK_OFFSET_UNKNOWN"
    assert health["correction_applied"] is False


def test_registry_validation_rejects_bad_entries(tmp_path):
    registry = load_registry()
    bad = json.loads(json.dumps(registry)); bad["entries"][0].pop("trust_level")
    path = tmp_path / "bad.json"; path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError):
        pt.load_timing_registry(path)
    bad2 = json.loads(json.dumps(registry))
    bad2["entries"][0]["timestamp_class"] = "TOTALLY_NEW_CLASS"
    path2 = tmp_path / "bad2.json"; path2.write_text(json.dumps(bad2), encoding="utf-8")
    with pytest.raises(ValueError):
        pt.load_timing_registry(path2)
    with pytest.raises(ValueError):
        pt.load_timing_registry(tmp_path / "missing.json")


# ------------------------------------------------------- PT1 / PT2 frames

def _batched_frame_children():
    receive = 1_787_590_800_123
    frame_ts = receive - 300
    return pd.DataFrame([pm_row(frame_ts, receive, 0.50, child=True),
                         pm_row(frame_ts, receive, 0.51, child=True)])


def test_pt1_parent_frame_classification():
    registry = load_registry()
    pm_entry = pt.registry_entry(registry, "polymarket_clob", "exchange_timestamp_ms")
    assert pm_entry["timestamp_class"] == "SOURCE_FRAME_TIME"
    assert pm_entry["timestamp_granularity"] == "FRAME_LEVEL"
    decomp = pt.latency_decomposition(_batched_frame_children(), "0x1", registry)
    assert (decomp["timestamp_class"] == "SOURCE_FRAME_TIME").all()
    assert (decomp["timestamp_granularity"] == "FRAME_LEVEL").all()
    assert decomp["receive_ts"].nunique() == 1   # children share the parent frame receive
    assert decomp["frame_ts"].nunique() == 1     # ...and the parent frame timestamp
    assert decomp["is_frame_child"].all()


def test_pt2_children_cannot_invent_event_times():
    registry = load_registry()
    decomp = pt.latency_decomposition(_batched_frame_children(), "0x1", registry)
    assert decomp["source_event_ts"].isna().all()  # no event-level source time exists
    assert (decomp["timestamp_class"] == "SOURCE_FRAME_TIME").all()  # never upgraded


# ------------------------------------------------ PT3 latency vs state age

def test_pt3_network_latency_separate_from_state_age():
    registry = load_registry()
    interp = pt.interpret_latency("SOURCE_FRAME_TIME")
    assert interp["network_latency_claim"] is False
    assert interp["quantity"] == "FRAME_DELIVERY_DELAY_FRAME_TIME_BASIS"
    assert pt.interpret_latency("SOURCE_EVENT_TIME")["quantity"] == \
        "EVENT_TRANSPORT_DELAY_PLUS_UNKNOWN_LOCAL_CLOCK_OFFSET"
    assert pt.interpret_latency("SOURCE_EVENT_TIME") != interp  # FRAME vs EVENT never conflated
    # events are not states: only PM rows carry a state age
    data = synthetic_market(lag_ms=500, seed=8)
    decomp = pt.latency_decomposition(timeline_frame(data), "0x2", registry)
    assert decomp[decomp.source == "BTC"]["state_age_ms"].isna().all()
    # delivery delay was 5ms, but the state's information grows old: 4s at the later bucket
    t0 = 1_787_590_800_000
    frame = pd.DataFrame([pm_row(t0, t0 + 5, 0.5)])
    grid = pt.availability_state_age(frame, t0, t0 + 6000, grid_ms=250)
    late = grid[grid.timestamp_ms == t0 + 4000].iloc[0]
    assert int(late["pm_state_availability_age_ms"]) == 3995   # delivery/carry-forward gap
    assert int(late["pm_state_info_age_ms"]) == 4000           # information age


# ---------------------------------------------------- PT4 / PT7 backdating

def test_pt4_availability_never_precedes_receive():
    clean = [{"pm_state_availability_ts": 1005, "receive_ts": 1005, "bucket_ts": 2000}]
    assert pt.validate_no_backdating(clean)["status"] == "PASS"
    backdated = [{"pm_state_availability_ts": 1000, "receive_ts": 1005, "bucket_ts": 2000}]
    result = pt.validate_no_backdating(backdated)
    assert result["status"] == "BACKDATING_DETECTED"
    assert result["violations"][0]["kind"] == "AVAILABILITY_BEFORE_RECEIVE"
    early = [{"pm_state_availability_ts": 2500, "receive_ts": 2500, "bucket_ts": 2000}]
    assert pt.validate_no_backdating(early)["violations"][0]["kind"] == "STATE_USED_BEFORE_AVAILABLE"


def test_pt7_backdating_regression_source_stamp_detected():
    # the bug under test: stamping PM state availability at the frame's SOURCE time
    rows = [{"pm_state_availability_ts": 1000, "receive_ts": 2000, "bucket_ts": 3000}]
    assert pt.validate_no_backdating(rows)["status"] == "BACKDATING_DETECTED"
    # the real VIEW_C construction passes validation by construction
    t0 = 1_787_590_800_000
    frame = pd.DataFrame([pm_row(t0, t0 + 5, 0.5)])
    grid = pt.availability_state_age(frame, t0, t0 + 6000, grid_ms=250)
    rows = [{"pm_state_availability_ts": int(r.pm_state_availability_ts),
             "receive_ts": int(r.pm_state_availability_ts),
             "bucket_ts": int(r.timestamp_ms)}
            for r in grid.itertuples() if pd.notna(r.pm_state_availability_ts)]
    assert pt.validate_no_backdating(rows)["status"] == "PASS"


# ------------------------------------------------------------- clock views

def test_pt5_source_vs_receive_views():
    data = synthetic_market(lag_ms=250, seed=9, btc_delay_ms=1000, pm_delay_ms=0)
    view_a = pt.view_method_estimates(make_grid(data, "exchange"), make_grid(data, "exchange", 100))
    view_b = pt.view_method_estimates(make_grid(data, "receive"), make_grid(data, "receive", 100))
    assert view_a["method_a_lag_ms"] != view_b["method_a_lag_ms"]
    assert view_a["direction"] == "BTC_LEAD"
    assert view_b["direction"] == "PM_LEAD"


def test_pt6_availability_view():
    data = synthetic_market(lag_ms=500, seed=10, btc_delay_ms=7, pm_delay_ms=13)
    frame = timeline_frame(data)
    grid_c = pt.availability_state_age(frame, data["t0"], data["end"], grid_ms=250)
    grid_b = make_grid(data, "receive")
    merged = grid_b[["timestamp_ms", "pm_mid"]].merge(
        grid_c[["timestamp_ms", "pm_mid"]], on="timestamp_ms", suffixes=("_b", "_c"))
    both = merged.dropna(subset=["pm_mid_b", "pm_mid_c"])
    assert len(both) > 50
    assert np.allclose(both["pm_mid_b"], both["pm_mid_c"])  # availability == receive for this collector
    with_availability = grid_c.dropna(subset=["pm_state_availability_ts"])
    assert (with_availability["pm_state_availability_ts"]
            <= with_availability["timestamp_ms"]).all()
    receives = set(frame[frame.source == "PM"]["receive_timestamp_ms"].astype(int))
    avail = set(with_availability["pm_state_availability_ts"].astype(int))
    assert avail <= receives  # availability IS the constructing event's receive stamp


# ------------------------------------------------------- trust / resolution

def test_pt8_trust_tier_no_direction_upgrade():
    assert pt.timing_trust_tier(250, 2500) == "TIER_D"
    assert pt.timing_trust_tier(999, 1000) == "TIER_D"
    assert pt.timing_trust_tier(1000, 1000) == "TIER_C"
    assert pt.timing_trust_tier(2000, 1000) == "TIER_B"
    assert pt.timing_trust_tier(4000, 1000) == "TIER_A"
    assert pt.timing_trust_tier(None, 1000) == "UNKNOWN"
    params = list(inspect.signature(pt.timing_trust_tier).parameters)
    assert params == ["lag_ms", "ambiguity_ms"]  # direction can never upgrade a tier


def test_pt9_resolution_status_boundaries():
    assert pt.resolution_status(1000, 1000) == "ABOVE_TIMING_RESOLUTION"
    assert pt.resolution_status(500, 1000) == "NEAR_TIMING_RESOLUTION"
    assert pt.resolution_status(499.9, 1000) == "BELOW_TIMING_RESOLUTION"
    assert pt.resolution_status(-1000, 1000) == "ABOVE_TIMING_RESOLUTION"
    assert pt.resolution_status(None, 1000) == "UNKNOWN"
    assert pt.resolution_status(250, None) == "UNKNOWN"
    assert pt.resolution_status(250, -1) == "UNKNOWN"
    assert pt.resolution_status(250, 0) == "ABOVE_TIMING_RESOLUTION"


def test_pt10_250ms_under_1000ms_bound_is_below_resolution():
    assert pt.resolution_status(250, 1000) == "BELOW_TIMING_RESOLUTION"


def test_pt11_agreement_matrix():
    stable = [{"VIEW_A": "BTC_LEAD", "VIEW_B": "BTC_LEAD", "VIEW_C": "BTC_LEAD"}
              for _ in range(3)]
    matrix = pt.agreement_matrix(stable)
    assert matrix["status"] == "CLOCK_BASIS_STABLE"
    assert matrix["all_three_agree"] == 3
    mixed = stable + [{"VIEW_A": "BTC_LEAD", "VIEW_B": "PM_LEAD", "VIEW_C": "PM_LEAD"}]
    matrix2 = pt.agreement_matrix(mixed)
    assert matrix2["status"] == "CLOCK_BASIS_INSTABILITY"
    assert matrix2["pairwise"]["VIEW_A_vs_VIEW_B"] == {"agree": 3, "disagree": 1}
    assert pt.agreement_matrix([])["status"] == "UNKNOWN"


def test_pt12_event_type_latency_breakdown():
    registry = load_registry()
    base = 1_787_590_800_000
    rows = []
    for event_type, delay, count in (("price_change", 300, 50),
                                     ("last_trade_price", 5000, 30),
                                     ("book", 100, 20)):
        rows += [pm_row(base + i * 100, base + i * 100 + delay, event_type=event_type)
                 for i in range(count)]
    decomp = pt.latency_decomposition(pd.DataFrame(rows), "0x3", registry)
    breakdown = {r["event_type"]: r for r in pt.latency_explain_by(decomp, ["event_type"])}
    assert set(breakdown) == {"price_change", "last_trade_price", "book"}
    assert breakdown["price_change"]["p50_ms"] == 300
    assert breakdown["last_trade_price"]["p50_ms"] == 5000
    assert breakdown["book"]["p50_ms"] == 100


# ------------------------------------------------------------- raw integrity

def test_pt13_active_no_sidecar_not_sha_failure(tmp_path):
    closed = tmp_path / "closed.ndjson"
    closed.write_text('{"a":1}\n', encoding="utf-8")
    digest = file_sha256(closed)
    (tmp_path / "closed.ndjson.meta.json").write_text(
        json.dumps({"sha256": digest, "integrity_status": "OK", "parse_errors": 0}),
        encoding="utf-8")
    active = tmp_path / "active.ndjson"
    active.write_text('{"a":1}\n', encoding="utf-8")
    result = pt.raw_input_integrity([closed, active], active_files=[active])
    assert result["status"] == "PASS"
    assert result["active_excluded_files"] == [str(active)]
    assert result["closed_missing_sidecar_files"] == []
    assert result["closed_sha_failure_files"] == []
    # the same sidecar-less file presented as a CLOSED input is an integrity failure
    failure = pt.raw_input_integrity([closed, active], active_files=[])
    assert failure["status"] == "RAW_INTEGRITY_FAILURE"
    assert str(active) in failure["closed_missing_sidecar_files"]
    # tampered content => SHA failure
    closed.write_text('{"a":2}\n', encoding="utf-8")
    tampered = pt.raw_input_integrity([closed], active_files=[])
    assert tampered["status"] == "RAW_INTEGRITY_FAILURE"
    assert str(closed) in tampered["closed_sha_failure_files"]


def test_pt14_v2_v3_cache_separation():
    shas = [("raw/book.ndjson", "aa" * 32), ("raw/btc.ndjson", "bb" * 32)]
    assert market_cache_key(shas) != pt.market_cache_key_v3(shas)
    v2_entry = {"research_spec_version": RESEARCH_SPEC_VERSION_V2, "file_shas": dict(shas)}
    assert not cache_reusable(v2_entry, dict(shas), pt.RESEARCH_SPEC_VERSION_V3)
    assert cache_reusable(v2_entry, dict(shas), RESEARCH_SPEC_VERSION_V2)
    v3_entry = {"research_spec_version": pt.RESEARCH_SPEC_VERSION_V3, "file_shas": dict(shas)}
    assert cache_reusable(v3_entry, dict(shas), pt.RESEARCH_SPEC_VERSION_V3)
    assert not cache_reusable(v3_entry, {**dict(shas), "raw/book.ndjson": "cc" * 32},
                              pt.RESEARCH_SPEC_VERSION_V3)
    assert pt.market_cache_key_v3(shas) != pt.market_cache_key_v3(
        [("raw/book.ndjson", "cc" * 32), ("raw/btc.ndjson", "bb" * 32)])


def test_pt19_cache_key_determinism_and_version_sensitivity(monkeypatch):
    # Same raw SHA set + research spec + timing semantics -> identical key
    # (per-market outputs identical across runs; verified end-to-end on the
    # real 3-market double run).
    shas = [("raw/book.ndjson", "aa" * 32), ("raw/btc.ndjson", "bb" * 32)]
    key_v1 = pt.market_cache_key_v3(shas)
    assert key_v1 == pt.market_cache_key_v3(shas)
    # file order must not leak into the key
    assert key_v1 == pt.market_cache_key_v3(list(reversed(shas)))
    # a different timing semantics version must invalidate the cache
    monkeypatch.setattr(pt, "TIMING_SEMANTICS_VERSION", "phase2b_timing_semantics_v2")
    assert pt.market_cache_key_v3(shas) != key_v1
    monkeypatch.setattr(pt, "TIMING_SEMANTICS_VERSION", "phase2b_timing_semantics_v1")
    # a different collector version must invalidate the cache
    assert pt.market_cache_key_v3(shas, collector_version="other") != pt.market_cache_key_v3(shas)


def test_pt15_deterministic_timing_output():
    registry = load_registry()
    data = synthetic_market(lag_ms=500, seed=12)
    frame = timeline_frame(data)
    pd.testing.assert_frame_equal(pt.latency_decomposition(frame, "0x4", registry),
                                  pt.latency_decomposition(frame, "0x4", registry))
    pd.testing.assert_frame_equal(pt.availability_state_age(frame, data["t0"], data["end"]),
                                  pt.availability_state_age(frame, data["t0"], data["end"]))
    assert pt.view_method_estimates(make_grid(data), make_grid(data, "exchange", 100)) == \
        pt.view_method_estimates(make_grid(data), make_grid(data, "exchange", 100))
    values = (data["btc_receive"] - data["ts"]).tolist()
    assert pt.robust_latency_stats(values) == pt.robust_latency_stats(values)


def test_pt16_b2_second_level_floor():
    fill = 1_787_590_805_123  # ::05.123
    one_s = markout_horizon_timestamp(fill, 1)
    second_start = (fill // 1000) * 1000
    assert one_s == second_start + 999 + 1000   # fill_second_end + 1s
    assert one_s % 1000 == 999                  # anchored to second end, never inside the fill second
    assert one_s - fill > 1000
    payload = b2_milestone_payload(1, [{"observation_id": "x"}], "run", None)
    assert payload["timestamp_semantics"]["same_second_ordering"] == "FORBIDDEN"
    assert payload["timestamp_semantics"]["post_fill_anchor"] == "fill_second_end"


def test_pt17_phase2a_frozen_definitions_unchanged():
    assert p2b.RESEARCH_SPEC_VERSION == "phase2b_research_v1"
    assert p2b.GRIDS_MS == (100, 250, 500, 1000)
    assert p2b.RESPONSE_HORIZONS_MS == (100, 250, 500, 1000, 2000, 5000)
    assert p2b.SHOCK_BUCKETS_BP == ((0, 1, "0-1bp"), (1, 2, "1-2bp"), (2, 5, "2-5bp"),
                                    (5, 10, "5-10bp"), (10, float("inf"), ">10bp"))
    assert p2b.PRIMARY_COLLECTOR_VERSION == "phase2a_prospective_v4"
    assert p2b.COHORT_VERSION == "prospective_v4"
    assert inspect.signature(p2b.build_grid).parameters["book_stale_ms"].default == 5000
    assert p2s.RESEARCH_SPEC_VERSION_V2 == "phase2b_research_v2"
    assert p2s.DIRECTION_TOLERANCE_MS == 100
    assert p2s.REFRACTORY_MS == 1000
    assert p2s.BOOTSTRAP_SEED == 20260824
    assert p2s.BOOTSTRAP_MIN_MARKETS == 10
    assert frozen_invariant_check({"ledger": "x", "settings": "y"},
                                  {"ledger": "x", "settings": "y"})["status"] == "PASS"
    assert frozen_invariant_check({"ledger": "x"}, {"ledger": "z"})["status"] == "FAIL"


def test_pt18_raw_hashes_unchanged(tmp_path):
    raw = tmp_path / "raw.ndjson"
    raw.write_text('{"receive_timestamp_ms":1}\n', encoding="utf-8")
    first = file_sha256(raw)
    assert first == file_sha256(raw)          # stable across calls
    raw.write_text('{"receive_timestamp_ms":1}\n{"receive_timestamp_ms":2}\n', encoding="utf-8")
    assert file_sha256(raw) != first          # any mutation is detectable
    entry = {"research_spec_version": RESEARCH_SPEC_VERSION_V2, "file_shas": {str(raw): first}}
    assert cache_reusable(entry, {str(raw): first}, RESEARCH_SPEC_VERSION_V2)
    assert not cache_reusable(entry, {str(raw): file_sha256(raw)}, RESEARCH_SPEC_VERSION_V2)


# --------------------------------------------------------------- fixtures

def test_fixture_a_clean_clocks_timing_resolved_btc_lead():
    data = synthetic_market(lag_ms=500, seed=3)
    estimates = pt.view_method_estimates(make_grid(data), make_grid(data, "exchange", 100))
    assert estimates["method_a_lag_ms"] == 500
    assert estimates["direction"] == "BTC_LEAD"
    btc_stats = pt.robust_latency_stats((data["btc_receive"] - data["ts"]).tolist())
    pm_stats = pt.robust_latency_stats((data["pm_receive"] - data["ts"]).tolist())
    bound = pt.minimum_resolvable_lag_ms(btc_stats, pm_stats)
    assert pt.resolution_status(estimates["method_a_lag_ms"], bound) == "ABOVE_TIMING_RESOLUTION"
    assert pt.timing_trust_tier(estimates["method_a_lag_ms"], bound) in ("TIER_A", "TIER_B")
    assert pt.v2_reassessment(3, 3, 3, 3) == "TIMING_ROBUST"


def test_fixture_b_lag_below_timing_resolution():
    data = synthetic_market(lag_ms=250, seed=4)
    estimates = pt.view_method_estimates(make_grid(data), make_grid(data, "exchange", 100))
    assert estimates["direction"] == "BTC_LEAD"  # raw association survives
    noisy = 1000.0 + np.random.default_rng(5).uniform(-2000, 2000, 20000)
    stats = pt.robust_latency_stats(noisy.tolist())
    bound = pt.minimum_resolvable_lag_ms(stats, stats)
    assert bound > 1500
    assert pt.resolution_status(250, bound) == "BELOW_TIMING_RESOLUTION"
    assert pt.timing_trust_tier(250, bound) == "TIER_D"
    assert pt.v2_reassessment(3, 3, 3, 0) == "DIRECTIONALLY_ROBUST_BUT_LAG_UNRESOLVED"


def test_fixture_c_clock_basis_instability():
    data = synthetic_market(lag_ms=250, seed=6, btc_delay_ms=1000, pm_delay_ms=0)
    view_a = pt.view_method_estimates(make_grid(data, "exchange"), make_grid(data, "exchange", 100))
    view_b = pt.view_method_estimates(make_grid(data, "receive"), make_grid(data, "receive", 100))
    assert view_a["direction"] == "BTC_LEAD"
    assert view_b["direction"] == "PM_LEAD"
    matrix = pt.agreement_matrix([{"market": "m", "VIEW_A": view_a["direction"],
                                   "VIEW_B": view_b["direction"],
                                   "VIEW_C": view_b["direction"]}])
    assert matrix["status"] == "CLOCK_BASIS_INSTABILITY"
    assert matrix["pairwise"]["VIEW_A_vs_VIEW_B"]["disagree"] == 1
    decisions = pt.timing_decision("TIMING_SEMANTICS_LIMITED", clock_instability=True)
    assert "CLOCK_BASIS_INSTABILITY" in decisions


def test_fixture_d_old_frame_ts_not_network_latency():
    registry = load_registry()
    receive = 1_787_590_800_000
    frame = pd.DataFrame([pm_row(receive - 5000, receive, child=True)])
    row = pt.latency_decomposition(frame, "0xabc", registry).iloc[0]
    assert int(row["receive_minus_source_ms"]) == 5000
    assert int(row["state_age_ms"]) == 5000
    assert row["timestamp_class"] == "SOURCE_FRAME_TIME"
    interp = pt.interpret_latency(row["timestamp_class"])
    assert interp["network_latency_claim"] is False
    assert "NETWORK_LATENCY" not in interp["quantity"].upper()
    # a 5s-old frame timestamp received now is NOT evidence of 5s network latency


def test_fixture_e_valid_but_stale_state():
    t0 = 1_787_590_800_000
    frame = pd.DataFrame([pm_row(t0, t0 + 5, 0.5)])
    grid = pt.availability_state_age(frame, t0, t0 + 6000, grid_ms=250)
    fresh = grid[grid.timestamp_ms == t0 + 1000].iloc[0]
    assert int(fresh["pm_state_info_age_ms"]) == 1000
    assert bool(fresh["book_valid"]) is True
    late = grid[grid.timestamp_ms == t0 + 4000].iloc[0]
    assert int(late["pm_state_info_age_ms"]) == 4000   # state info is 4s old...
    assert bool(late["book_valid"]) is True            # ...yet the book is still VALID
    stale = grid[grid.timestamp_ms == t0 + 5250].iloc[0]
    assert int(stale["pm_state_info_age_ms"]) == 5250
    assert bool(stale["book_valid"]) is False          # beyond 5s: VALID window over


# ------------------------------------------------------- stats / decisions

def test_robust_latency_stats():
    values = [100, 200, 300, 600, 1200, 6000]
    stats = pt.robust_latency_stats(values)
    assert stats["n"] == 6
    assert stats["p50_ms"] == 450.0
    assert stats["mad_ms"] == 300.0
    assert stats["frac_gt_250ms"] == 4 / 6
    assert stats["frac_gt_500ms"] == 3 / 6
    assert stats["frac_gt_1000ms"] == 2 / 6
    assert stats["frac_gt_5000ms"] == 1 / 6
    empty = pt.robust_latency_stats([])
    assert empty["n"] == 0 and empty["p50_ms"] is None


def test_minimum_resolvable_lag():
    assert pt.minimum_resolvable_lag_ms({"p99_minus_p50_ms": 710.0},
                                        {"p99_minus_p50_ms": 7426.0}) == 7426.0
    assert pt.minimum_resolvable_lag_ms({}, {"p99_minus_p50_ms": 7426.0}) == 7426.0
    assert pt.minimum_resolvable_lag_ms({}, {}) is None


def test_systematic_offset_assessment():
    tight = pt.robust_latency_stats([2540, 2550, 2560] * 100)
    result = pt.systematic_offset_assessment(tight)
    assert result["pattern"] == "CONSTANT_OFFSET_DOMINANT"
    assert result["conclusion"] == "NONE - local clock offset unknown, no decomposition possible"
    wide = pt.robust_latency_stats(
        np.random.default_rng(2).uniform(0, 10000, 5000).tolist())
    assert pt.systematic_offset_assessment(wide)["pattern"] == "VARIABLE_DELAY_DOMINANT"


def test_coarse_lag_buckets():
    assert pt.coarse_lag_bucket(250) == "0-500ms"
    assert pt.coarse_lag_bucket(-499) == "0-500ms"
    assert pt.coarse_lag_bucket(-700) == "500-1000ms"
    assert pt.coarse_lag_bucket(1500) == "1-2s"
    assert pt.coarse_lag_bucket(-2500) == ">2s"
    assert pt.coarse_lag_bucket(None) is None


def test_decision_vocabularies():
    decisions = pt.timing_decision("TIMING_SEMANTICS_LIMITED",
                                   n_with_estimate=3, n_not_above_resolution=3)
    assert decisions == ["TIMING_SEMANTICS_LIMITED", "TIMING_RESOLUTION_INSUFFICIENT"]
    pt.assert_allowed_timing_decision(decisions)
    with pytest.raises(ValueError):
        pt.assert_allowed_timing_decision(["CONFIRMED_250MS"])
    with pytest.raises(ValueError):
        pt.assert_allowed_v2_reassessment("CONFIRMED_250MS")
    for outcome in pt.ALLOWED_V2_REASSESSMENTS:
        pt.assert_allowed_v2_reassessment(outcome)
    assert pt.v2_reassessment(1, 1, 1, 1) == "INSUFFICIENT_DATA"
    assert pt.v2_reassessment(3, 3, 1, 0) == "NOT_TIMING_ROBUST"
    assert pt.v2_reassessment(3, 3, 2, 3) == "TIMING_ROBUST"
    assert pt.v2_reassessment(3, 3, 3, 1) == "DIRECTIONALLY_ROBUST_BUT_LAG_UNRESOLVED"
