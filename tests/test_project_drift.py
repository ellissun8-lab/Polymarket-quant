"""PD1-PD10 project-governance regression gates."""
from std0_quant.audit.project_drift import (
    baseline_change_summary,coverage_gate_findings,documentation_contradictions,
    frozen_truth_findings,governance_change,milestone,milestone_status,
    retrospective_expansion,single_session_o3,
)


def test_pd1_frozen_y30_drift_detected():
    assert frozen_truth_findings("v1_3sec",3,31)[0]["category"]=="CRITICAL_RESEARCH_DRIFT"


def test_pd2_99_percent_gate_drift_detected():
    assert coverage_gate_findings(.98,.99,5000,14)[0]["severity"]=="CRITICAL"


def test_pd3_governance_change_is_not_research_drift():
    row=governance_change("Phase2B after final","exploratory parallel")
    assert row["category"]=="GOVERNANCE_CHANGE" and not row["material_research_drift"]


def test_pd4_documentation_contradiction_detected():
    assert documentation_contradictions({"old.md":"Phase2B only after FINAL"},["only after FINAL"])


def test_pd5_prospective_eligibility_versioning_passes():
    assert retrospective_expansion([{"condition_id":"x","session_started_at_ms":9,
                                     "primary_cohort_included":False}],10)==[]


def test_pd6_retroactive_cohort_expansion_detected():
    assert retrospective_expansion([{"condition_id":"x","session_started_at_ms":9,
                                     "primary_cohort_included":True}],10)==["x"]


def test_pd7_evolving_ledger_new_rows_not_false_positive():
    before=[{"condition_id":"a","y30":0}];after=before+[{"condition_id":"b","y30":1}]
    result=baseline_change_summary(before,after,["y30"])
    assert result["status"]=="PASS" and result["new_rows"]==1


def test_pd8_historical_row_modification_detected():
    result=baseline_change_summary([{"condition_id":"a","y30":0}],
                                   [{"condition_id":"a","y30":1}],["y30"])
    assert result["status"]=="FAIL" and result["changed_historical_ids"]==["a"]


def test_pd9_o3_never_stitches_sessions():
    result=single_session_o3([{"session_id":"a","start_ms":0,"end_ms":50_000_000},
                              {"session_id":"b","start_ms":60_000_000,"end_ms":100_000_000}])
    assert result["runtime_seconds"]==50_000 and not result["stitched"] and result["status"]=="PENDING"


def test_pd10_milestone_statuses_are_deterministic():
    assert milestone_status(3,10)=="ACCUMULATING" and milestone_status(10,10)=="PASS"
    value=milestone("M10","B1","replication",3,10,"ACCUMULATING",blocking=False,
                    evidence=None,version="v3",next_action="accumulate")
    assert value["non_blocking"] and value["status"]=="ACCUMULATING"
