"""Phase 2A-Prospective AH-AQ acceptance tests."""
from __future__ import annotations
import json
from types import SimpleNamespace

from std0_quant.audit.prospective import (
    CohortManifest,build_operations_summary,classify_market_lifecycle,
    continuous_operations_status,coverage_quality,covered_calendar_days,
    create_baseline_snapshot,event_window_counts,fully_covered_observation,
    lineage_audit,schema_profile,trigger_checkpoints,verify_baseline_snapshot,
)

START=1_700_000_100_000;END=START+300_000;CID="0xc"

def events(kind="book",start=START-10_000,end=END+1_000,condition=CID):
    rows=[{"event":"connected","timestamp_ms":start}]
    if kind=="book":rows.append({"event":"subscribed","timestamp_ms":start+1,"market":condition})
    rows.append({"event":"session_end","timestamp_ms":end});return rows

def test_ah_full_lifecycle_and_partial_classification():
    full=classify_market_lifecycle(CID,START,END,[events()],[events("btc")]);assert full["lifecycle"]=="FULL_LIFECYCLE_MARKET"
    partial=classify_market_lifecycle(CID,START,END,[events(start=START+1)],[events("btc")]);assert partial["lifecycle"]=="PARTIAL_SESSION_MARKET"

def test_ai_operations_summary_counts_expected_disconnect_gap_and_sidecars():
    session=SimpleNamespace(kind="btc_ticks",events=[{"event":"connected","timestamp_ms":START},{"event":"gap_detected","source":"BINANCE_BTC","timestamp_ms":START+1,"duration_ms":6000},{"event":"disconnected","timestamp_ms":END}])
    integrity={"raw_file_count":2,"sidecar_count":2,"sidecar_missing":[],"corrupt_raw_files":[],"sha256_failures":[],"parse_errors":0}
    meta=[{"source":"binance_btc","record_count":10,"first_timestamp_ms":START,"last_timestamp_ms":END}]
    out=build_operations_summary(START,END,[session],[],integrity,meta);assert out["expected_btc5m_markets"]==1 and out["btc_disconnects"]==1 and out["btc_gap_seconds"]==6 and out["sidecar_count"]==2

def test_aj_lineage_pass_and_missing_raw_file(tmp_path):
    raw=tmp_path/"raw.ndjson";raw.write_text("{}\n")
    feature={"feature_cutoff_ms":1000,"prediction_ts_ms":2000}
    rows=[{"feature_name":"a","source_type":"binance_btc","source_timestamp_max_ms":999,"source_file":str(raw)},{"feature_name":"b","source_type":"polymarket_book","source_timestamp_max_ms":1000,"source_file":str(raw)},{"feature_name":"c","source_type":"phase1_truth","source_timestamp_max_ms":2000,"source_file":str(raw)}]
    assert lineage_audit(feature,rows)["status"]=="LINEAGE_PASS";rows[0]["source_file"]=str(tmp_path/"missing");assert lineage_audit(feature,rows)["status"].startswith("LINEAGE_FAIL")

def baseline_rows(y30=1):return [{"condition_id":"a","clean_flag":True,"exclude_reason":None,"y30":y30,"y30_horizon_eligible":True,"episode_rule_version":"v1_3sec"}]

def test_ak_historical_invariance_allows_new_row_but_not_changed_y30(tmp_path):
    snapshot=create_baseline_snapshot(baseline_rows(),tmp_path/"baseline.json");new=baseline_rows()+[{**baseline_rows()[0],"condition_id":"b"}];assert verify_baseline_snapshot(snapshot,new)["status"]=="PASS";assert verify_baseline_snapshot(snapshot,baseline_rows(0))["status"]=="FAIL"

def test_al_cohort_deduplication(tmp_path):
    store=CohortManifest(tmp_path/"cohort.json");row={"condition_id":"a","prediction_ts_ms":1,"cutoff_mode":"cutoff_1"};assert store.upsert([row])["inserted"]==1;result=store.upsert([row]);assert result["inserted"]==0 and result["duplicates"]==1 and result["total"]==1

def test_am_checkpoint_trigger_is_idempotent_and_manual_reruns(tmp_path):
    path=tmp_path/"state.json";assert trigger_checkpoints(100,path)==[100];assert trigger_checkpoints(100,path)==[];assert trigger_checkpoints(100,path,manual=100)==[100]

