"""EV1-EV9 prospective eligibility-version governance gates."""
import json

import pytest

from std0_quant.audit.eligibility_policy import (
    COLLECTOR_VERSION, COVERAGE_THRESHOLD, ELIGIBILITY_POLICY_VERSION,
    PRIMARY_COHORT_RETROACTIVE_RECLASSIFICATION, eligibility_decision,
    eligibility_migration_audit, freeze_eligibility_policy,
    o3_candidate_status, primary_policy_decision,
)
from std0_quant.collectors.network_stability import NETWORK_ENGINEERING_FIX_VERSION


def row(**updates):
    base={"condition_id":"c","market_start_ms":2_000,
          "session_started_at_ms":1_000,"collector_version":COLLECTOR_VERSION,
          "engineering_fix_version":NETWORK_ENGINEERING_FIX_VERSION,
          "legacy_lifecycle":"FULL_LIFECYCLE_MARKET",
          "lifecycle":"FULL_LIFECYCLE_MARKET",
          "btc_coverage_pct":.99,"book_coverage_pct":.99}
    return {**base,**updates}


def frozen(tmp_path):
    return freeze_eligibility_policy(tmp_path/"freeze.json","s1",1_000,
                                     NETWORK_ENGINEERING_FIX_VERSION)


def test_ev1_retrospective_cohort_expansion_forbidden():
    old=row(session_started_at_ms=500,legacy_lifecycle="PARTIAL_SESSION_MARKET")
    audit=eligibility_migration_audit([old])
    assert audit["market_count_changed"]==1
    assert audit["changed_markets"][0]["transition"]=="EXCLUDED -> ELIGIBLE"
    assert audit["changed_markets"][0]["primary_cohort_action"]=="PRESERVE_ORIGINAL_DECISION"
    assert audit["primary_cohort_retroactive_reclassification"]==PRIMARY_COHORT_RETROACTIVE_RECLASSIFICATION=="FORBIDDEN"


def test_ev2_effective_from_session_is_immutable(tmp_path):
    value=frozen(tmp_path)
    assert value["effective_from_session_id"]=="s1" and value["effective_from_timestamp_ms"]==1_000
    reloaded=freeze_eligibility_policy(tmp_path/"freeze.json","s2",2_000,NETWORK_ENGINEERING_FIX_VERSION)
    assert reloaded["effective_from_session_id"]=="s1" and reloaded["effective_from_timestamp_ms"]==1_000


def test_ev3_pre_fix_session_cannot_count(tmp_path):
    assert not primary_policy_decision(row(session_started_at_ms=999),frozen(tmp_path))["primary_cohort_included"]


def test_ev4_post_fix_market_can_count(tmp_path):
    assert primary_policy_decision(row(),frozen(tmp_path))["primary_cohort_included"]


def test_ev5_collector_version_remains_prospective_v4():
    assert COLLECTOR_VERSION=="phase2a_prospective_v4"
    assert eligibility_decision(row(collector_version="phase2a_prospective_v5"),policy_version=ELIGIBILITY_POLICY_VERSION)["status"]=="EXCLUDED"


def test_ev6_engineering_fix_version_recorded(tmp_path):
    assert frozen(tmp_path)["engineering_fix_version"]=="network_stability_fix_v1"


def test_ev7_coverage_threshold_remains_99_percent():
    assert COVERAGE_THRESHOLD==.99
    assert eligibility_decision(row(btc_coverage_pct=.989999),policy_version=ELIGIBILITY_POLICY_VERSION)["status"]=="EXCLUDED"


def test_ev8_o3_candidate_begins_only_post_fix(tmp_path):
    freeze=frozen(tmp_path)
    sessions=[{"session_id":"old","started_at_ms":999,"engineering_fix_version":"recorder_reliability_fix_v1"}]
    assert o3_candidate_status(sessions,freeze,2_000)["status"]=="O3_CANDIDATE_NOT_STARTED"
    sessions.append({"session_id":"s1","started_at_ms":1_000,"engineering_fix_version":NETWORK_ENGINEERING_FIX_VERSION})
    assert o3_candidate_status(sessions,freeze,2_000)["runtime_seconds"]==1


def test_ev9_restart_session_resets_o3(tmp_path):
    freeze=frozen(tmp_path);sessions=[
        {"session_id":"s1","started_at_ms":1_000,"ended_at_ms":50_000,"engineering_fix_version":NETWORK_ENGINEERING_FIX_VERSION},
        {"session_id":"s2","started_at_ms":60_000,"engineering_fix_version":NETWORK_ENGINEERING_FIX_VERSION}]
    status=o3_candidate_status(sessions,freeze,61_000)
    assert status["session_id"]=="s2" and status["runtime_seconds"]==1 and status["resets"]==1
