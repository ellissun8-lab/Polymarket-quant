"""Phase 2B v3.1 tests (PC1-PC10): interpretation hierarchy, recorder
reliability gate, M10 evidence accumulation.

v3.1 core principles under test:
- DIRECTION MAY REPLICATE BEFORE MAGNITUDE IS IDENTIFIED;
- a peak lag below the timing-resolution bound can never support a magnitude
  conclusion (numeric peaks stay descriptive);
- the erratum corrects wording without mutating immutable v3 reports;
- recorder reliability is an engineering gate (collector version unchanged);
- M10/milestone artifacts are idempotent and immutable once written;
- bootstrap unit is the MARKET, never the shock;
- timing-resolved BTC_LEAD fraction is always reported separately from the
  raw BTC_LEAD fraction;
- B2 N001 identity comes from Phase 2A observation_id;
- Phase 2A / v2 frozen vocabularies are unchanged.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

from std0_quant.collectors.recorder_reliability import (
    ENGINEERING_FIX_VERSION,
    detect_recorder_hotfix,
    health_step_isolated,
)
from std0_quant.research import phase2b_stability as p2s
from std0_quant.research import phase2b_timing as pt
from std0_quant.research.phase2b import COHORT_VERSION
from std0_quant.research.phase2b_stability import (
    file_sha256,
    emit_milestone_once,
    b2_milestone_payload,
)

ROOT = Path(__file__).resolve().parents[1]

V3_RUN_ID = "20260824T193505Z"


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------- PC1
def test_pc1_unsupported_lag_magnitude_cannot_enter_conclusion():
    """While |peak| < resolution the supported conclusion must not contain a
    magnitude claim; the guard raises InterpretationOverreach otherwise."""
    clean = ("Across the 3 estimated eligible prospective_v4 markets, the dominant "
             "association is BTC_LEAD on all three clock views; however, the measured "
             "peak lags are below the current timing-resolution bound, so the lag "
             "magnitude is unresolved.")
    result = pt.interpretation_guard(clean, 250, 10323)
    assert result == {"lag_magnitude_status": "LAG_MAGNITUDE_UNRESOLVED"}
    for overreach in (
        "BTC leads PM by 250ms",
        "BTC leads PM within 500ms",
        "BTC generally leads PM within roughly 0-2s",
        "the peak lag is 250ms",
    ):
        with pytest.raises(pt.InterpretationOverreach):
            pt.interpretation_guard(overreach, 250, 10323)
    # once |peak| >= resolution a magnitude wording is allowed
    allowed = pt.interpretation_guard("BTC leads PM by 250ms", 250, 200)
    assert allowed == {"lag_magnitude_status": "LAG_MAGNITUDE_RESOLVED"}
    # missing estimates are unresolved by construction
    assert pt.interpretation_guard(clean, None, None)["lag_magnitude_status"] \
        == "LAG_MAGNITUDE_UNRESOLVED"


# ---------------------------------------------------------------- PC2
def test_pc2_below_resolution_exact_lag_descriptive_only():
    """Level 2 is UNRESOLVED and the Level 3 grid peak is DESCRIPTIVE_ONLY
    whenever |peak| < resolution; both flip only at/above the bound."""
    below = pt.timing_hierarchy("BTC_LEAD", 250, 10323)
    assert below["level_1_direction"] == "BTC_LEAD"
    assert below["level_2_lag_magnitude"] == "UNRESOLVED"
    assert below["lag_magnitude_status"] == "LAG_MAGNITUDE_UNRESOLVED"
    assert below["level_3_peak_ms"] == 250
    assert below["level_3_role"] == "DESCRIPTIVE_ONLY"
    # negative peak of the same magnitude behaves identically (|peak| rule)
    assert pt.timing_hierarchy("PM_LEAD", -250, 10323)["lag_magnitude_status"] \
        == "LAG_MAGNITUDE_UNRESOLVED"
    at_bound = pt.timing_hierarchy("BTC_LEAD", 750, 750)
    assert at_bound["level_2_lag_magnitude"] == "500-1000ms"
    assert at_bound["lag_magnitude_status"] == "LAG_MAGNITUDE_RESOLVED"
    assert at_bound["level_3_role"] == "TIMING_CONCLUSION_ALLOWED"
    above = pt.timing_hierarchy("BTC_LEAD", 1500, 1000)
    assert above["level_2_lag_magnitude"] == "1-2s"
    assert above["level_3_role"] == "TIMING_CONCLUSION_ALLOWED"
    unknown = pt.timing_hierarchy(None, None, None)
    assert unknown["level_1_direction"] is None
    assert unknown["lag_magnitude_status"] == "LAG_MAGNITUDE_UNRESOLVED"
    assert unknown["level_3_role"] == "DESCRIPTIVE_ONLY"


# ---------------------------------------------------------------- PC3
def test_pc3_erratum_does_not_mutate_old_report(tmp_path):
    """The erratum is additive: immutable v3 artifacts keep their bytes and a
    second emit for the same statement writes nothing."""
    audit = tmp_path / f"phase2b_timing_audit_{V3_RUN_ID}.json"
    research = tmp_path / f"phase2b_research_v3_{V3_RUN_ID}.json"
    for path in (audit, research):
        path.write_text(json.dumps({"run_id": V3_RUN_ID,
                                    "research_spec_version": "phase2b_research_v3"}),
                        encoding="utf-8")
    shas_before = {p.name: file_sha256(p) for p in (audit, research)}

    script = _load_script("emit_v3_interpretation_erratum")
    path, erratum = script.emit_erratum(tmp_path, V3_RUN_ID,
                                        timestamp="20260825T000000Z")
    assert path is not None and path.exists()
    assert erratum["classification"] == "INTERPRETATION_PRECISION_CORRECTION"
    assert erratum["data_unchanged"] is True
    assert erratum["metrics_unchanged"] is True
    assert erratum["research_spec_unchanged"] is True
    assert erratum["v2_reassessment_unchanged"] == "DIRECTIONALLY_ROBUST_BUT_LAG_UNRESOLVED"
    assert erratum["corrected_interpretation"] == ["DIRECTION_REPLICATED_EARLY",
                                                   "LAG_MAGNITUDE_UNRESOLVED"]
    # immutable v3 reports untouched, byte for byte
    assert {p.name: file_sha256(p) for p in (audit, research)} == shas_before
    # referenced artifacts are pinned by sha256
    refs = erratum["immutable_artifacts_referenced"]
    assert refs and all(r["sha256"] for r in refs)

    # idempotent: same statement -> no second file
    errata_before = sorted(p.name for p in
                           tmp_path.glob("phase2b_v3_interpretation_erratum_*.json"))
    path2, erratum2 = script.emit_erratum(tmp_path, V3_RUN_ID,
                                          timestamp="20260901T000000Z")
    assert path2 is None
    assert erratum2["statement_sha256"] == erratum["statement_sha256"]
    assert sorted(p.name for p in
                  tmp_path.glob("phase2b_v3_interpretation_erratum_*.json")) \
        == errata_before


# ---------------------------------------------------------------- PC4
def test_pc4_hotfix_detector_and_engineering_version_metadata():
    """The recorder reliability gate passes all 8 engineering items and keeps
    the collector version unchanged (engineering fix, no semantic bump)."""
    gate = detect_recorder_hotfix()
    assert gate["overall"] == "MEMORY_HOTFIX_PASS"
    assert gate["engineering_fix_version"] == ENGINEERING_FIX_VERSION \
        == "recorder_reliability_fix_v1"
    assert gate["collector_version_unchanged"] == "phase2a_prospective_v4"
    assert gate["version_bump_required"] is False
    assert len(gate["items"]) == 8
    assert set(gate["items"].values()) == {"PASS"}
    assert set(gate["items"]) == {
        "STREAMING_SHA256", "HEALTH_TAIL_READER_BOUNDED",
        "HEALTH_FAILURE_ISOLATION", "TASK_EXCEPTION_OWNERSHIP",
        "FAILURE_CLASSIFICATION", "ORPHAN_SIDECAR_STARTUP_RECOVERY",
        "MEMORY_QUEUE_TELEMETRY", "ANALYSIS_NEVER_STOPS_RECORDER"}


# ---------------------------------------------------------------- PC5
def test_pc5_health_failure_does_not_kill_collector():
    """A health build/publish failure (including MemoryError) is classified,
    journaled and swallowed - the collectors keep running."""
    events = []

    class Journal:
        def emit(self, event, **kwargs):
            events.append((event, kwargs))

    def build_oom():
        raise MemoryError("oom")

    published = []
    result = health_step_isolated(build_oom, published.append, Journal())
    assert result["status"] == "HEALTH_REPORT_FAILURE"
    assert result["failure_kind"] == "MEMORY_ERROR"
    assert published == []  # nothing published, but NO exception escaped
    assert events and events[0][0] == "health_report_failure"

    def build_broken():
        raise RuntimeError("health path bug")

    result2 = health_step_isolated(build_broken, lambda p: None, None)
    assert result2["status"] == "HEALTH_REPORT_FAILURE"
    assert result2["failure_kind"] == "HEALTH_REPORT_FAILURE"

    # healthy path still publishes
    ok = health_step_isolated(lambda: {"a": 1}, published.append, None)
    assert ok == {"status": "OK", "payload": {"a": 1}}
    assert published == [{"a": 1}]


# ---------------------------------------------------------------- PC6
def test_pc6_m10_milestone_idempotent(tmp_path):
    """First attainment writes the immutable M10 artifact; a later run for the
    same milestone never rewrites or duplicates it."""
    payload = {"milestone": "B1-M10", "n_markets": 10, "no_real_trading": True,
               "immutable": True}
    status, path = emit_milestone_once(tmp_path, "phase2b_b1_v3_m10",
                                       "20260825T000000Z", payload, "# B1-M10\n")
    assert status == "written" and path is not None
    frozen = (path.read_bytes(), path.with_suffix(".md").read_bytes())

    status2, path2 = emit_milestone_once(tmp_path, "phase2b_b1_v3_m10",
                                         "20260901T000000Z",
                                         {"milestone": "B1-M10", "changed": True},
                                         "# B1-M10 rewritten\n")
    assert status2 == "already_present" and path2 == path
    assert (path.read_bytes(), path.with_suffix(".md").read_bytes()) == frozen
    assert not (tmp_path / "phase2b_b1_v3_m10_20260901T000000Z.json").exists()
    assert len(list(tmp_path.glob("phase2b_b1_v3_m10_*.json"))) == 1


# ---------------------------------------------------------------- PC7
def test_pc7_market_bootstrap_uses_market_clusters():
    """Bootstrap unit is the MARKET: <10 markets -> not computed; >=10 ->
    deterministic seeded resampling of markets, and a lag CI can never
    upgrade an unresolved timing conclusion."""
    rows = [{"slug": f"btc-updown-5m-{i}", "direction": "BTC_LEAD",
             "raw_btc_lead": True, "method_a_lag_ms": 200 + 10 * i,
             "method_b_signed_mean": 0.5, "timing_ambiguity_ms": 5000}
            for i in range(10)]
    result = pt.market_bootstrap_fractions(rows)
    assert result["status"] == "COMPUTED"
    assert result["bootstrap_unit"] == "MARKET"
    assert result["seed"] == 20260824
    assert result["n_markets"] == 10
    assert result["btc_lead_fraction"] == 1.0
    assert result["btc_lead_fraction_ci"] == {"p2_5": 1.0, "p97_5": 1.0}
    # deterministic under the frozen seed
    assert pt.market_bootstrap_fractions(rows) == result
    # median descriptive lag below the median bound keeps magnitude unresolved
    assert result["median_peak_lag_ms"] == float(np.median(
        [r["method_a_lag_ms"] for r in rows]))
    assert abs(result["median_peak_lag_ms"]) < result["median_timing_bound_ms"]
    assert result["lag_magnitude_status"] == "LAG_MAGNITUDE_UNRESOLVED"

    # one dissenting market moves the fraction; the CI spreads by resampling
    # markets (clusters), not shocks
    mixed = rows[:9] + [{"slug": "btc-updown-5m-dissent", "direction": "PM_LEAD",
                         "raw_btc_lead": False, "method_a_lag_ms": -300,
                         "method_b_signed_mean": -0.5,
                         "timing_ambiguity_ms": 5000}]
    r2 = pt.market_bootstrap_fractions(mixed)
    assert r2["status"] == "COMPUTED"
    assert r2["btc_lead_fraction"] == pytest.approx(0.9)
    assert r2["btc_lead_fraction_ci"]["p2_5"] < 0.9
    assert r2["btc_lead_fraction_ci"]["p97_5"] > 0.9
    assert r2["median_response_sign"] == 1.0

    # below the pre-registered minimum the bootstrap stays not-computed
    small = pt.market_bootstrap_fractions(rows[:9])
    assert small == {"status": "NOT_COMPUTED_N_BELOW_10", "n_markets": 9,
                     "seed": 20260824, "bootstrap_unit": "MARKET"}


# ---------------------------------------------------------------- PC8
def test_pc8_timing_resolved_fraction_separate_from_raw(tmp_path):
    """M-milestone payloads always carry the raw BTC_LEAD fraction and the
    timing-resolved BTC_LEAD fraction as SEPARATE fields (plus the full
    section-15 M10 field set)."""
    runner = _load_script("run_phase2b_research")
    rows = [{"slug": f"m{i}", "calendar_date": "2026-08-24",
             "direction": "BTC_LEAD", "raw_btc_lead": True,
             "method_a_lag_ms": 250, "timing_ambiguity_ms": 10323,
             "timing_resolved_btc_lead": False,
             "method_agreement": "METHOD_CONSISTENT_MARKET",
             "clock_views_agree": True,
             "dependence_sensitivity_warning": False} for i in range(3)]
    payload = runner.b1_v3_milestone_payload(
        3, 3, "run1", rows, {"equal": True},
        {"status": "NOT_COMPUTED_N_BELOW_10"},
        "DIRECTIONALLY_ROBUST_BUT_LAG_UNRESOLVED", str(tmp_path / "a.parquet"))
    # raw direction replicated, timing-resolved fraction is separately 0.0
    assert payload["raw_btc_lead_fraction"] == 1.0
    assert payload["timing_resolved_btc_lead_fraction"] == 0.0
    assert payload["timing_resolved_direction_counts"] == {"BTC_LEAD": 0}
    # section-15 M10 fields
    for key in ("n_days", "direction_fractions", "pm_lead_fraction",
                "synchronous_fraction", "view_abc_agreement",
                "method_abc_agreement",
                "overlap_dependence_markets_with_warning",
                "per_market_timing_bounds_ms", "median_timing_bound_ms",
                "market_bootstrap_fractions"):
        assert key in payload
    assert payload["n_days"] == 1
    assert payload["pm_lead_fraction"] == 0.0
    assert payload["synchronous_fraction"] == 0.0
    assert payload["direction_fractions"]["BTC_LEAD"] == 1.0
    assert payload["view_abc_agreement"] == "3/3"
    assert payload["method_abc_agreement"] == "3/3"
    assert payload["overlap_dependence_markets_with_warning"] == 0
    assert payload["per_market_timing_bounds_ms"] == {f"m{i}": 10323
                                                      for i in range(3)}
    assert payload["median_timing_bound_ms"] == 10323.0
    assert payload["market_bootstrap_fractions"]["status"] \
        == "NOT_COMPUTED_N_BELOW_10"

    # when lags ARE above resolution the two fractions can both be 1.0 but
    # remain distinct fields
    resolved_rows = [dict(r, timing_resolved_btc_lead=True,
                          timing_ambiguity_ms=100) for r in rows]
    payload2 = runner.b1_v3_milestone_payload(
        3, 3, "run1", resolved_rows, {"equal": True},
        {"status": "NOT_COMPUTED_N_BELOW_10"}, "TIMING_ROBUST",
        str(tmp_path / "a.parquet"))
    assert payload2["raw_btc_lead_fraction"] == 1.0
    assert payload2["timing_resolved_btc_lead_fraction"] == 1.0


# ---------------------------------------------------------------- PC9
def test_pc9_b2_n001_identity_from_phase2a():
    """The first eligible std0 observation keeps the Phase 2A
    observation_id as primary key; non-Phase2A rows can never become N001."""
    observation = {"observation_id": "phase2a-obs-0001",
                   "cohort_version": COHORT_VERSION,
                   "collector_version": "phase2a_prospective_v4",
                   "coverage_pass": True, "provenance_pass": True,
                   "sanity_pass": True, "lineage_pass": True,
                   "pit_pass": True}
    eligible = p2s.b2_eligible_observations([observation])
    assert len(eligible) == 1
    payload = b2_milestone_payload(1, eligible, "run1", None)
    assert payload["first_observation_id"] == "phase2a-obs-0001"
    assert payload["primary_key"] == "observation_id (Phase 2A prospective cohort)"
    assert payload["state"] == "DESCRIPTIVE_ONLY_TINY_N"
    assert payload["timestamp_semantics"]["post_fill_anchor"] == "fill_second_end"
    assert payload["timestamp_semantics"]["same_second_ordering"] == "FORBIDDEN"

    # any failed Phase 2A gate / wrong collector / pit leak -> not eligible,
    # so it can never become N001
    for mutated in (
        dict(observation, collector_version="phase2a_prospective_v3"),
        dict(observation, coverage_pass=False),
        dict(observation, provenance_pass=False),
        dict(observation, sanity_pass=False),
        dict(observation, lineage_pass=False),
        dict(observation, pit_pass=False),
    ):
        assert p2s.b2_eligible_observations([mutated]) == []


# ---------------------------------------------------------------- PC10
def test_pc10_phase2a_frozen_definitions_unchanged():
    """The v3.1 layer changes no frozen Phase 2A / v2 / v3 vocabulary or
    milestone definition; trading/alpha claims remain impossible to output."""
    assert p2s.RESEARCH_SPEC_VERSION_V2 == "phase2b_research_v2"
    assert p2s.DIRECTION_TOLERANCE_MS == 100
    assert p2s.REFRACTORY_MS == 1000
    assert p2s.BOOTSTRAP_SEED == 20260824
    assert p2s.B1_MILESTONES == (3, 10, 20, 50, 100)
    assert p2s.B2_MILESTONES == (1, 10, 50, 100, 250, 500)
    assert p2s.ALLOWED_OVERALL_DECISIONS == (
        "EXPLORATORY_PIPELINE_READY", "EXPLORATORY_EVIDENCE_ACCUMULATING",
        "MICROSTRUCTURE_DATA_QUALITY_FAILURE")
    assert p2s.FORBIDDEN_DECISION_TOKENS == (
        "ALPHA_PROVEN", "STRATEGY_PROVEN", "READY_TO_TRADE")
    assert p2s.ALLOWED_B1_STATES == (
        "TINY_SAMPLE", "EARLY_REPLICATION", "EXPLORATORY_REPLICATION",
        "MULTI_MARKET_EVIDENCE", "INTERMEDIATE_STABILITY",
        "BROAD_EXPLORATORY_EVIDENCE", "MICROSTRUCTURE_DATA_QUALITY_FAILURE")
    assert p2s.ALLOWED_B2_STATES == (
        "INSUFFICIENT_STD0_EVENTS", "DESCRIPTIVE_ONLY_TINY_N",
        "EXPLORATORY_EVIDENCE_ACCUMULATING", "MECHANISM_CANDIDATE_IDENTIFIED")

    assert pt.RESEARCH_SPEC_VERSION_V3 == "phase2b_research_v3"
    assert pt.ALLOWED_TIMING_DECISIONS == (
        "TIMING_SEMANTICS_PASS", "TIMING_SEMANTICS_LIMITED",
        "TIMING_RESOLUTION_INSUFFICIENT", "CLOCK_BASIS_INSTABILITY",
        "TIMESTAMP_PARSER_FAILURE")
    assert pt.ALLOWED_V2_REASSESSMENTS == (
        "TIMING_ROBUST", "DIRECTIONALLY_ROBUST_BUT_LAG_UNRESOLVED",
        "NOT_TIMING_ROBUST", "INSUFFICIENT_DATA")
    # v3.1 extends the forbidden reassessment tokens additively
    assert "CONFIRMED_250MS" in pt.FORBIDDEN_REASSESSMENT_TOKENS
    assert "CONFIRMED_0_2S" in pt.FORBIDDEN_REASSESSMENT_TOKENS
    assert "EXACT_LAG" in pt.FORBIDDEN_REASSESSMENT_TOKENS
    assert "CONFIRMED_250MS" not in p2s.ALLOWED_B1_STATES
    assert "CONFIRMED_0_2S" not in p2s.ALLOWED_B1_STATES

    # the decision gate still rejects trading claims
    for forbidden in ("READY_TO_TRADE", "ALPHA_PROVEN", "STRATEGY_PROVEN"):
        with pytest.raises(ValueError):
            p2s.assert_allowed_decision([forbidden])
    with pytest.raises(ValueError):
        pt.assert_allowed_v2_reassessment("CONFIRMED_250MS")
