"""PS1-PS15 Phase 2B-Research stability tests plus synthetic fixtures A-E.

Fixtures A-E (pre-registered): A=250ms BTC lead, B=500ms BTC lead,
C=no lag, D=PM leads, E=mixed markets.  E must never be reported as a
universal BTC lead.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from std0_quant.audit.prospective import CohortManifest
from std0_quant.research.phase2b import conservative_fill_window
from std0_quant.research.phase2b_stability import (
    RESEARCH_SPEC_VERSION_V2,
    b1_maturity_state,
    b2_eligible_observations,
    b2_milestone_payload,
    b2_observation_state,
    cache_reusable,
    classify_direction,
    clock_basis_assessment,
    collect_shock_response_values,
    combine_value_maps,
    dependence_sensitivity,
    emit_milestone_once,
    equal_market_summary,
    file_sha256,
    frozen_invariant_check,
    is_full_lifecycle_v4_market,
    latency_summary,
    market_bootstrap,
    market_cache_key,
    markout_horizon_timestamp,
    method_a_peak,
    method_agreement,
    method_b_peak,
    non_overlapping_anchors,
    per_market_lead_lag_row,
    response_stats_from_values,
    shock_anchor_rows,
)


def synthetic_market(seed: int, lag_steps: int, n: int = 2400, grid_ms: int = 250) -> pd.DataFrame:
    """BTC returns; PM follows with lag_steps delay (positive => BTC leads)."""
    rng = np.random.default_rng(seed)
    ret = rng.normal(0.0, 30.0, n)  # bp per step -> nearly every 1s window is a shock
    btc = 100.0 * np.cumprod(1.0 + ret / 10_000.0)
    if lag_steps > 0:
        pm_ret = np.r_[np.zeros(lag_steps), ret[:-lag_steps]]
    elif lag_steps < 0:
        pm_ret = np.r_[ret[-lag_steps:], np.zeros(-lag_steps)]
    else:
        pm_ret = ret.copy()
    pm = 0.5 + np.cumsum(pm_ret / 10_000.0 * 10.0)
    grid = pd.DataFrame({
        "grid_ms": grid_ms,
        "timestamp_ms": np.arange(0, n * grid_ms, grid_ms, dtype="int64"),
        "btc_price": btc,
        "pm_mid": pm,
    })
    grid["btc_ret_1s_bp"] = grid["btc_price"].pct_change(max(1, 1000 // grid_ms), fill_method=None) * 10_000
    return grid


def fine_grid(primary: pd.DataFrame, grid_ms: int = 100) -> pd.DataFrame:
    end = int(primary["timestamp_ms"].max()) + 100
    fine = pd.DataFrame({"timestamp_ms": np.arange(0, end, grid_ms, dtype="int64")})
    fine["pm_mid"] = np.interp(fine["timestamp_ms"], primary["timestamp_ms"], primary["pm_mid"])
    return fine


P30_MS, P35_MS = 30.0, 35.0


def latencies(p50: float = 10.0, p99: float = 40.0) -> dict:
    return {"n": 100, "p50_ms": p50, "p90_ms": P30_MS, "p95_ms": P35_MS, "p99_ms": p99, "max_ms": p99 + 10}


# ------------------------------------------------------------- PS1

def test_ps1_per_market_independence():
    btc_lead = synthetic_market(1, 1)
    pm_lead = synthetic_market(2, -2)
    row_a = per_market_lead_lag_row("c1", "slug-a", 0, 600_000, btc_lead, fine_grid(btc_lead),
                                    btc_lead, latencies(), latencies())
    row_b = per_market_lead_lag_row("c2", "slug-b", 0, 600_000, pm_lead, fine_grid(pm_lead),
                                    pm_lead, latencies(), latencies())
    assert row_a["method_a_lag_ms"] == 250 and row_a["direction"] == "BTC_LEAD"
    assert row_b["method_a_lag_ms"] == -500 and row_b["direction"] == "PM_LEAD"
    summary = equal_market_summary([row_a, row_b])
    assert summary["direction_counts"] == {"BTC_LEAD": 1, "PM_LEAD": 1}
    assert summary["universal_direction_claim"] is False
    # pooled is a separate diagnostic, never a replacement for the per-market table
    assert isinstance(method_a_peak(pd.concat([btc_lead, pm_lead], ignore_index=True)), dict)


# ------------------------------------------------------------- PS2

def test_ps2_overlapping_and_non_overlapping_both_kept():
    grid = synthetic_market(3, 1)
    row = per_market_lead_lag_row("c", "slug", 0, 600_000, grid, fine_grid(grid), grid,
                                  latencies(), latencies())
    assert row["n_shocks_overlapping"] > 0
    assert 0 < row["n_shocks_non_overlapping_1s"] < row["n_shocks_overlapping"]
    assert row["method_b_n"] > 0 and row["method_b_n_non_overlapping_1s"] > 0


def test_ps2_dependence_warning_cannot_be_hidden():
    overlap = {1000: {"n": 100, "signed_mean": 0.010}}
    same = {1000: {"n": 100, "signed_mean": 0.011}}
    flipped = {1000: {"n": 100, "signed_mean": -0.010}}
    weak = {1000: {"n": 100, "signed_mean": 0.001}}
    assert dependence_sensitivity(overlap, same)["warning"] is False
    assert dependence_sensitivity(overlap, flipped)["warning"] is True
    assert dependence_sensitivity(overlap, flipped)["sign_flip"] is True
    assert dependence_sensitivity(overlap, weak)["warning"] is True


# ------------------------------------------------------------- PS3

def test_ps3_one_second_refractory():
    assert non_overlapping_anchors([0, 400, 1500, 1600, 3000]) == [0, 1500, 3000]
    assert non_overlapping_anchors([0, 999]) == [0]
    assert non_overlapping_anchors([0, 1000]) == [0, 1000]


# ------------------------------------------------------------- PS4

def test_ps4_clock_basis_consistency_and_instability():
    stable = clock_basis_assessment(250, 250, latencies(), latencies())
    assert stable["status"] == "CLOCK_BASIS_CONSISTENT"
    unstable = clock_basis_assessment(250, -500, latencies(), latencies())
    assert unstable["status"] == "CLOCK_BASIS_INSTABILITY"
    unknown = clock_basis_assessment(None, 250, latencies(), latencies())
    assert unknown["status"] == "CLOCK_BASIS_UNKNOWN"


def test_ps4_timing_resolution_warning():
    small_drift = clock_basis_assessment(250, 250, latencies(p99=40), latencies(p99=40))
    assert small_drift["timing_resolution_warning"] is False
    big_drift = clock_basis_assessment(250, 250, latencies(p99=300), latencies(p99=40))
    assert big_drift["timing_resolution_warning"] is True


# ------------------------------------------------------------- PS5

def test_ps5_latency_distribution():
    stats = latency_summary(list(range(1, 101)))
    assert stats["n"] == 100
    assert stats["p50_ms"] == pytest.approx(50.5)
    assert stats["p90_ms"] == pytest.approx(90.1)
    assert stats["p99_ms"] == pytest.approx(99.01, abs=0.02)
    assert stats["max_ms"] == 100.0
    empty = latency_summary([])
    assert empty["n"] == 0 and empty["p50_ms"] is None


# ------------------------------------------------------------- PS6

def test_ps6_full_lifecycle_gating():
    def market(**overrides):
        row = {"collector_version": "phase2a_prospective_v4", "lifecycle": "FULL_LIFECYCLE_MARKET",
               "btc_coverage_pct": 0.995, "book_coverage_pct": 0.997}
        row.update(overrides)
        return row

    assert is_full_lifecycle_v4_market(market())
    assert not is_full_lifecycle_v4_market(market(collector_version="phase2a_prospective_v3"))
    assert not is_full_lifecycle_v4_market(market(lifecycle="PARTIAL_LIFECYCLE"))
    assert not is_full_lifecycle_v4_market(market(btc_coverage_pct=0.5))
    assert not is_full_lifecycle_v4_market(market(book_coverage_pct=0.98))


# ------------------------------------------------------------- PS7

def test_ps7_milestone_idempotent_and_immutable(tmp_path):
    payload = {"milestone": "B1-M3", "n_markets": 3}
    status, first = emit_milestone_once(tmp_path, "phase2b_b1_m3", "run1", payload, "# B1-M3\n")
    assert status == "written" and first is not None and first.exists()
    status2, path2 = emit_milestone_once(tmp_path, "phase2b_b1_m3", "run2", {"milestone": "changed"}, "# changed\n")
    assert status2 == "already_present" and path2 == first
    assert json.loads(first.read_text(encoding="utf-8"))["milestone"] == "B1-M3"
    assert len(list(tmp_path.glob("phase2b_b1_m3_*.json"))) == 1


def test_ps7_b1_maturity_states():
    assert b1_maturity_state(0) == "TINY_SAMPLE"
    assert b1_maturity_state(2) == "TINY_SAMPLE"
    assert b1_maturity_state(3) == "EARLY_REPLICATION"
    assert b1_maturity_state(9) == "EARLY_REPLICATION"
    assert b1_maturity_state(10) == "EXPLORATORY_REPLICATION"
    assert b1_maturity_state(19) == "EXPLORATORY_REPLICATION"
    assert b1_maturity_state(20) == "MULTI_MARKET_EVIDENCE"
    assert b1_maturity_state(49) == "MULTI_MARKET_EVIDENCE"
    assert b1_maturity_state(50) == "INTERMEDIATE_STABILITY"
    assert b1_maturity_state(99) == "INTERMEDIATE_STABILITY"
    assert b1_maturity_state(100) == "BROAD_EXPLORATORY_EVIDENCE"


# ------------------------------------------------------------- PS8

def test_ps8_incremental_manifest(tmp_path):
    raw = tmp_path / "raw.ndjson"
    raw.write_text('{"a":1}\n', encoding="utf-8")
    sha = file_sha256(raw)
    assert sha == hashlib.sha256(raw.read_bytes()).hexdigest()
    key1 = market_cache_key([(str(raw), sha)])
    assert key1 == market_cache_key([(str(raw), sha)])
    raw.write_text('{"a":2}\n', encoding="utf-8")
    assert market_cache_key([(str(raw), file_sha256(raw))]) != key1
    entry = {"research_spec_version": RESEARCH_SPEC_VERSION_V2, "file_shas": {str(raw): sha}}
    current = {str(raw): sha}
    assert cache_reusable(entry, current, RESEARCH_SPEC_VERSION_V2, tmp_path / "missing.parquet") is False
    assert cache_reusable(entry, current, RESEARCH_SPEC_VERSION_V2) is True
    assert cache_reusable(entry, {str(raw): "other"}, RESEARCH_SPEC_VERSION_V2) is False
    assert cache_reusable(entry, current, "phase2b_research_v1") is False
    assert cache_reusable(None, current) is False


# ------------------------------------------------- PS9-PS13 (B2)

def observation(**overrides):
    row = {"condition_id": "cond-1", "prediction_ts_ms": 1_787_600_000_000,
           "collector_version": "phase2a_prospective_v4", "market_start_ms": 1_787_590_800_000,
           "coverage_pass": True, "provenance_pass": True, "sanity_pass": True,
           "lineage_pass": True, "pit_pass": True}
    row.update(overrides)
    return row


def test_ps9_b2_activation_n001(tmp_path):
    manifest = CohortManifest(tmp_path / "cohort.json")
    manifest.upsert([observation()])
    eligible = b2_eligible_observations(manifest.observations())
    assert len(eligible) == 1
    assert b2_observation_state(1) == "DESCRIPTIVE_ONLY_TINY_N"
    payload = b2_milestone_payload(1, eligible, "run1", "ctx.parquet")
    assert payload["milestone"] == "B2-N001" and payload["state"] == "DESCRIPTIVE_ONLY_TINY_N"
    assert payload["first_observation_id"] == eligible[0]["observation_id"]


def test_ps10_b2_rejection_paths(tmp_path):
    manifest = CohortManifest(tmp_path / "cohort.json")
    manifest.upsert([observation(), observation(condition_id="cond-2", coverage_pass=False),
                     observation(condition_id="cond-3", lineage_pass=False),
                     observation(condition_id="cond-4", pit_pass=False),
                     observation(condition_id="cond-5", collector_version="phase2a_prospective_v3")])
    eligible = b2_eligible_observations(manifest.observations())
    assert [row["condition_id"] for row in eligible] == ["cond-1"]
    assert b2_observation_state(0) == "INSUFFICIENT_STD0_EVENTS"


def test_ps11_observation_id_is_primary_key(tmp_path):
    manifest = CohortManifest(tmp_path / "cohort.json")
    manifest.upsert([observation()])
    eligible = b2_eligible_observations(manifest.observations())
    observation_id = eligible[0]["observation_id"]
    assert isinstance(observation_id, str) and len(observation_id) == 64
    assert all(c in "0123456789abcdef" for c in observation_id)


def test_ps12_same_second_ambiguity_forbidden():
    window = conservative_fill_window(12_345)
    assert window == {"fill_second_start_ms": 12000, "fill_second_end_ms": 12999,
                      "pre_context_cutoff_ms": 11999, "post_markout_anchor_ms": 12999}
    assert window["pre_context_cutoff_ms"] < window["fill_second_start_ms"]


def test_ps13_conservative_anchor():
    assert markout_horizon_timestamp(12_345, 1) == 13_999
    assert markout_horizon_timestamp(12_345, 5) == 17_999
    assert markout_horizon_timestamp(12_999, 1) == 13_999


# ------------------------------------------------- PS14-PS15

def test_ps14_phase2a_gates_unchanged():
    settings = Path(__file__).parents[1] / "config/settings.yaml"
    before = hashlib.sha256(settings.read_bytes()).hexdigest()
    grid = synthetic_market(7, 1)
    per_market_lead_lag_row("c", "s", 0, 600_000, grid, fine_grid(grid), grid,
                            latencies(), latencies())
    assert hashlib.sha256(settings.read_bytes()).hexdigest() == before
    assert frozen_invariant_check({"a": "x"}, {"a": "x"})["status"] == "PASS"
    assert frozen_invariant_check({"a": "x"}, {"a": "y"})["status"] == "FAIL"
    assert frozen_invariant_check({"a": "x"}, {"a": "y"})["changed"] == ["a"]


def test_ps15_raw_never_mutated(tmp_path):
    raw = tmp_path / "raw.ndjson"
    raw.write_text('{"collector_version":"phase2a_prospective_v4"}\n', encoding="utf-8")
    before = file_sha256(raw)
    grid = synthetic_market(8, 2)
    per_market_lead_lag_row("c", "s", 0, 600_000, grid, fine_grid(grid), grid,
                            latencies(), latencies())
    emit_milestone_once(tmp_path / "reports", "phase2b_b1_m3", "run1", {"x": 1}, "# m3\n")
    assert file_sha256(raw) == before
    assert not list((tmp_path / "reports").glob(f"{raw.name}*"))


# ------------------------------------------------- fixtures A-E

def test_fixture_a_250ms_btc_lead():
    grid = synthetic_market(11, 1)
    peak = method_a_peak(grid)
    assert peak["lag_ms"] == 250
    assert classify_direction(peak["lag_ms"]) == "BTC_LEAD"


def test_fixture_b_500ms_btc_lead():
    grid = synthetic_market(12, 2)
    peak = method_a_peak(grid)
    assert peak["lag_ms"] == 500
    assert classify_direction(peak["lag_ms"]) == "BTC_LEAD"


def test_fixture_c_no_lag_synchronous():
    grid = synthetic_market(13, 0)
    peak = method_a_peak(grid)
    assert peak["lag_ms"] == 0
    assert classify_direction(peak["lag_ms"]) == "SYNCHRONOUS"


def test_fixture_d_pm_leads():
    grid = synthetic_market(14, -2)
    peak = method_a_peak(grid)
    assert peak["lag_ms"] == -500
    assert classify_direction(peak["lag_ms"]) == "PM_LEAD"


def test_fixture_e_mixed_is_not_universal_btc_lead():
    rows = []
    for seed, lag, slug in ((21, 1, "btc-lead"), (22, -2, "pm-lead"), (23, 0, "sync")):
        grid = synthetic_market(seed, lag)
        rows.append(per_market_lead_lag_row(f"c{seed}", slug, 0, 600_000, grid,
                                            fine_grid(grid), grid, latencies(), latencies()))
    summary = equal_market_summary(rows)
    assert len(summary["direction_counts"]) == 3
    assert summary["universal_direction_claim"] is False
    assert summary["universal_direction"] is None
    assert summary["direction_counts"]["BTC_LEAD"] == 1
    assert summary["direction_counts"]["PM_LEAD"] == 1
    assert summary["direction_counts"]["SYNCHRONOUS"] == 1


# ------------------------------------------------- supporting units

def test_method_agreement_rule():
    consistent = method_agreement(["BTC_LEAD", "BTC_LEAD", "SYNCHRONOUS"])
    assert consistent["agreement"] == "METHOD_CONSISTENT_MARKET"
    assert consistent["direction"] == "BTC_LEAD"
    inconsistent = method_agreement(["BTC_LEAD", "PM_LEAD", "SYNCHRONOUS"])
    assert inconsistent["agreement"] == "METHOD_INCONSISTENT"
    assert inconsistent["direction"] is None


def test_response_collection_and_stats():
    anchors = pd.DataFrame({"timestamp_ms": [0, 1000], "pm_mid": [0.5, 0.5],
                            "btc_ret_1s_bp": [60.0, -60.0]})
    fine = pd.DataFrame({"timestamp_ms": np.arange(0, 5100, 100, dtype="int64")})
    fine["pm_mid"] = np.concatenate([np.full(10, 0.5), np.full(10, 0.6), np.full(31, 0.55)])
    values = collect_shock_response_values(anchors, fine, (1000, 2000, 5000))
    assert values[1000] == pytest.approx([0.1, -0.05])
    stats = response_stats_from_values(values[1000])
    assert stats["n"] == 2 and stats["signed_mean"] == pytest.approx(0.025)
    peak = method_b_peak(values)
    assert peak["lag_ms"] == 5000 and peak["n"] == 1
    combined = combine_value_maps([values, values])
    assert len(combined[1000]) == 4


def test_bootstrap_n_below_10_not_computed():
    assert market_bootstrap([250] * 9)["status"] == "NOT_COMPUTED_N_BELOW_10"
    result = market_bootstrap([250] * 10)
    assert result["status"] == "COMPUTED_EXPLORATORY"
    assert result["mean_lag_ms"] == 250.0
    assert result["ci_lo_ms"] == 250.0 and result["ci_hi_ms"] == 250.0
    assert result["direction_stability_fraction"] == 1.0


def test_shock_anchor_and_direction_helpers():
    grid = synthetic_market(31, 1)
    anchors = shock_anchor_rows(grid)
    assert len(anchors) > 0
    assert classify_direction(None) is None
    assert classify_direction(100) == "SYNCHRONOUS"
    assert classify_direction(101) == "BTC_LEAD"
    assert classify_direction(-101) == "PM_LEAD"