def test_an_calendar_days_counts_only_covered_days():
    rows=[{"calendar_date":"2026-01-01","coverage_pass":True},{"calendar_date":"2026-01-01","coverage_pass":True},{"calendar_date":"2026-01-02","coverage_pass":False}];assert covered_calendar_days(rows)==1

def test_ao_full_market_below_99_is_warning():
    assert coverage_quality([.98],[1.0])["status"]=="COVERAGE_QUALITY_WARNING";assert coverage_quality([.999],[1.0])["status"]=="PASS"

def test_ap_schema_drift_missing_expected_warns_additive_unknown_does_not():
    assert schema_profile([{"a":1,"extra":2}],{"a"})["status"]=="PASS";assert schema_profile([{"extra":2}],{"a"})["status"]=="SCHEMA_DRIFT_WARNING"

def test_aq_versioned_cohort_preserves_old_artifact(tmp_path):
    store=CohortManifest(tmp_path/"cohort.json");row={"condition_id":"a","prediction_ts_ms":1,"cutoff_mode":"cutoff_1"};store.upsert([row],"prospective_v3");store.upsert([row],"prospective_v4");payload=json.loads((tmp_path/"cohort.json").read_text());assert set(payload["cohorts"])=={"prospective_v3","prospective_v4"}

def test_completion_a_post_market_stale_does_not_change_market_window():
    out=event_window_counts([{"event":"stale_feed_detected","timestamp_ms":END+2000}],START,END)
    assert out["in_market_window"]["stale"]==0 and out["post_market_shutdown"]["stale"]==1

def test_completion_b_full_market_without_first_opposite_is_not_observation():
    assert not fully_covered_observation({"coverage_pass":True,"provenance_pass":True,"sanity_pass":True,"lineage_pass":False})

def test_completion_c_point_in_time_is_hard_failure(tmp_path):
    raw=tmp_path/"btc_ticks"/"raw.ndjson";raw.parent.mkdir();raw.write_text("{}\n")
    feature={"feature_cutoff_ms":1000,"prediction_ts_ms":2000}
    rows=[{"feature_name":"btc","source_type":"binance_btc","source_timestamp_max_ms":1001,"source_file":str(raw)},
          {"feature_name":"book","source_type":"polymarket_book","source_timestamp_max_ms":1000,"source_file":str(raw)},
          {"feature_name":"truth","source_type":"phase1_truth","source_timestamp_max_ms":2000,"source_file":str(raw)}]
    assert lineage_audit(feature,rows)["status"]=="POINT_IN_TIME_FAILURE"

def test_completion_d_primary_version_and_start_are_hard_gates(tmp_path):
    store=CohortManifest(tmp_path/"cohort.json");store.freeze_primary(start_ms=100,start_market="m")
    base={"condition_id":"a","prediction_ts_ms":200,"cutoff_mode":"cutoff_1","market_start_ms":100}
    assert store.upsert([{**base,"collector_version":"phase2a_prospective_v3"}])["excluded_version"]==1
    assert store.upsert([{**base,"market_start_ms":99,"collector_version":"phase2a_prospective_v4"}])["excluded_before_start"]==1
    assert store.upsert([{**base,"collector_version":"phase2a_prospective_v4"}])["inserted"]==1

def _supervisor(session_id,start,end):
    return SimpleNamespace(kind="live_supervisor",session_id=session_id,events=[{"event":"session_start","timestamp_ms":start},{"event":"session_end","timestamp_ms":end}])

def test_completion_e_24h_boundary_semantics():
    assert continuous_operations_status([_supervisor("a",0,86_399_000)])["status"]=="24H_OPERATIONS_PENDING_NOT_ENOUGH_RUNTIME"
    assert continuous_operations_status([_supervisor("a",0,86_400_000)])["status"]=="24H_OPERATIONS_PASS"

def test_completion_f_disconnected_sessions_are_not_stitched():
    sessions=[_supervisor("a",0,43_200_000),_supervisor("b",43_201_000,86_401_000)]
    out=continuous_operations_status(sessions)
    assert out["status"]=="24H_OPERATIONS_PENDING_NOT_ENOUGH_RUNTIME" and out["longest_continuous_runtime_seconds"]==43_200

def test_completion_g_primary_freeze_is_immutable(tmp_path):
    store=CohortManifest(tmp_path/"cohort.json");store.freeze_primary(start_ms=100,start_market="m")
    try:store.freeze_primary(start_ms=101,start_market="m")
    except RuntimeError:pass
    else:raise AssertionError("freeze mutation must fail")
