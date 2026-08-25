"""Run offline Phase 2B exploratory research over closed prospective_v4 files.

v3 (``phase2b_research_v3``): TIMING FIRST, REPLICATION SECOND.  Every run
writes versioned, immutable artifacts (never overwrites a previous run),
reuses unchanged raw files through a version-separated processed-file SHA
manifest (v2 and v3 caches cannot collide), audits timestamp semantics and
clock bases BEFORE interpreting any subsecond lead-lag number, replicates the
per-market lead-lag table across ALL closed SHA-verified full-lifecycle v4
markets, and leaves the recorder, cohort, raw files and all Phase 2A gates
untouched.  v2 reports remain immutable; this runner never rewrites them.

v3.1 additions (same research spec version - no research-definition change):
strict three-level interpretation hierarchy per market (L1 direction / L2
magnitude only when the peak is at/above the timing-resolution bound / L3
grid peak descriptive only), an interpretation guard that rejects unsupported
magnitude wording in supported conclusions, the recorder reliability
engineering gate (collector version unchanged), and the section-15 M10
evidence-accumulation payload fields.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"src"))
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from std0_quant.audit.prospective import CohortManifest,atomic_json
from std0_quant.audit.coverage import load_sessions
from std0_quant.config import load_settings,resolve_path
from std0_quant.research.phase2b import (
    COHORT_VERSION,GRIDS_MS,PRIMARY_COLLECTOR_VERSION,RESEARCH_SPEC_VERSION,
    add_market_features,assert_timeline_order,build_grid,cross_correlations,
    conservative_fill_window,conservative_markout,event_response_curves,
    lagged_regressions,normalized_book_state,up_equivalent,valid_book_row,
)
from std0_quant.research.phase2b_stability import (
    B1_MILESTONES,B2_MILESTONES,ECONOMIC_HORIZONS_MS,RESEARCH_SPEC_VERSION_V2,
    TEST_BASELINE_COUNT,b1_maturity_state,
    b2_eligible_observations,b2_milestone_payload,b2_observation_state,
    cache_reusable,cluster_awareness,collect_shock_response_values,
    combine_value_maps,dependence_sensitivity,is_full_lifecycle_v4_market,
    emit_milestone_once,equal_market_summary,file_sha256,frozen_invariant_check,
    lifecycle_stability,latency_summary,load_state,market_bootstrap,
    per_market_lead_lag_row,pooled_peak_lag,response_stats_from_values,
    non_overlapping_anchors,save_state,shock_anchor_rows,shock_bucket_stability,assert_allowed_decision,
    next_b1_milestone,next_b2_milestone,
)
from std0_quant.research import phase2b_timing as pt
from std0_quant.collectors.recorder_reliability import detect_recorder_hotfix

TEST_BASELINE_COUNT_V3 = 357  # re-run for real before any v3 development (spec 41)


def read_rows(path:Path):
    with path.open("r",encoding="utf-8") as source:
        for line_no,line in enumerate(source,1):
            if line.strip():yield line_no,json.loads(line)


def latest_json(directory:Path,pattern:str)->Path|None:
    files=sorted(directory.glob(pattern));return files[-1] if files else None


def validated_markets(reports:Path)->list[dict]:
    by_condition={}
    for path in sorted(reports.glob("v4_full_lifecycle_validation_*.json")):
        row=json.loads(path.read_text(encoding="utf-8"))
        if row.get("result")=="PASS" and row.get("collector_version")==PRIMARY_COLLECTOR_VERSION:
            row["validation_artifact"]=str(path);by_condition[row["condition_id"]]=row
    ops_path=latest_json(reports,"live_operations_24h_*.json")
    if ops_path:
        operations=json.loads(ops_path.read_text(encoding="utf-8"))
        for market in operations.get("markets",[]):
            if not is_full_lifecycle_v4_market(market):continue
            cid=str(market["condition_id"])
            if cid in by_condition:continue
            candidates=sorted((reports/"coverage").glob(f"{market['slug']}_*.json"),reverse=True)
            coverage=next((json.loads(path.read_text(encoding="utf-8"))|{"validation_artifact":str(path)} for path in candidates
                           if json.loads(path.read_text(encoding="utf-8")).get("book_files") and json.loads(path.read_text(encoding="utf-8")).get("btc_files")),None)
            if coverage:
                by_condition[cid]={**market,"market_start_ms":coverage["market_start_ms"],"market_end_ms":coverage["market_end_ms"],"raw_files":coverage["book_files"]+coverage["btc_files"],"result":"PASS","validation_artifact":coverage["validation_artifact"]}
    return sorted(by_condition.values(),key=lambda r:int(r["market_start_ms"]))


def build_market_rows(market:dict)->tuple[list[dict],dict]:
    """v3 timeline rows add source_event_ts_ms (BTC T), frame_ts_ms (BTC E /
    PM payload frame timestamp) and is_frame_child (batched price_changes[]
    children inherit the parent frame timestamp)."""
    start,end=int(market["market_start_ms"]),int(market["market_end_ms"]);timeline=[]
    counts={"btc_raw":0,"btc_v4":0,"book_raw":0,"book_v4":0,"book_valid":0,
            "book_invalid":0,"book_uninitialized":0,"book_stale":0,"book_desynced":0,
            "pm_frame_children":0}
    for name in market["raw_files"]:
        path=Path(name);source_kind="BTC" if "btc_ticks" in path.parts else "PM"
        for line_no,row in read_rows(path):
            event_ts=row.get("exchange_timestamp_ms")
            if event_ts is None or not start<=int(event_ts)<end:continue
            if source_kind=="BTC":
                counts["btc_raw"]+=1
                if row.get("collector_version")!=PRIMARY_COLLECTOR_VERSION:continue
                counts["btc_v4"]+=1
                timeline.append({"research_spec_version":pt.RESEARCH_SPEC_VERSION_V3,"time_basis":"exchange_timestamp_ms","market_start_ms":start,"market_end_ms":end,"condition_id":market["condition_id"],"slug":market["slug"],"source":"BTC","event_timestamp_ms":int(event_ts),"exchange_timestamp_ms":int(event_ts),"receive_timestamp_ms":int(row["receive_timestamp_ms"]),"event_type":"trade","source_event_ts_ms":int(event_ts),"frame_ts_ms":int(row.get("event_timestamp_ms") or event_ts),"is_frame_child":False,"btc_price":float(row["price"]),"btc_size":float(row["size"]),"btc_trade_id":int(row["trade_id"]),"btc_buyer_is_maker":row.get("buyer_is_maker"),"pm_token":None,"pm_outcome":None,"pm_best_bid":None,"pm_best_ask":None,"pm_mid":None,"pm_spread":None,"pm_bid_depth_top3":None,"pm_ask_depth_top3":None,"pm_obi_top3":None,"book_valid":None,"session_id":row.get("session_id"),"connection_id":row.get("connection_id"),"collector_version":row.get("collector_version"),"schema_version":row.get("schema_version"),"raw_file":str(path),"raw_line":line_no,"raw_message_ref":None})
            else:
                counts["book_raw"]+=1
                if row.get("collector_version")==PRIMARY_COLLECTOR_VERSION:counts["book_v4"]+=1
                if not valid_book_row(row):
                    counts["book_invalid"]+=1;status=str(row.get("book_state_status") or "").lower()
                    if status in {"uninitialized","stale","desynced"}:counts[f"book_{status}"]+=1
                    continue
                counts["book_valid"]+=1;state=normalized_book_state(row)
                child=row.get("applied_change") is not None
                counts["pm_frame_children"]+=int(child)
                timeline.append({"research_spec_version":pt.RESEARCH_SPEC_VERSION_V3,"time_basis":"exchange_timestamp_ms","market_start_ms":start,"market_end_ms":end,"condition_id":market["condition_id"],"slug":market["slug"],"source":"PM","event_timestamp_ms":int(event_ts),"exchange_timestamp_ms":int(event_ts),"receive_timestamp_ms":int(row["receive_timestamp_ms"]),"event_type":row.get("event_type"),"source_event_ts_ms":None,"frame_ts_ms":int(event_ts),"is_frame_child":child,"btc_price":None,"btc_size":None,"btc_trade_id":None,"btc_buyer_is_maker":None,"pm_token":row.get("token_id"),"pm_outcome":row.get("outcome"),**state,"book_valid":True,"session_id":row.get("session_id"),"connection_id":row.get("connection_id"),"collector_version":row.get("collector_version"),"schema_version":row.get("schema_version"),"raw_file":str(path),"raw_line":line_no,"raw_message_ref":row.get("raw_message_ref")})
    timeline.sort(key=lambda r:(r["event_timestamp_ms"],r["receive_timestamp_ms"],r["source"],r["raw_line"]));assert_timeline_order(timeline)
    return timeline,counts


def basis_frames(frame:pd.DataFrame,basis:str)->tuple[pd.DataFrame,pd.DataFrame]:
    column="event_timestamp_ms" if basis=="exchange" else "receive_timestamp_ms"
    btc=frame[frame.source=="BTC"][[column,"btc_price"]].rename(columns={column:"event_timestamp_ms"}).sort_values("event_timestamp_ms")
    book_cols=[column,"pm_mid","pm_best_bid","pm_best_ask","pm_spread","pm_bid_depth_top3","pm_ask_depth_top3","pm_obi_top3"]
    book=frame[frame.source=="PM"][book_cols].rename(columns={column:"event_timestamp_ms"}).sort_values("event_timestamp_ms")
    return btc,book


def market_latencies(frame:pd.DataFrame)->tuple[dict,dict]:
    btc=frame[frame.source=="BTC"];pm=frame[frame.source=="PM"]
    btc_lat=latency_summary((btc["receive_timestamp_ms"]-btc["exchange_timestamp_ms"]).tolist())
    clob_lat=latency_summary((pm["receive_timestamp_ms"]-pm["exchange_timestamp_ms"]).tolist())
    return btc_lat,clob_lat


def load_or_build_timeline(market:dict,cache_dir:Path,state:dict,run_id:str)->tuple[pd.DataFrame,dict,bool]:
    """v3 cache: key includes raw SHAs + collector version + research spec +
    timing semantics version; v2 caches can never satisfy a v3 lookup."""
    cid=str(market["condition_id"])
    file_shas={str(name):file_sha256(name) for name in market["raw_files"]}
    key=pt.market_cache_key_v3(list(file_shas.items()))
    cache_path=cache_dir/f"{cid}_v3_{key[:16]}.parquet"
    entry=state.get("market_cache_v3",{}).get(cid)
    if (cache_reusable(entry,file_shas,pt.RESEARCH_SPEC_VERSION_V3,cache_path)
            and (entry or {}).get("timing_semantics_version")==pt.TIMING_SEMANTICS_VERSION):
        frame=pd.read_parquet(cache_path)
        counts=dict(entry["counts"]);counts["condition_id"]=cid
        for path,digest in file_shas.items():state["processed_files"][path]={"sha256":digest,"last_seen_run_id":run_id}
        return frame,counts,True
    rows,counts=build_market_rows(market);frame=pd.DataFrame(rows)
    pq.write_table(pa.Table.from_pandas(frame,preserve_index=False),cache_path,compression="zstd")
    state.setdefault("market_cache_v3",{})[cid]={"research_spec_version":pt.RESEARCH_SPEC_VERSION_V3,
                                                 "timing_semantics_version":pt.TIMING_SEMANTICS_VERSION,
                                                 "cache_key":key,
                                                 "file_shas":file_shas,"timeline_path":str(cache_path),
                                                 "counts":{k:v for k,v in counts.items() if k!="condition_id"}}
    for path,digest in file_shas.items():state["processed_files"][path]={"sha256":digest,"last_seen_run_id":run_id}
    counts=dict(counts);counts["condition_id"]=cid
    return frame,counts,False


def frame_summary(frame:pd.DataFrame,fields:list[str])->dict:
    result={}
    for name in fields:
        values=frame[name].dropna() if name in frame else pd.Series(dtype=float)
        result[name]={"n":int(len(values)),"mean":float(values.mean()) if len(values) else None,"median":float(values.median()) if len(values) else None,"p05":float(values.quantile(.05)) if len(values) else None,"p95":float(values.quantile(.95)) if len(values) else None}
    return result


def materialize_std0_context(eligible:list[dict],grids:pd.DataFrame,settings,derived:Path,reports:Path,run_id:str)->Path|None:
    if not eligible:return None
    ids=sorted({row["condition_id"] for row in eligible});observations={row["condition_id"]:row for row in eligible}
    fills=pq.read_table(resolve_path(settings,"normalized")/"fills.parquet",filters=[("condition_id","in",ids)]).to_pylist();base=grids[grids.grid_ms==1000].sort_values("timestamp_ms");output=[]
    for fill in fills:
        cid=fill["condition_id"];market=base[base.condition_id==cid];window=conservative_fill_window(int(fill["timestamp_ms"]));pre=market[market.timestamp_ms<=window["pre_context_cutoff_ms"]]
        prior=pre.iloc[-1] if len(pre) else None;direction,up_price=up_equivalent(fill["side"],fill["outcome"],float(fill["price"]));row={"research_spec_version":pt.RESEARCH_SPEC_VERSION_V3,"observation_id":observations[cid].get("observation_id"),"condition_id":cid,"slug":fill.get("slug"),"fill_id":fill.get("fill_id"),"std0_fill_timestamp_ms":int(fill["timestamp_ms"]),**window,"side":fill["side"],"outcome":fill["outcome"],"price":float(fill["price"]),"size":float(fill["size"]),"direction":direction,"up_equiv_price":up_price,"pre_btc_price":float(prior.btc_price) if prior is not None and pd.notna(prior.btc_price) else None,"pre_pm_bid":float(prior.pm_best_bid) if prior is not None and pd.notna(prior.pm_best_bid) else None,"pre_pm_ask":float(prior.pm_best_ask) if prior is not None and pd.notna(prior.pm_best_ask) else None,"pre_pm_mid":float(prior.pm_mid) if prior is not None and pd.notna(prior.pm_mid) else None,"pre_pm_spread":float(prior.pm_spread) if prior is not None and pd.notna(prior.pm_spread) else None,"pre_btc_source_timestamp_ms":int(prior.btc_source_timestamp_ms) if prior is not None and pd.notna(prior.btc_source_timestamp_ms) else None,"pre_book_source_timestamp_ms":int(prior.book_source_timestamp_ms) if prior is not None and pd.notna(prior.book_source_timestamp_ms) else None,"coverage_pass":bool(prior is not None and pd.notna(prior.btc_price) and bool(prior.book_valid)),"pit_pass":bool(prior is not None and (pd.isna(prior.btc_source_timestamp_ms) or int(prior.btc_source_timestamp_ms)<=window["pre_context_cutoff_ms"]) and (pd.isna(prior.book_source_timestamp_ms) or int(prior.book_source_timestamp_ms)<=window["pre_context_cutoff_ms"])),"lineage_pass":bool(observations[cid].get("lineage_pass")),"collector_version":PRIMARY_COLLECTOR_VERSION,"cohort_version":COHORT_VERSION,"feature_artifact":observations[cid].get("feature_artifact"),"provenance_artifact":observations[cid].get("provenance_artifact")}
        for horizon in (1,5,30,60):
            future=market[market.timestamp_ms>=window["post_markout_anchor_ms"]+horizon*1000];future_mid=float(future.iloc[0].pm_mid) if len(future) and pd.notna(future.iloc[0].pm_mid) else None;row[f"post_pm_mid_{horizon}s"]=future_mid;row[f"markout_{horizon}s"]=conservative_markout(fill["side"],fill["outcome"],float(fill["price"]),future_mid)
        output.append(row)
    path=derived/f"std0_fill_context_{run_id}.parquet";pq.write_table(pa.Table.from_pylist(output),path,compression="zstd")
    for point in B2_MILESTONES:
        if len(eligible)>=point:
            payload=b2_milestone_payload(point,eligible,run_id,str(path))
            text=(f"# Phase 2B B2-N{point:03d}\n\n- State: **{payload['state']}**\n"
                  f"- Observations: {payload['n_observations']}\n- Primary key: observation_id\n"
                  f"- Post-fill anchor: fill_second_end; same-second ms ordering forbidden.\n"
                  f"- EXPLORATORY ONLY - NO REAL TRADING.\n")
            emit_milestone_once(reports,f"phase2b_b2_n{point:03d}",run_id,payload,text)
    return path


def fmt(value):
    if value is None:return "n/a"
    if isinstance(value,float):return f"{value:.4f}"
    return str(value)


def half_split_stats(values:list[float])->dict:
    arr=np.asarray([float(v) for v in values if v is not None],dtype=float)
    if arr.size<4:return {"p50_first_half_ms":None,"p50_second_half_ms":None}
    mid=arr.size//2
    return {"p50_first_half_ms":float(np.median(arr[:mid])),
            "p50_second_half_ms":float(np.median(arr[mid:]))}


def v2_report_artifact(reports:Path)->dict|None:
    for path in sorted(reports.glob("phase2b_research_*.json"),reverse=True):
        try:payload=json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError,OSError):continue
        if payload.get("research_spec_version")==RESEARCH_SPEC_VERSION_V2:
            rows=payload.get("per_market_lead_lag",{}).get("rows") or []
            return {"artifact":str(path),"run_id":payload.get("run_id"),
                    "n_markets":payload.get("data_maturity",{}).get("n_complete_markets"),
                    "method_a_lag_ms":rows[0].get("method_a_lag_ms") if rows else None,
                    "direction":rows[0].get("direction") if rows else None}
    return None


V2_RESULT_LABELS={"TIMING_ROBUST":"V2_RESULT_TIMING_ROBUST",
                  "DIRECTIONALLY_ROBUST_BUT_LAG_UNRESOLVED":"V2_RESULT_DIRECTIONALLY_ROBUST_BUT_LAG_UNRESOLVED",
                  "NOT_TIMING_ROBUST":"V2_RESULT_NOT_TIMING_ROBUST",
                  "INSUFFICIENT_DATA":"V2_REASSESSMENT_INSUFFICIENT_DATA"}


def b1_v3_milestone_payload(milestone:int,n_markets:int,run_id:str,rows:list[dict],
                            equal_market:dict,bootstrap:dict,reassessment:str,
                            artifact_path:str)->dict:
    with_estimate=[r for r in rows if r.get("method_a_lag_ms") is not None]
    directions=Counter(r["direction"] for r in with_estimate)
    resolved=[r for r in with_estimate if r.get("timing_resolved_btc_lead")]
    ambiguities=[r["timing_ambiguity_ms"] for r in with_estimate if r.get("timing_ambiguity_ms") is not None]
    lags=[r["method_a_lag_ms"] for r in with_estimate]
    n_days=len({r.get("calendar_date") for r in with_estimate if r.get("calendar_date")})
    view_agree=sum(1 for r in with_estimate if r.get("clock_views_agree"))
    method_consistent=sum(1 for r in with_estimate if r.get("method_agreement")=="METHOD_CONSISTENT_MARKET")
    dep_warn=sum(1 for r in with_estimate if r.get("dependence_sensitivity_warning"))
    bounds={r["slug"]:r.get("timing_ambiguity_ms") for r in with_estimate}
    bound_values=[v for v in bounds.values() if v is not None]
    return {"research_spec_version":pt.RESEARCH_SPEC_VERSION_V3,
            "milestone":f"B1-M{milestone}","run_id":run_id,"n_markets":int(n_markets),
            "n_days":n_days,
            "evidence_state":b1_maturity_state(n_markets),
            "direction_counts":dict(directions),
            "direction_fractions":{k:directions.get(k,0)/len(with_estimate) for k in ("BTC_LEAD","PM_LEAD","SYNCHRONOUS","UNKNOWN")} if with_estimate else None,
            "timing_resolved_direction_counts":{"BTC_LEAD":len(resolved)},
            "raw_btc_lead_fraction":directions.get("BTC_LEAD",0)/len(with_estimate) if with_estimate else None,
            "pm_lead_fraction":directions.get("PM_LEAD",0)/len(with_estimate) if with_estimate else None,
            "synchronous_fraction":directions.get("SYNCHRONOUS",0)/len(with_estimate) if with_estimate else None,
            "timing_resolved_btc_lead_fraction":len(resolved)/len(with_estimate) if with_estimate else None,
            "view_abc_agreement":f"{view_agree}/{len(with_estimate)}" if with_estimate else None,
            "method_abc_agreement":f"{method_consistent}/{len(with_estimate)}" if with_estimate else None,
            "overlap_dependence_markets_with_warning":dep_warn,
            "per_market_timing_bounds_ms":bounds,
            "median_timing_bound_ms":float(np.median(bound_values)) if bound_values else None,
            "median_peak_lag_ms":float(np.median(lags)) if lags else None,
            "median_timing_ambiguity_ms":float(np.median(ambiguities)) if ambiguities else None,
            "market_bootstrap_fractions":pt.market_bootstrap_fractions(rows),
            "equal_market_weighted":equal_market,"bootstrap":bootstrap,
            "v2_reassessment":reassessment,
            "per_market_lead_lag_artifact":artifact_path,
            "note":("BTC_LEAD and TIMING-RESOLVED BTC_LEAD are different things; effective "
                    "evidence is the market count, not the shock count; the timing-resolved "
                    "fraction is always reported separately from the raw lead fraction"),
            "no_real_trading":True,"causal_claim":False,"immutable":True}


def markdown_timing_audit(report:dict)->str:
    reg=report["timestamp_registry"];btc_d=report["btc_timing_diagnostics"]
    clob_d=report["clob_timing_diagnostics"];res=report["minimum_resolvable_lag"]
    repl=report["per_market_replication"];agree=report["cross_clock_agreement"]
    b1=report["b1_milestones"];reassess=report["v2_reassessment"]
    lines=["# Phase 2B-Research v3 Timing & Replication Audit","",
           "**RESEARCH ONLY - NO REAL TRADING**","",
           "MEASURE THE CLOCK BEFORE MEASURING THE LAG.","",
           f"- Research spec: `{report['research_spec_version']}`; timing semantics: `{report['timing_semantics_version']}`; run: `{report['run_id']}`",
           f"- Pre-development pytest baseline: **{report['test_baseline']['pre_development_passed']} passed** (re-run for real).","",
           "## A. Timestamp Registry",
           f"- Registry: `{reg['timing_semantics_version']}`, {len(reg['entries'])} fields; frozen before viewing new-market aggregates: **{reg['frozen_before_viewing_new_market_aggregates']}**."]
    for e in reg["entries"]:
        lines.append(f"- `{e['source']}.{e['field_name']}` -> {e['semantic_name']}: **{e['timestamp_class']}** / {e['timestamp_granularity']} / trust {e['trust_level']} / cross-source ordering {e['can_order_cross_source']}.")
    lines+=[f"- Local clock: **{reg['local_clock']['status']}** (offset never measured; retroactive correction FORBIDDEN).","",
            "## B. Raw Timestamp Semantics",
            "- BTC `exchange_timestamp_ms` = Binance trade time T = SOURCE_EVENT_TIME (HIGH, event-level).",
            "- PM `exchange_timestamp_ms` = CLOB per-frame server timestamp = SOURCE_FRAME_TIME (MEDIUM, FRAME_LEVEL): batched `price_changes[]` children inherit the parent frame timestamp; proven NOT last-trade time; never called exchange trade time.",
            "- Both `receive_timestamp_ms` = LOCAL_RECEIVE_TIME on one local clock (BTC burst-quantized by the shared asyncio loop).",
            "- std0 fills: SECOND_LEVEL; same-second ms ordering forbidden.","",
            "## C. BTC Timing Diagnostics",
            f"- receive minus T (n={btc_d['receive_minus_source']['n']}): p50/p90/p99/max = {fmt(btc_d['receive_minus_source']['p50_ms'])}/{fmt(btc_d['receive_minus_source']['p90_ms'])}/{fmt(btc_d['receive_minus_source']['p99_ms'])}/{fmt(btc_d['receive_minus_source']['max_ms'])} ms; MAD {fmt(btc_d['receive_minus_source']['mad_ms'])} ms; fractions >250/>500/>1000/>5000ms = {fmt(btc_d['receive_minus_source']['frac_gt_250ms'])}/{fmt(btc_d['receive_minus_source']['frac_gt_500ms'])}/{fmt(btc_d['receive_minus_source']['frac_gt_1000ms'])}/{fmt(btc_d['receive_minus_source']['frac_gt_5000ms'])}.",
            f"- Interpretation: {btc_d['interpretation']['quantity']} (network-latency claim: {btc_d['interpretation']['network_latency_claim']}).",
            f"- Offset pattern: **{btc_d['systematic_offset']['pattern']}** - {btc_d['systematic_offset'].get('hypothesis','')}",
            f"- p50 first/second half: {fmt(btc_d['p50_first_half_ms'])}/{fmt(btc_d['p50_second_half_ms'])} ms; E-T p50 = {fmt(btc_d['e_minus_t_p50_ms'])} ms.","",
            "## D. CLOB Timing Diagnostics",
            f"- receive minus frame timestamp (n={clob_d['receive_minus_frame']['n']}): p50/p90/p99/max = {fmt(clob_d['receive_minus_frame']['p50_ms'])}/{fmt(clob_d['receive_minus_frame']['p90_ms'])}/{fmt(clob_d['receive_minus_frame']['p99_ms'])}/{fmt(clob_d['receive_minus_frame']['max_ms'])} ms; MAD {fmt(clob_d['receive_minus_frame']['mad_ms'])} ms; fractions >250/>500/>1000/>5000ms = {fmt(clob_d['receive_minus_frame']['frac_gt_250ms'])}/{fmt(clob_d['receive_minus_frame']['frac_gt_500ms'])}/{fmt(clob_d['receive_minus_frame']['frac_gt_1000ms'])}/{fmt(clob_d['receive_minus_frame']['frac_gt_5000ms'])}.",
            f"- Interpretation: {clob_d['interpretation']['quantity']} (network-latency claim: {clob_d['interpretation']['network_latency_claim']}) - this is NOT event network latency; frame timestamps batch server-side and STATE_AGE is tracked separately.",
            f"- p50 first/second half: {fmt(clob_d['p50_first_half_ms'])}/{fmt(clob_d['p50_second_half_ms'])} ms.","",
            "## E. Event-Type Breakdown (frame-basis delay)"]
    for row in report["event_type_breakdown"]:
        lines.append(f"- {row['event_type']} ({row['source']}): n={row['n']}, p50 {fmt(row['p50_ms'])} ms, p99-p50 {fmt(row['p99_minus_p50_ms'])} ms, >1s {fmt(row['frac_gt_1000ms'])}.")
    for row in report["by_market"]:
        lines.append(f"- market {'...' + str(row['market'])[-8:]} ({row['source']}): p50 {fmt(row['p50_ms'])} ms, p99-p50 {fmt(row['p99_minus_p50_ms'])} ms.")
    lines+=[f"- By market/connection and market/session rows: {len(report['by_market_connection'])}/{len(report['by_market_session'])} (JSON report).","",
            "## F. State Age"]
    for row in report["state_age_diagnostics"]:
        lines.append(f"- {row['slug']}: info age p50/p90/p99 = {fmt(row['pm_state_info_age_p50_ms'])}/{fmt(row['pm_state_info_age_p90_ms'])}/{fmt(row['pm_state_info_age_p99_ms'])} ms; availability carry-forward p50 {fmt(row['pm_state_availability_age_p50_ms'])} ms; valid-but-not-fresh buckets {row['n_valid_not_fresh']}.")
    lines+=["- VALID is not FRESH: a still-VALID book state can carry seconds-old information; that age is never read as websocket network latency.","",
            "## G. Local Clock Health",
            f"- {report['local_clock_health']['status']}; correction applied: {report['local_clock_health']['correction_applied']}; retroactive correction: {report['local_clock_health']['retroactive_correction']}.","",
            "## H. Cross-source Comparability"]
    comp=report["cross_source_comparability"]
    lines=[*lines,
           f"- source-time basis: **{comp['source_time_basis']}**; receive-time basis: **{comp['receive_time_basis']}**; CAN_COMPARE_BTC_PM = **{comp['can_compare_btc_pm']}**.",
           f"- {comp['note']}","",
           "## I. Minimum Resolvable Lag",
           f"- Rule: {res['rule']}",
           f"- Overall bound: **{fmt(res['overall_ms'])} ms**; per-market bounds: " + ", ".join(f"{k}={fmt(v)}" for k,v in res["per_market_ms"].items()) + ".",
           f"- A 250ms peak under a 1000ms bound is BELOW_TIMING_RESOLUTION; never report 'exact lag = 250ms'.","",
           "## J. Source-time Lead-Lag (VIEW_A)",
           f"- Status: {report['source_time_lead_lag']['status']} - descriptive only; PM source timestamps are frame times, not event times."]
    for row in report["source_time_lead_lag"]["rows"]:
        lines.append(f"- {row['slug']}: METHOD_A {fmt(row['method_a_lag_ms'])}ms -> **{row['direction']}**; METHOD_B {fmt(row['method_b_lag_ms'])}ms; METHOD_C {fmt(row['method_c_lag_ms'])}ms; agreement {row['method_agreement']}.")
    lines+=["","## K. Receive-time Lead-Lag (VIEW_B)",
            "- Both streams stamped by one local clock (burst-quantized); lag includes the transport-path difference."]
    for row in report["receive_time_lead_lag"]["rows"]:
        lines.append(f"- {row['slug']}: METHOD_A {fmt(row['method_a_lag_ms'])}ms -> **{row['direction']}**; METHOD_B {fmt(row['method_b_lag_ms'])}ms; METHOD_C {fmt(row['method_c_lag_ms'])}ms; agreement {row['method_agreement']}.")
    lines+=["","## L. Availability-time Lead-Lag (VIEW_C)",
            f"- No-backdating check: **{report['availability_time_lead_lag']['no_backdating']}** across {report['availability_time_lead_lag']['n_markets']} markets.",
            f"- VIEW_C equivalence note: {report['availability_time_lead_lag']['equivalence_note']}"]
    for row in report["availability_time_lead_lag"]["rows"]:
        lines.append(f"- {row['slug']}: METHOD_A {fmt(row['method_a_lag_ms'])}ms -> **{row['direction']}** (timing trust {row['timing_trust_tier']}, resolution {row['resolution_status']}, coarse bucket {row['coarse_lag_bucket']}).")
    lines+=["","## M. Cross-clock Agreement",
            f"- Status: **{agree['status']}**; all three views agree on {agree['all_three_agree']}/{agree['n_markets']} markets; pairwise {agree['pairwise']}.","",
            "## N. Dependence Sensitivity"]
    dep=report["dependence_sensitivity"]
    lines=[*lines,
           f"- OVERLAPPING vs NON_OVERLAPPING_1S retained on every market; warnings on {dep['markets_with_warning']}/{dep['n_markets']} markets.",
           f"- Pooled: warning={dep['pooled_warning']}, sign_flip={dep['pooled_sign_flip']}, max_rel_diff={fmt(dep['pooled_max_rel_diff'])}.","",
           "## O. Per-market Replication",
           f"- Markets: {repl['n_markets']}; direction counts {repl['direction_counts']}; coarse buckets {repl['coarse_bucket_counts']}.",
           f"- raw_btc_lead_fraction = **{fmt(repl['raw_btc_lead_fraction'])}**; timing_resolved_btc_lead_fraction = **{fmt(repl['timing_resolved_btc_lead_fraction'])}**.",
           f"- Cluster awareness: {repl['cluster_awareness']} (effective evidence ~ market count, not shock count).","",
           "## P. B1 Milestones",
           f"- Evidence state: **{b1['state']}**; n_markets={b1['n_markets']}; attained v3 milestones {b1['attained']}; next {fmt(b1['next'])}.",
           f"- M3 auto-gate fired at >=3 full-lifecycle v4 markets without waiting for user instruction: **{b1['m3_generated']}**.","",
           "## Q. B2 Status",
           f"- **{report['b2_status']['state']}** - {report['b2_status']['n_observations']} eligible std0 observations; second-level floor unchanged; B2 can never validate a 250ms reaction.","",
           "## R. O3 Recorder Status"]
    o3=report["o3_recorder_status"]
    lines=[*lines,
           f"- Supervisor active: {o3['supervisor_active']} (exit reason {o3['exit_reason']}); analysis ran OFFLINE on closed files only; recorder priority rule untouched.",
           f"- Recorder reliability gate (v3.1): **{o3['reliability_gate']['overall']}** - engineering fix `{o3['engineering_fix_version']}` (8 items); collector version unchanged: {o3['collector_version_unchanged']}; version bump required: {o3['version_bump_required']}; recorder state: **{o3['recorder_state']}**.",
           f"- Raw inputs: {o3['integrity']['n_formal_inputs']} closed SHA-verified files; active unclosed files excluded: {len(o3['active_unclosed_files'])} (ACTIVE_FILE_NO_SIDECAR != SHA_FAILURE); closed missing sidecar {len(o3['integrity']['closed_missing_sidecar_files'])}; SHA failures {len(o3['integrity']['closed_sha_failure_files'])}.","",
           "## S. v2 +250ms Reassessment",
           f"- v2 artifact: `{reassess['v2_artifact']}` - {reassess['v2_n_markets']} market(s), METHOD_A lag {fmt(reassess['v2_method_a_lag_ms'])}ms, direction {reassess['v2_direction']}.",
           f"- v3 inputs: {reassess['v3_n_markets']} markets, {reassess['v3_btc_lead_count']} BTC_LEAD, {reassess['v3_resolved_count']} timing-resolved (ABOVE_TIMING_RESOLUTION).",
           f"- Outcome: **{reassess['outcome']}** ({V2_RESULT_LABELS[reassess['outcome']]})",
           f"- Reasoning: {reassess['reasoning']}",
           "- CONFIRMED_250MS is not in the vocabulary and was not output.","",
           "## T. Decision",
           f"- Timing decision: **{' + '.join(report['timing_decision'])}**",
           f"- Timing confidence: **{report['timing_confidence']}** (direction consistent with limited semantics never upgrades trust tiers or confidence).",
           f"- Overall: **{' + '.join(report['decision'])}**","",
           "No causal, alpha, profitability, execution or trading conclusion is authorized.",
           "DIRECTION MAY REPLICATE BEFORE MAGNITUDE IS IDENTIFIED: report the Level 1 direction, keep Level 2 UNRESOLVED while the peak is below the timing-resolution bound, and treat Level 3 grid peaks as descriptive only.","",
           "## U. Interpretation (v3.1 hierarchy)"]
    interp=report["interpretation"]
    lines=[*lines,
           f"- Direction status: **{interp['direction_status']}**; lag magnitude status: **{interp['lag_magnitude_status']}**.",
           f"- Guard: peak {fmt(interp['guard']['peak_lag_ms'])} vs resolution {fmt(interp['guard']['timing_resolution_ms'])} -> {interp['guard']['lag_magnitude_status']}; unsupported magnitude wording is rejected before publication.",
           f"- Supported conclusion: {interp['supported_conclusion']}",
           f"- Interpretation erratum: `{interp['erratum_artifact']}` (immutable v3 reports unchanged; INTERPRETATION_PRECISION_CORRECTION)."]
    for row in interp["hierarchy_rows"]:
        lines.append(f"- {row['slug']}: L1 **{row['level_1_direction']}** / L2 {row['level_2_lag_magnitude']} ({row['lag_magnitude_status']}) / L3 peak {fmt(row['level_3_peak_ms'])}ms = {row['level_3_role']}.")
    lines+=[f"- Descriptive metrics (never timing conclusions while below resolution): per-market METHOD_A peaks {interp['descriptive_metrics']['per_market_method_a_peak_ms']}; per-market receive-view peaks {interp['descriptive_metrics']['per_market_receive_method_a_peak_ms']}.",
            "- CAPTURE NEW EVIDENCE BEFORE ADDING NEW EXPLANATIONS.",""]
    return "\n".join(lines)


def markdown_at(report:dict)->str:
    pmll=report["per_market_lead_lag"];pooled=report["pooled_vs_equal_market"];dep=report["overlap_dependence"]
    clock=report["clock_basis"];lat=report["latency_distributions"];resp=report["response_magnitude"]
    b1=report["b1_evidence"];b2=report["b2_observations"]
    lines=["# Phase 2B Research - Per-Market Lead-Lag Stability Audit","",
           "**EXPLORATORY RESEARCH ONLY - NO REAL TRADING**","",
           "## A. Governance",
           f"- Phase 2B-Research: **{report['governance']['phase2b_research']}**; Confirmed/strategy/execution/trading: **NOT_AUTHORIZED**",
           f"- Research spec: `{report['research_spec_version']}`; collector: `{report['governance']['collector_version']}`; cohort: `{report['governance']['cohort_version']}`",
           f"- Recorder priority: {report['recorder_cohort_state']['recorder_priority']} - analysis never blocks the recorder.","",
           "## B. Test Baseline",
           f"- Pre-development `pytest` baseline recorded before any Phase 2B-Research code: **{report['test_baseline']['pre_development_passed']} passed**.",
           "- The baseline was re-run for real before development started (spec requirement); current count lives in `tests/`.","",
           "## C. Input Versions",
           f"- Closed SHA-verified raw files only: {report['input_versions']['raw_file_count']} files, sidecar/SHA failures: {len(report['input_versions']['sha256_failures'])}.",
           f"- Incremental manifest (v2/v3 version-separated caches): {report['input_versions']['cache_hits']} cached / {report['input_versions']['cache_misses']} parsed this run; spec `{report['input_versions']['research_spec_version']}`; timing semantics `{report['input_versions']['timing_semantics_version']}`.","",
           "## D. Recorder Status",
           f"- Closed + SHA-verified raw only; active raw files are never formal inputs.",
           f"- Cohort observations: {report['recorder_cohort_state']['primary_cohort_observations']} eligible.","",
           "## E. Full Lifecycle Market Count",
           f"- **{report['data_maturity']['maturity']}** - full/partial {report['data_maturity']['n_complete_markets']}/{report['data_maturity']['n_partial_markets']}, UTC days {report['data_maturity']['n_calendar_days']}, BTC ticks {report['data_maturity']['n_btc_ticks']}, valid/invalid PM states {report['data_maturity']['n_valid_pm_states']}/{report['data_maturity']['n_invalid_pm_states']}, shocks {report['data_maturity']['n_shocks']}.","",
           "## F. Per-Market Lead-Lag",
           f"- Markets with estimates: {pmll['n_markets']}; next B1 milestone: {pmll['next_b1_milestone']}.",
           "- direction tolerance |lag| <= 100ms => SYNCHRONOUS (frozen before results).",""]
    for row in pmll["rows"]:
        lines.append(f"- `{row['slug']}` ({row['calendar_date']}): METHOD_A lag {fmt(row['method_a_lag_ms'])}ms (r={fmt(row['method_a_correlation'])}) -> **{row['direction']}**; "
                     f"METHOD_B {fmt(row['method_b_lag_ms'])}ms / METHOD_C {fmt(row['method_c_lag_ms'])}ms -> {row['method_agreement']}; "
                     f"shocks overlap/non-overlap {row['n_shocks_overlapping']}/{row['n_shocks_non_overlapping_1s']}; "
                     f"timing trust {row.get('timing_trust_tier')}, resolution {row.get('resolution_status')}, coarse {row.get('coarse_lag_bucket')}.")
    lines += ["","## G. Pooled vs Equal-Market-Weighted",
              f"- Shock-weighted pooled peak lag (diagnostic): {fmt(pooled['shock_weighted_pooled']['lag_ms'])}ms (r={fmt(pooled['shock_weighted_pooled']['correlation'])}).",
              f"- Equal-market-weighted (primary stability evidence): mean lag {fmt(pooled['equal_market']['mean_lag_ms'])}ms, median {fmt(pooled['equal_market']['median_lag_ms'])}ms, direction counts {pooled['equal_market']['direction_counts']}.",
              f"- Universal direction claim: {pooled['equal_market']['universal_direction_claim']} (requires >=3 markets and a single direction).",
              f"- Market-level bootstrap: {pooled['bootstrap']['status']}" + (f" CI [{fmt(pooled['bootstrap']['ci_lo_ms'])}, {fmt(pooled['bootstrap']['ci_hi_ms'])}]ms, direction stability {fmt(pooled['bootstrap']['direction_stability_fraction'])}" if pooled['bootstrap']['status']=="COMPUTED_EXPLORATORY" else ""),
              f"- Cluster awareness: {pooled['cluster_awareness']} (shocks within a market are not independent).","",
              "## H. Overlap Dependence",
              f"- OVERLAPPING vs NON_OVERLAPPING_1S (refractory {dep['refractory_ms']}ms, both retained): warnings {dep['markets_with_warning']}/{dep['n_markets']}.",
              f"- Pooled dependence: warning={dep['pooled_warning']}, sign_flip={dep['pooled_sign_flip']}, max_rel_diff={fmt(dep['pooled_max_rel_diff'])}.","",
              "## I. Exchange vs Receive Clock",
              f"- Per-market clock basis: {clock['status_counts']}; TIMING_RESOLUTION_WARNING on {clock['timing_resolution_warning_markets']}/{clock['n_markets']} markets.",
              f"- Latency drift is compared against the measured lag; same order of magnitude triggers a warning.",
              f"- v3 timing audit (registry, trust tiers, resolution statuses, agreement matrix): `{report['timing_audit']['artifact']}`.","",
              "## J. Latency Distributions",
              f"- BTC (exchange->receive): p50/p90/p95/p99/max = {fmt(lat['btc']['p50_ms'])}/{fmt(lat['btc']['p90_ms'])}/{fmt(lat['btc']['p95_ms'])}/{fmt(lat['btc']['p99_ms'])}/{fmt(lat['btc']['max_ms'])} ms.",
              f"- CLOB: p50/p90/p95/p99/max = {fmt(lat['clob']['p50_ms'])}/{fmt(lat['clob']['p90_ms'])}/{fmt(lat['clob']['p95_ms'])}/{fmt(lat['clob']['p99_ms'])}/{fmt(lat['clob']['max_ms'])} ms.","",
              "## K. Shock Magnitude Stability"]
    for row in report["shock_magnitude_stability"]:
        lines.append(f"- {row['bucket']}: shocks {row['n_shocks']} across {row['n_markets']} markets, signed 1s response {fmt(row['signed_mean_cents'])}c, per-market sign +/- {row['n_markets_positive']}/{row['n_markets_negative']}, sign_consistent={row['sign_consistent']}.")
    lines += ["","## L. Lifecycle Stability"]
    for row in report["lifecycle_stability"]:
        lines.append(f"- {row['bucket']}: rows {row['n_rows']}, shocks {row['n_shocks']}, markets {row['n_markets']}, signed 1s response {fmt(row['signed_mean_cents'])}c.")
    lines += ["","## M. Method Agreement",
              f"- METHOD_CONSISTENT_MARKET: {report['method_agreement']['consistent_markets']}/{report['method_agreement']['n_markets']}; agreement remains exploratory.","",
              "## N. Response Magnitude (cents/share)"]
    for horizon in ECONOMIC_HORIZONS_MS:
        stats=resp["by_horizon_ms"][str(horizon)]
        lines.append(f"- {horizon}ms: signed mean {fmt(stats['signed_mean_cents'])}c, abs median {fmt(stats['abs_median_cents'])}c, p25/p75/p90 {fmt(stats['p25_cents'])}/{fmt(stats['p75_cents'])}/{fmt(stats['p90_cents'])}c, n={stats['n']}.")
    lines += [f"- Latency-decay fractions (response_h / response_5s): {resp['latency_decay_fractions']}.","",
                 "## O. B1 Evidence Maturity",
                 f"- **{b1['state']}** - {b1['n_markets']} full-lifecycle markets; attained milestones {b1['attained_milestones']}; next: {fmt(b1['next_milestone'])}.",
                 f"- Goal: replicate the lead-lag *distribution*, not the single +250ms observation.","",
                 "## P. B2 Observation Count",
                 f"- **{b2['state']}** - {b2['n_observations']} eligible fully-covered std0 observations; next milestone {fmt(b2['next_milestone'])}.","",
                 "## Q. B2 N001"]
    if report["b2_n001"]:
        lines.append(f"- First observation_id: `{report['b2_n001']['observation_id']}` - **DESCRIPTIVE_ONLY_TINY_N**; anchor=fill_second_end; same-second ms ordering forbidden.")
    else:
        lines.append("- Not available: no fully-covered prospective_v4 std0 observation yet (INSUFFICIENT_STD0_EVENTS).")
    lines += ["","## R. Phase 2A Status",
              f"- Confirmatory: {report['phase2a_confirmatory_status']}; frozen ledger/settings hash check: **{report['phase2a_frozen_invariants']['status']}**; gates unchanged.","",
              "## S. Interpretation (v3.1) & Recorder Gate",
              f"- Direction: **{report['interpretation']['direction_status']}**; lag magnitude: **{report['interpretation']['lag_magnitude_status']}**.",
              f"- {report['interpretation']['supported_conclusion']}",
              f"- Interpretation erratum: `{report['interpretation']['erratum_artifact']}`.",
              f"- Recorder reliability gate: **{report['recorder_gate']['overall']}** (engineering fix `{report['recorder_gate']['engineering_fix_version']}`; collector version unchanged); recorder state: **{report['recorder_state']}**.","",
              "## T. Limitations"]+[f"- {x}" for x in report["limitations"]]+["",
              "## U. Decision",
              f"- **{' + '.join(report['decision'])}**","",
              "No causal, alpha, profitability, execution or trading conclusion is authorized. 'REPLICATE BEFORE EXPLAIN'.",""]
    return "\n".join(lines)


def markdown_legacy(report:dict)->str:
    d=report["data_maturity"];lead=report["btc_to_pm_lead_lag"]
    sections=["# Phase 2B Market Microstructure","","**EXPLORATORY RESEARCH ONLY - NO REAL TRADING**","",
      "## A. Governance / Version",f"- Research: **{report['governance']['phase2b_research']}**; Confirmed/strategy/execution: **NOT_AUTHORIZED**",f"- Spec: `{report['research_spec_version']}`; collector: `{report['governance']['collector_version']}`","",
      "## B. Data Maturity",f"- **{d['maturity']}** - full/partial markets {d['n_complete_markets']}/{d['n_partial_markets']}, days {d['n_calendar_days']}, BTC ticks {d['n_btc_ticks']}, valid/invalid PM states {d['n_valid_pm_states']}/{d['n_invalid_pm_states']}, shocks {d['n_shocks']}","",
      "## C. Recorder / Cohort State",f"- Closed SHA-verified raw only; std0 cohort N={report['std0_sample_availability']['n']}","",
      "## D. BTC -> PM Lead-Lag",f"- Peak absolute correlation lag: {lead.get('peak_abs_correlation_lag_ms')} ms, correlation {lead.get('peak_abs_correlation')}","- Association only; not causal.","",
      "## E. Market Lifecycle Effects",f"- Grid rows: {report['market_lifecycle_effects']['grid_row_counts']}","",
      "## F. Spread / Depth / OBI Context",f"- Audited fields: {', '.join(report['spread_depth_obi_context'])}","",
      "## G. std0 Sample Availability",f"- **{report['std0_sample_availability']['status']}**",""]
    for title,key in (("H. BTC -> std0","btc_to_std0"),("I. PM -> std0","pm_to_std0"),("J. Fill vs Prior Book","fill_vs_prior_book"),("K. Markout","markout"),("L. Matched Controls","matched_controls"),("M. Maker/Taker Inference","maker_taker_inference"),("N. Pair Economics","pair_economics"),("O. Inventory Proxy","inventory_proxy"),("P. Latency Decay","latency_decay")):
        sections += [f"## {title}",f"- {report[key]['status']}",""]
    sections += ["## Q. Hypothesis Scorecard"]+[f"- {row['hypothesis']}: {row['status']} (N={row['n']})" for row in report["hypothesis_scorecard"]]+["","## R. Limitations"]+[f"- {x}" for x in report["limitations"]]+["","## S. Phase 2A Confirmatory Status",f"- **{report['phase2a_confirmatory_status']}**; frozen hash check **{report['phase2a_frozen_invariants']['status']}**","","## T. Decision",f"- **{' + '.join(report['decision'])}**","","A small number of full markets cannot support generalization, causality, alpha, profitability or executability claims.",""]
    return "\n".join(sections)


def main(argv=None)->int:
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--no-notebook",action="store_true");args=parser.parse_args(argv)
    settings=load_settings();reports=resolve_path(settings,"reports");state=resolve_path(settings,"state");derived=resolve_path(settings,"derived")/"phase2b";derived.mkdir(parents=True,exist_ok=True)
    cache_dir=derived/"cache";cache_dir.mkdir(parents=True,exist_ok=True)
    registry=pt.load_timing_registry(state/"timing_semantics_registry.json")
    state_path=state/"phase2b_research_state.json";research_state=load_state(state_path)
    ledger=resolve_path(settings,"derived")/"event_ledger.parquet";settings_path=ROOT/"config/settings.yaml"
    before={"ledger":file_sha256(ledger),"settings":file_sha256(settings_path)}
    markets=validated_markets(reports)
    if not markets:raise RuntimeError("no formal prospective_v4 full-lifecycle PASS artifact")
    sessions=load_sessions(resolve_path(settings,"sessions"))
    opened={str(e.get("file")) for s in sessions for e in s.events if e.get("event")=="file_open"}
    closed_events={str(e.get("file")) for s in sessions for e in s.events if e.get("event")=="file_close"}
    active_files={name for name in opened-closed_events if Path(name).exists() and not Path(name+".meta.json").exists()}
    closed_files=sorted({Path(name) for market in markets for name in market["raw_files"]})
    integrity=pt.raw_input_integrity(closed_files,active_files=sorted(active_files))
    run_id=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if integrity["status"]!="PASS":
        failure={"research_spec_version":pt.RESEARCH_SPEC_VERSION_V3,"run_id":run_id,"no_real_trading":True,
                 "raw_integrity":integrity,"decision":["MICROSTRUCTURE_DATA_QUALITY_FAILURE"],
                 "limitations":["Raw sidecar/SHA integrity failure; analysis stopped before any estimate."]}
        assert_allowed_decision(failure["decision"]);path=reports/f"phase2b_timing_audit_{run_id}.json";atomic_json(path,failure)
        path.with_suffix(".md").write_text("# Phase 2B Timing Audit\n\n- **MICROSTRUCTURE_DATA_QUALITY_FAILURE** - raw integrity check failed; no estimates produced.\n",encoding="utf-8")
        print(json.dumps({"status":failure["decision"],"report":str(path)},indent=2));return 3
    timeline_frames=[];counts=[];cache_hits=cache_misses=0
    for market in markets:
        frame,market_counts,hit=load_or_build_timeline(market,cache_dir,research_state,run_id)
        timeline_frames.append((market,frame));counts.append(market_counts);cache_hits+=int(hit);cache_misses+=int(not hit)
    grid_frames=[];curves=[];corr=[];regressions=[];per_market_rows=[]
    pooled_values_overlap=[];pooled_values_nonoverlap=[];btc_latencies=[];clob_latencies=[]
    primary_frames=[];decomp_frames=[];state_age_rows=[];btc_robust_by_market={};clob_robust_by_market={}
    backdating_all="PASS"
    for market,frame in timeline_frames:
        cid=str(market["condition_id"]);slug=str(market["slug"])
        start,end=int(market["market_start_ms"]),int(market["market_end_ms"])
        decomp=pt.latency_decomposition(frame,cid,registry);decomp_frames.append(decomp)
        btc_delay=decomp.loc[decomp.source=="BTC","receive_minus_source_ms"].dropna().tolist()
        clob_delay=decomp.loc[decomp.source=="PM","receive_minus_source_ms"].dropna().tolist()
        btc_robust=pt.robust_latency_stats(btc_delay);clob_robust=pt.robust_latency_stats(clob_delay)
        btc_robust_by_market[slug]=btc_robust;clob_robust_by_market[slug]=clob_robust
        btc_latencies.extend(btc_delay);clob_latencies.extend(clob_delay)
        btc_lat,clob_lat=market_latencies(frame)
        btc_ex,book_ex=basis_frames(frame,"exchange");btc_rx,book_rx=basis_frames(frame,"receive")
        market_grids={}
        for grid_ms in GRIDS_MS:
            grid=build_grid(btc_ex,book_ex,start,end,grid_ms);grid=add_market_features(grid,start,end)
            grid.insert(0,"research_spec_version",pt.RESEARCH_SPEC_VERSION_V3);grid.insert(1,"time_basis","exchange_timestamp_ms")
            grid.insert(2,"condition_id",cid);grid.insert(3,"slug",slug);grid_frames.append(grid);market_grids[grid_ms]=grid
        grid_receive=add_market_features(build_grid(btc_rx,book_rx,start,end,250),start,end)
        grid_receive_fine=add_market_features(build_grid(btc_rx,book_rx,start,end,100),start,end)
        availability_grid=pt.availability_state_age(frame,start,end,250)
        backcheck=pt.validate_no_backdating([{"pm_state_availability_ts":int(r.pm_state_availability_ts),
                                              "receive_ts":int(r.pm_state_availability_ts),
                                              "bucket_ts":int(r.timestamp_ms)}
                                             for r in availability_grid.itertuples()
                                             if pd.notna(r.pm_state_availability_ts)])
        if backcheck["status"]!="PASS":backdating_all="BACKDATING_DETECTED"
        primary=market_grids[250];response=market_grids[100]
        curves.extend({"condition_id":cid,**row} for row in event_response_curves(primary,response))
        corr.extend({"condition_id":cid,**row} for row in cross_correlations(primary))
        regressions.extend({"condition_id":cid,**row} for row in lagged_regressions(primary))
        v2_row=per_market_lead_lag_row(cid,slug,start,end,primary,response,grid_receive,btc_lat,clob_lat)
        view_b=pt.view_method_estimates(grid_receive,grid_receive_fine)
        bound=pt.minimum_resolvable_lag_ms(btc_robust,clob_robust)
        hierarchy=pt.timing_hierarchy(v2_row["direction"],v2_row["method_a_lag_ms"],bound)
        info_ages=availability_grid["pm_state_info_age_ms"].dropna()
        avail_ages=availability_grid["pm_state_availability_age_ms"].dropna()
        valid_grid=availability_grid[availability_grid["book_valid"]]
        n_valid_not_fresh=int((valid_grid["pm_state_info_age_ms"]>1000).sum())
        row={**v2_row,
             "research_spec_version":pt.RESEARCH_SPEC_VERSION_V3,
             "timing_semantics_version":pt.TIMING_SEMANTICS_VERSION,
             "view_b_method_b_lag_ms":view_b["method_b_lag_ms"],
             "view_b_method_c_lag_ms":view_b["method_c_lag_ms"],
             "view_b_method_agreement":view_b["method_agreement"],
             "view_c_method_a_lag_ms":view_b["method_a_lag_ms"],
             "view_a_direction":v2_row["direction"],
             "view_b_direction":v2_row["receive_direction"],
             "view_c_direction":v2_row["receive_direction"],
             "clock_views_agree":v2_row["direction"]==v2_row["receive_direction"],
             "timing_ambiguity_ms":bound,
             "timing_trust_tier":pt.timing_trust_tier(v2_row["method_a_lag_ms"],bound),
             "resolution_status":pt.resolution_status(v2_row["method_a_lag_ms"],bound),
             "resolution_status_receive":pt.resolution_status(v2_row["receive_method_a_lag_ms"],bound),
             "coarse_lag_bucket":pt.coarse_lag_bucket(v2_row["method_a_lag_ms"]),
             "raw_btc_lead":v2_row["direction"]=="BTC_LEAD",
             "timing_resolved_btc_lead":(v2_row["direction"]=="BTC_LEAD"
                                         and pt.resolution_status(v2_row["method_a_lag_ms"],bound)=="ABOVE_TIMING_RESOLUTION"),
             "backdating_check":backcheck["status"],
             "pm_state_info_age_p50_ms":float(np.percentile(info_ages,50)) if len(info_ages) else None,
             "pm_state_info_age_p90_ms":float(np.percentile(info_ages,90)) if len(info_ages) else None,
             "pm_state_info_age_p99_ms":float(np.percentile(info_ages,99)) if len(info_ages) else None,
             "pm_state_availability_age_p50_ms":float(np.percentile(avail_ages,50)) if len(avail_ages) else None,
             "n_valid_not_fresh_states":n_valid_not_fresh,
             "level_1_direction":hierarchy["level_1_direction"],
             "level_2_lag_magnitude":hierarchy["level_2_lag_magnitude"],
             "lag_magnitude_status":hierarchy["lag_magnitude_status"],
             "level_3_peak_ms":hierarchy["level_3_peak_ms"],
             "level_3_role":hierarchy["level_3_role"]}
        per_market_rows.append(row)
        state_age_rows.append({"slug":slug,"condition_id":cid,
                               "pm_state_info_age_p50_ms":row["pm_state_info_age_p50_ms"],
                               "pm_state_info_age_p90_ms":row["pm_state_info_age_p90_ms"],
                               "pm_state_info_age_p99_ms":row["pm_state_info_age_p99_ms"],
                               "pm_state_availability_age_p50_ms":row["pm_state_availability_age_p50_ms"],
                               "n_valid_not_fresh":n_valid_not_fresh})
        anchors=shock_anchor_rows(primary);anchor_times=[int(t) for t in anchors["timestamp_ms"].tolist()]
        keep=set(non_overlapping_anchors(anchor_times));anchors_no=anchors[anchors["timestamp_ms"].map(lambda t:int(t) in keep)]
        pooled_values_overlap.append(collect_shock_response_values(anchors,response))
        pooled_values_nonoverlap.append(collect_shock_response_values(anchors_no,response))
        primary_frames.append(primary)
    timeline_path=derived/f"market_timeline_{run_id}.parquet";grid_path=derived/f"market_grids_{run_id}.parquet"
    curves_path=derived/f"event_response_{run_id}.parquet";corr_path=derived/f"cross_correlation_{run_id}.parquet";reg_path=derived/f"lagged_regression_{run_id}.parquet"
    pmll_path=derived/f"per_market_lead_lag_v3_{run_id}.parquet";stability_path=derived/f"stability_summary_{run_id}.parquet"
    timing_path=derived/f"timing_diagnostics_{run_id}.parquet"
    pq.write_table(pa.Table.from_pandas(pd.concat([f for _,f in timeline_frames],ignore_index=True),preserve_index=False),timeline_path,compression="zstd")
    grids=pd.concat(grid_frames,ignore_index=True);pq.write_table(pa.Table.from_pandas(grids,preserve_index=False),grid_path,compression="zstd")
    pq.write_table(pa.Table.from_pylist(curves),curves_path);pq.write_table(pa.Table.from_pylist(corr),corr_path);pq.write_table(pa.Table.from_pylist(regressions),reg_path)
    pq.write_table(pa.Table.from_pylist(per_market_rows),pmll_path,compression="zstd")
    decomposition_all=pd.concat(decomp_frames,ignore_index=True)
    pq.write_table(pa.Table.from_pandas(decomposition_all,preserve_index=False),timing_path,compression="zstd")
    primary_concat=pd.concat(primary_frames,ignore_index=True)
    n_markets=len(markets);n_shocks=int((primary_concat.btc_ret_1s_bp.abs()>=1).sum())
    maturity="TINY_SAMPLE" if n_markets<3 else "EXPLORATORY_SMALL_N" if n_markets<=20 else "EXPLORATORY" if n_markets<100 else "INTERMEDIATE_EVIDENCE"
    equal_market=equal_market_summary(per_market_rows);lags=[r["method_a_lag_ms"] for r in per_market_rows if r["method_a_lag_ms"] is not None]
    bootstrap=market_bootstrap(lags);pooled=pooled_peak_lag(primary_concat[["timestamp_ms","grid_ms","btc_price","pm_mid"]])
    valid_corr=[r for r in corr if r["correlation"] is not None];peak=max(valid_corr,key=lambda r:abs(r["correlation"])) if valid_corr else None
    reverse=max((r for r in valid_corr if r["lag_ms"]<0),key=lambda r:abs(r["correlation"]),default=None)
    overlap_map=combine_value_maps(pooled_values_overlap);nonoverlap_map=combine_value_maps(pooled_values_nonoverlap)
    overlap_stats={int(h):response_stats_from_values(v) for h,v in overlap_map.items()}
    nonoverlap_stats={int(h):response_stats_from_values(v) for h,v in nonoverlap_map.items()}
    dependence_pooled=dependence_sensitivity(overlap_stats,nonoverlap_stats)
    response_by_horizon={};decay={}
    base_5s=overlap_stats.get(5000,{}).get("signed_mean")
    for horizon in ECONOMIC_HORIZONS_MS:
        s=overlap_stats.get(int(horizon),{});response_by_horizon[str(horizon)]={"n":s.get("n"),"signed_mean_cents":s["signed_mean"]*100 if s.get("signed_mean") is not None else None,"abs_median_cents":s["abs_median"]*100 if s.get("abs_median") is not None else None,"p25_cents":s["p25"]*100 if s.get("p25") is not None else None,"p75_cents":s["p75"]*100 if s.get("p75") is not None else None,"p90_cents":s["p90"]*100 if s.get("p90") is not None else None}
        value=s.get("signed_mean");decay[f"{horizon}ms"]=value/base_5s if value is not None and base_5s not in (None,0) else None
    shock_rows=shock_bucket_stability(primary_concat);lifecycle_rows=lifecycle_stability(primary_concat)
    pq.write_table(pa.Table.from_pylist([{"kind":"shock_bucket",**row} for row in shock_rows]+[{"kind":"lifecycle_bucket",**row} for row in lifecycle_rows]),stability_path)
    ops_path=latest_json(reports,"live_operations_24h_*.json");ops=json.loads(ops_path.read_text(encoding="utf-8")) if ops_path else {}
    v4_markets=[m for m in ops.get("markets",[]) if m.get("collector_version")==PRIMARY_COLLECTOR_VERSION];partial=sum(m.get("lifecycle")!="FULL_LIFECYCLE_MARKET" for m in v4_markets)
    cohort=CohortManifest(state/"prospective_cohort.json");eligible=b2_eligible_observations(cohort.observations(COHORT_VERSION))
    b2_status=b2_observation_state(len(eligible));std0_context=materialize_std0_context(eligible,grids,settings,derived,reports,run_id)
    after={"ledger":file_sha256(ledger),"settings":file_sha256(settings_path)};frozen=frozen_invariant_check(before,after)
    clock_counts=Counter(r["clock_basis_status"] for r in per_market_rows)
    b1_state=b1_maturity_state(n_markets)
    # ------------------------------------------------ v2 +250ms reassessment
    with_estimate=[r for r in per_market_rows if r["method_a_lag_ms"] is not None]
    btc_lead_count=sum(1 for r in with_estimate if r["raw_btc_lead"])
    resolved_count=sum(1 for r in with_estimate if r["resolution_status"]=="ABOVE_TIMING_RESOLUTION")
    reassessment_outcome=pt.v2_reassessment(n_markets,len(with_estimate),btc_lead_count,resolved_count)
    pt.assert_allowed_v2_reassessment(reassessment_outcome)
    v2_artifact=v2_report_artifact(reports)
    if reassessment_outcome=="TIMING_ROBUST":
        reasoning="direction replicated by strict majority AND every estimated market's lag is ABOVE_TIMING_RESOLUTION"
    elif reassessment_outcome=="DIRECTIONALLY_ROBUST_BUT_LAG_UNRESOLVED":
        reasoning="direction replicated by strict majority but the lag is not timing-resolved (ambiguity at or above the measured lag); the direction stands, the magnitude does not"
    elif reassessment_outcome=="NOT_TIMING_ROBUST":
        reasoning="direction did not replicate by strict majority under v3 timing semantics"
    else:
        reasoning="fewer than 2 markets or no estimate available"
    # ------------------------------------------------ timing decision / audit
    agreement=pt.agreement_matrix([{"market":r["slug"],"VIEW_A":r["view_a_direction"],
                                    "VIEW_B":r["view_b_direction"],"VIEW_C":r["view_c_direction"]}
                                   for r in per_market_rows])
    sem_status=pt.timing_semantics_status(registry)
    instability=(agreement["status"]=="CLOCK_BASIS_INSTABILITY"
                 or any(r["clock_basis_status"]=="CLOCK_BASIS_INSTABILITY" for r in per_market_rows))
    n_not_above=len(with_estimate)-resolved_count
    timing_decisions=pt.timing_decision(sem_status,parser_failure=(backdating_all!="PASS"),
                                        clock_instability=instability,
                                        n_with_estimate=len(with_estimate),
                                        n_not_above_resolution=n_not_above)
    pt.assert_allowed_timing_decision(timing_decisions)
    timing_confidence="LOW"
    overall_bound=pt.minimum_resolvable_lag_ms(pt.robust_latency_stats(btc_latencies),pt.robust_latency_stats(clob_latencies))
    btc_delays_all=decomposition_all.loc[decomposition_all.source=="BTC","receive_minus_source_ms"].dropna()
    clob_delays_all=decomposition_all.loc[decomposition_all.source=="PM","receive_minus_source_ms"].dropna()
    btc_e_minus_t=(decomposition_all.loc[decomposition_all.source=="BTC","frame_ts"]
                   -decomposition_all.loc[decomposition_all.source=="BTC","source_event_ts"]).dropna()
    direction_counts=Counter(r["direction"] for r in with_estimate)
    coarse_counts=Counter(r["coarse_lag_bucket"] for r in with_estimate)
    # ------------------------------------------------ v3.1 interpretation hierarchy
    max_abs_peak=max((abs(r["method_a_lag_ms"]) for r in with_estimate if r["method_a_lag_ms"] is not None),default=None)
    dominant=Counter(r["direction"] for r in with_estimate).most_common(1)[0][0] if with_estimate else "UNKNOWN"
    direction_status="DIRECTION_REPLICATED_EARLY" if (with_estimate and btc_lead_count*2>len(with_estimate)) else "DIRECTION_NOT_REPLICATED"
    lag_magnitude_status="LAG_MAGNITUDE_RESOLVED" if (with_estimate and resolved_count>=len(with_estimate)) else "LAG_MAGNITUDE_UNRESOLVED"
    view_text="on all three clock views" if (n_markets>0 and agreement["all_three_agree"]==n_markets) else "in the primary view"
    if lag_magnitude_status=="LAG_MAGNITUDE_RESOLVED":
        supported=(f"Across the {len(with_estimate)} estimated eligible prospective_v4 markets, the dominant "
                   f"association is {dominant} {view_text} and every measured peak lag is at or above the "
                   f"timing-resolution bound.")
    else:
        supported=(f"Across the {len(with_estimate)} estimated eligible prospective_v4 markets, the dominant "
                   f"association is {dominant} {view_text}; however, the measured peak lags are below the "
                   f"current timing-resolution bound, so the lag magnitude is unresolved.")
    guard=pt.interpretation_guard(supported,max_abs_peak,overall_bound)  # hard rule: below-resolution magnitude never enters the conclusion
    interpretation={"direction_status":direction_status,
                    "lag_magnitude_status":lag_magnitude_status,
                    "supported_conclusion":supported,
                    "guard":{**guard,"peak_lag_ms":max_abs_peak,"timing_resolution_ms":overall_bound},
                    "descriptive_metrics":{"per_market_method_a_peak_ms":{r["slug"]:r["method_a_lag_ms"] for r in with_estimate},
                                           "per_market_receive_method_a_peak_ms":{r["slug"]:r["receive_method_a_lag_ms"] for r in with_estimate},
                                           "per_market_timing_bounds_ms":{r["slug"]:r["timing_ambiguity_ms"] for r in with_estimate},
                                           "note":"numeric peaks are descriptive statistics only; they enter timing conclusions only where resolution_status is ABOVE_TIMING_RESOLUTION"},
                    "hierarchy_rows":[{"slug":r["slug"],"level_1_direction":r["level_1_direction"],
                                       "level_2_lag_magnitude":r["level_2_lag_magnitude"],
                                       "lag_magnitude_status":r["lag_magnitude_status"],
                                       "level_3_peak_ms":r["level_3_peak_ms"],
                                       "level_3_role":r["level_3_role"]} for r in per_market_rows]}
    erratum_path=latest_json(reports,"phase2b_v3_interpretation_erratum_*.json")
    # ------------------------------------------------ v3.1 recorder reliability gate
    recorder_gate=detect_recorder_hotfix()
    supervisor_status=json.loads((state/"supervisor_status.json").read_text(encoding="utf-8")) if (state/"supervisor_status.json").exists() else {}
    if recorder_gate.get("overall")!="MEMORY_HOTFIX_PASS":
        recorder_state="RECORDER_INTEGRITY_FAILURE"
    elif supervisor_status.get("active"):
        recorder_state="ACCUMULATING_LIVE_DATA"
    else:
        recorder_state="RECORDER_READY"
    # ------------------------------------------------ B1 v3 milestones
    attained_b1=sorted({m for m in B1_MILESTONES if n_markets>=m})
    m3_generated=None
    for milestone in B1_MILESTONES:
        if n_markets>=milestone:
            payload=b1_v3_milestone_payload(milestone,n_markets,run_id,per_market_rows,equal_market,bootstrap,reassessment_outcome,str(pmll_path))
            text=(f"# Phase 2B B1-M{milestone} (v3)\n\n- Evidence state: **{payload['evidence_state']}**\n- Markets: {n_markets} across {payload['n_days']} UTC day(s)\n"
                  f"- Direction counts: {payload['direction_counts']}; direction fractions: {payload['direction_fractions']}; timing-resolved BTC_LEAD: {payload['timing_resolved_direction_counts']}\n"
                  f"- raw_btc_lead_fraction: {fmt(payload['raw_btc_lead_fraction'])}; timing_resolved_btc_lead_fraction: {fmt(payload['timing_resolved_btc_lead_fraction'])} (always reported separately)\n"
                  f"- VIEW_A/B/C agreement: {payload['view_abc_agreement']}; METHOD_A/B/C agreement: {payload['method_abc_agreement']}; dependence warnings: {payload['overlap_dependence_markets_with_warning']}/{n_markets}\n"
                  f"- Per-market timing bounds (ms): {payload['per_market_timing_bounds_ms']}; median bound: {fmt(payload['median_timing_bound_ms'])}\n"
                  f"- Market bootstrap (unit=MARKET): {payload['market_bootstrap_fractions']['status']}\n"
                  f"- v2 reassessment: **{reassessment_outcome}**\n"
                  "- BTC_LEAD and TIMING-RESOLVED BTC_LEAD are different things; effective evidence is the market count.\n"
                  "- EXPLORATORY ONLY - NO REAL TRADING.\n")
            status,path=emit_milestone_once(reports,f"phase2b_b1_v3_m{milestone}",run_id,payload,text)
            if milestone==3:m3_generated=status
    research_state["attained_b1_milestones"]=sorted(set(research_state.get("attained_b1_milestones",[]))|set(attained_b1))
    research_state["attained_b1_v3_milestones"]=sorted(set(research_state.get("attained_b1_v3_milestones",[]))|set(attained_b1))
    research_state["attained_b2_milestones"]=sorted(set(research_state.get("attained_b2_milestones",[]))|{m for m in B2_MILESTONES if len(eligible)>=m})
    research_state["last_run_id"]=run_id;research_state["research_spec_version"]=pt.RESEARCH_SPEC_VERSION_V3
    research_state["timing_semantics_version"]=pt.TIMING_SEMANTICS_VERSION
    save_state(state_path,research_state)
    overall=["EXPLORATORY_PIPELINE_READY","EXPLORATORY_EVIDENCE_ACCUMULATING"] if frozen["status"]=="PASS" else ["MICROSTRUCTURE_DATA_QUALITY_FAILURE"]
    decision=overall+[b1_state,b2_status];assert_allowed_decision(decision)
    lifecycle={str(k):int(v) for k,v in primary_concat.lifecycle_bucket.value_counts(dropna=False).items()}
    context=frame_summary(primary_concat,["pm_spread","pm_bid_depth_top3","pm_ask_depth_top3","pm_obi_top3","btc_vol_5s_bp"])
    limitations=[f"{n_markets} formal full-lifecycle prospective_v4 markets are available; per-market tables grow as the recorder accumulates closed markets.",
        "PM source timestamps are per-frame server times (SOURCE_FRAME_TIME, FRAME_LEVEL); sub-second source-time lead-lag is descriptive only (TIMING_SEMANTICS_LIMITED).",
        "Local clock offset is UNKNOWN; receive-minus-source mixes transport with clock offset and is never decomposed into the two.",
        f"Minimum resolvable lag (frozen heuristic) is {fmt(overall_bound)} ms - materially larger than any observed sub-second peak; sub-second lag magnitudes are BELOW/NEAR timing resolution.",
        "Receive-time burst quantization (shared asyncio loop) limits ordering inside identical-ms bursts.",
        "Overlapping shock anchors are descriptive and not independent observations; NON_OVERLAPPING_1S is retained alongside them; effective evidence scales with market count, not shock count.",
        "METHOD_B peak horizon measures response magnitude timing, not onset; agreement is descriptive only.",
        "B2 std0 timestamps are second-level; B2 can never validate a 250ms reaction (>=1s ambiguity by construction).",
        "No causal, alpha, profitability, execution or trading conclusion is authorized."]
    report={"research_spec_version":pt.RESEARCH_SPEC_VERSION_V3,"run_id":run_id,"research_only":True,"no_real_trading":True,
      "governance":{"phase2b_research":"AUTHORIZED_EXPLORATORY","phase2b_confirmed":"NOT_AUTHORIZED","strategy_research":"NOT_AUTHORIZED","pnl_execution":"NOT_AUTHORIZED","collector_version":PRIMARY_COLLECTOR_VERSION,"cohort_version":COHORT_VERSION},
      "test_baseline":{"pre_development_passed":TEST_BASELINE_COUNT_V3,"recorded_before_development":True,"note":"re-run for real before v3 development (spec 41); current count in tests/"},
      "input_versions":{"research_spec_version":pt.RESEARCH_SPEC_VERSION_V3,"timing_semantics_version":pt.TIMING_SEMANTICS_VERSION,"raw_file_count":integrity["n_formal_inputs"],"sha256_failures":integrity["closed_sha_failure_files"],"sidecar_missing":integrity["closed_missing_sidecar_files"],"active_excluded":integrity["active_excluded_files"],"cache_hits":cache_hits,"cache_misses":cache_misses},
      "data_maturity":{"maturity":maturity,"n_complete_markets":n_markets,"n_partial_markets":partial,"n_calendar_days":len({datetime.fromtimestamp(int(m["market_start_ms"])/1000,timezone.utc).date().isoformat() for m in markets}),"n_btc_ticks":sum(c["btc_v4"] for c in counts),"n_valid_pm_states":sum(c["book_valid"] for c in counts),"n_invalid_pm_states":sum(c["book_invalid"] for c in counts),"n_shocks":n_shocks,"counts_by_market":counts},
      "recorder_cohort_state":{"recorder_priority":"HIGHER_THAN_OFFLINE_ANALYSIS","primary_cohort_observations":len(eligible),"raw_files_closed_only":True,"raw_integrity":integrity},
      "per_market_lead_lag":{"n_markets":n_markets,"rows":per_market_rows,"next_b1_milestone":next_b1_milestone(n_markets),"direction_tolerance_ms":100,"artifact":str(pmll_path)},
      "pooled_vs_equal_market":{"shock_weighted_pooled":pooled,"equal_market":equal_market,"bootstrap":bootstrap,"primary_stability_evidence":"EQUAL_MARKET_WEIGHTED","cluster_awareness":cluster_awareness(per_market_rows,primary_concat)},
      "overlap_dependence":{"refractory_ms":1000,"n_markets":n_markets,"markets_with_warning":sum(1 for r in per_market_rows if r["dependence_sensitivity_warning"]),"pooled_warning":dependence_pooled["warning"],"pooled_sign_flip":dependence_pooled["sign_flip"],"pooled_max_rel_diff":dependence_pooled["max_rel_diff"],"overlapping_stats":{str(h):s for h,s in overlap_stats.items()},"non_overlapping_stats":{str(h):s for h,s in nonoverlap_stats.items()}},
      "clock_basis":{"n_markets":n_markets,"status_counts":dict(clock_counts),"timing_resolution_warning_markets":sum(1 for r in per_market_rows if r["timing_resolution_warning"]),"drift_rule":"max(p99-p50 of BTC/CLOB latency) >= max(|lag|, 100ms)"},
      "latency_distributions":{"btc":latency_summary(btc_latencies),"clob":latency_summary(clob_latencies)},
      "shock_magnitude_stability":shock_rows,"lifecycle_stability":lifecycle_rows,
      "method_agreement":{"n_markets":n_markets,"consistent_markets":sum(1 for r in per_market_rows if r["method_agreement"]=="METHOD_CONSISTENT_MARKET"),"rule":">=2 of 3 methods share a direction"},
      "interpretation":{"direction_status":interpretation["direction_status"],
                        "lag_magnitude_status":interpretation["lag_magnitude_status"],
                        "supported_conclusion":interpretation["supported_conclusion"],
                        "guard":interpretation["guard"],
                        "descriptive_metrics":interpretation["descriptive_metrics"],
                        "hierarchy_rows":interpretation["hierarchy_rows"],
                        "erratum_artifact":str(erratum_path) if erratum_path else None},
      "recorder_gate":recorder_gate,"recorder_state":recorder_state,
      "response_magnitude":{"unit":"cents_per_share","by_horizon_ms":response_by_horizon,"latency_decay_fractions":decay},
      "b1_evidence":{"state":b1_state,"n_markets":n_markets,"attained_milestones":attained_b1,"next_milestone":next_b1_milestone(n_markets),"replication_goal":"lead-lag distribution, not +250ms specifically"},
      "b2_observations":{"n_observations":len(eligible),"state":b2_status,"next_milestone":next_b2_milestone(len(eligible))},
      "b2_n001":{"observation_id":eligible[0].get("observation_id") if eligible else None,"state":"DESCRIPTIVE_ONLY_TINY_N" if eligible else "INSUFFICIENT_STD0_EVENTS","post_fill_anchor":"fill_second_end","same_second_policy":"NO_SAME_SECOND_PRE_POST_ORDERING"} if eligible else None,
      "btc_to_pm_lead_lag":{"association_only":True,"causal_claim":False,"primary_grid_ms":250,"sensitivity_grids_ms":list(GRIDS_MS),"response_horizons_ms":[100,250,500,1000,2000,5000],"peak_abs_correlation_lag_ms":peak["lag_ms"] if peak else None,"peak_abs_correlation":peak["correlation"] if peak else None,"cross_correlations":corr,"response_curves":curves,"lagged_regressions":regressions},
      "pm_to_btc_lead_lag":{"association_only":True,"peak_negative_lag_ms":reverse["lag_ms"] if reverse else None,"peak_negative_lag_correlation":reverse["correlation"] if reverse else None},
      "market_lifecycle_effects":{"grid_row_counts":lifecycle},"spread_depth_obi_context":context,
      "std0_sample_availability":{"n":len(eligible),"status":b2_status,"auto_enable_at_n":1,"same_second_policy":"NO_SAME_SECOND_PRE_POST_ORDERING","context_artifact":str(std0_context) if std0_context else None},
      "btc_to_std0":{"status":"NOT_RUN" if not eligible else "AVAILABLE_FOR_EXPLORATION"},"pm_to_std0":{"status":"NOT_RUN" if not eligible else "AVAILABLE_FOR_EXPLORATION"},
      "fill_vs_prior_book":{"status":"NOT_RUN_INSUFFICIENT_STD0_EVENTS" if not eligible else "MATERIALIZED"},"markout":{"status":"NOT_RUN_INSUFFICIENT_STD0_EVENTS" if not eligible else "MATERIALIZED_CONSERVATIVE","primary_horizons_seconds":[1,5,30,60],"anchor":"fill_second_end"},
      "matched_controls":{"status":"NOT_RUN_INSUFFICIENT_STD0_EVENTS" if not eligible else "ARCHITECTURE_READY","future_leakage_allowed":False},
      "maker_taker_inference":{"status":"ARCHITECTURE_READY","method":"MULTISET_OCCURRENCE_MATCHING"},
      "pair_economics":{"status":"ARCHITECTURE_READY","fee_adjusted":"NOT_COMPUTED_WITHOUT_VERSIONED_FEE_RULE"},
      "inventory_proxy":{"status":"ARCHITECTURE_READY","is_true_inventory":False},
      "latency_decay":{"status":"EXPLORATORY_SMALL_N" if n_markets>=3 else "EXPLORATORY_TINY_SAMPLE","horizons_ms":[100,250,500,1000,2000],"no_fill_probability_model":True},
      "hypothesis_scorecard":[{"hypothesis":h,"evidence_for":[],"evidence_against":[],"n":n_markets,"status":"INCONCLUSIVE"} for h in ("FAST_INFORMATION","MARKET_MAKING","RELATIVE_VALUE_PAIRING","INVENTORY_REBALANCING","MIXED_MECHANISM")],
      "limitations":limitations,
      "phase2a_confirmatory_status":"ACCUMULATING_LIVE_DATA","phase2a_frozen_invariants":frozen,"decision":decision,
      "timing_audit":{"artifact":"REPLACED_BELOW","v2_reassessment":reassessment_outcome,"timing_decision":timing_decisions},
      "artifacts":{"market_timeline":str(timeline_path),"market_grids":str(grid_path),"event_response":str(curves_path),"cross_correlation":str(corr_path),"lagged_regression":str(reg_path),"per_market_lead_lag":str(pmll_path),"stability_summary":str(stability_path),"timing_diagnostics":str(timing_path),"std0_fill_context":str(std0_context) if std0_context else None,"state":str(state_path)}}
    # ------------------------------------------------ timing audit report
    audit={"research_spec_version":pt.RESEARCH_SPEC_VERSION_V3,
           "timing_semantics_version":pt.TIMING_SEMANTICS_VERSION,"run_id":run_id,
           "research_only":True,"no_real_trading":True,
           "principle":"MEASURE THE CLOCK BEFORE MEASURING THE LAG",
           "test_baseline":{"pre_development_passed":TEST_BASELINE_COUNT_V3,"recorded_before_development":True},
           "frozen_spec":{"direction_tolerance_ms":100,"refractory_ms":1000,"trust_tier_ratios":"A>=4x,B>=2x,C>=1x,D<1x",
                          "resolution_thresholds":"ABOVE>=bound,NEAR>=0.5*bound,BELOW<0.5*bound",
                          "minimum_resolvable_lag_rule":"max(p99-p50 of BTC, p99-p50 of CLOB receive-minus-source)",
                          "coarse_lag_buckets":["0-500ms","500-1000ms","1-2s",">2s"],
                          "bootstrap":{"seed":20260824,"min_markets":10},
                          "v2_reassessment_rule":"strict majority BTC_LEAD + all estimated markets ABOVE resolution"},
           "input_versions":report["input_versions"],
           "raw_integrity":integrity,
           "timestamp_registry":{"timing_semantics_version":pt.TIMING_SEMANTICS_VERSION,
                                 "frozen_before_viewing_new_market_aggregates":True,
                                 "local_clock":registry["local_clock"],
                                 "entries":[{"source":e["source"],"field_name":e["field_name"],
                                             "semantic_name":e["semantic_name"],
                                             "timestamp_class":e["timestamp_class"],
                                             "timestamp_granularity":e.get("timestamp_granularity"),
                                             "trust_level":e["trust_level"],
                                             "can_order_cross_source":e["can_order_cross_source"]}
                                            for e in registry["entries"]]},
           "raw_timestamp_semantics":{"btc_exchange":"Binance trade time T = SOURCE_EVENT_TIME (HIGH, event-level)",
                                      "pm_exchange":"CLOB per-frame server timestamp = SOURCE_FRAME_TIME (MEDIUM, FRAME_LEVEL; children inherit parent frame ts; NOT last-trade time)",
                                      "receives":"LOCAL_RECEIVE_TIME on one local clock; BTC burst-quantized by the shared asyncio loop",
                                      "std0":"SECOND_LEVEL; same-second ms ordering forbidden"},
           "btc_timing_diagnostics":{"receive_minus_source":pt.robust_latency_stats(btc_delays_all.tolist()),
                                     "interpretation":pt.interpret_latency("SOURCE_EVENT_TIME"),
                                     "systematic_offset":pt.systematic_offset_assessment(pt.robust_latency_stats(btc_delays_all.tolist())),
                                     **half_split_stats(btc_delays_all.tolist()),
                                     "e_minus_t_p50_ms":float(np.median(btc_e_minus_t)) if len(btc_e_minus_t) else None},
           "clob_timing_diagnostics":{"receive_minus_frame":pt.robust_latency_stats(clob_delays_all.tolist()),
                                      "interpretation":pt.interpret_latency("SOURCE_FRAME_TIME"),
                                      "systematic_offset":pt.systematic_offset_assessment(pt.robust_latency_stats(clob_delays_all.tolist())),
                                      **half_split_stats(clob_delays_all.tolist())},
           "event_type_breakdown":pt.latency_explain_by(decomposition_all,["event_type"]),
           "by_market":pt.latency_explain_by(decomposition_all,["market"]),
           "by_market_connection":pt.latency_explain_by(decomposition_all[decomposition_all.source=="PM"],["market","connection_id"]),
           "by_market_session":pt.latency_explain_by(decomposition_all[decomposition_all.source=="PM"],["market","session_id"]),
           "state_age_diagnostics":state_age_rows,
           "local_clock_health":pt.local_clock_health(json.loads((state/"live_health.json").read_text(encoding="utf-8")) if (state/"live_health.json").exists() else {}),
           "cross_source_comparability":pt.cross_source_ordering(registry),
           "minimum_resolvable_lag":{"rule":"max(p99-p50 of BTC, p99-p50 of CLOB receive-minus-source)",
                                     "overall_ms":overall_bound,
                                     "per_market_ms":{r["slug"]:r["timing_ambiguity_ms"] for r in per_market_rows}},
           "source_time_lead_lag":{"status":"DESCRIPTIVE_ONLY_SOURCE_FRAME_TIME_SEMANTICS",
                                   "rows":[{"slug":r["slug"],"method_a_lag_ms":r["method_a_lag_ms"],
                                            "method_b_lag_ms":r["method_b_lag_ms"],
                                            "method_c_lag_ms":r["method_c_lag_ms"],
                                            "direction":r["direction"],
                                            "method_agreement":r["method_agreement"]} for r in per_market_rows]},
           "receive_time_lead_lag":{"status":"PRIMARY_VALID_VIEW_SHARED_LOCAL_CLOCK",
                                    "rows":[{"slug":r["slug"],"method_a_lag_ms":r["receive_method_a_lag_ms"],
                                             "method_b_lag_ms":r["view_b_method_b_lag_ms"],
                                             "method_c_lag_ms":r["view_b_method_c_lag_ms"],
                                             "direction":r["view_b_direction"],
                                             "method_agreement":r["view_b_method_agreement"]} for r in per_market_rows]},
           "availability_time_lead_lag":{"no_backdating":backdating_all,"n_markets":n_markets,
                                         "equivalence_note":"VIEW_C availability equals the constructing event's receive time for collector phase2a_prospective_v4 (synchronous single-loop reconstruction; no LOCAL_PROCESS_TIME recorded), so VIEW_C estimates equal VIEW_B numerically - asserted, not assumed; a future async collector would diverge and be caught by the no-backdating check.",
                                         "rows":[{"slug":r["slug"],"method_a_lag_ms":r["view_c_method_a_lag_ms"],
                                                  "direction":r["view_c_direction"],
                                                  "timing_trust_tier":r["timing_trust_tier"],
                                                  "resolution_status":r["resolution_status"],
                                                  "coarse_lag_bucket":r["coarse_lag_bucket"]} for r in per_market_rows]},
           "cross_clock_agreement":agreement,
           "dependence_sensitivity":{"refractory_ms":1000,"n_markets":n_markets,
                                     "markets_with_warning":sum(1 for r in per_market_rows if r["dependence_sensitivity_warning"]),
                                     "pooled_warning":dependence_pooled["warning"],
                                     "pooled_sign_flip":dependence_pooled["sign_flip"],
                                     "pooled_max_rel_diff":dependence_pooled["max_rel_diff"]},
           "per_market_replication":{"n_markets":n_markets,
                                     "direction_counts":dict(direction_counts),
                                     "coarse_bucket_counts":dict(coarse_counts),
                                     "raw_btc_lead_fraction":btc_lead_count/len(with_estimate) if with_estimate else None,
                                     "timing_resolved_btc_lead_fraction":sum(1 for r in with_estimate if r["timing_resolved_btc_lead"])/len(with_estimate) if with_estimate else None,
                                     "cluster_awareness":cluster_awareness(per_market_rows,primary_concat)},
           "b1_milestones":{"state":b1_state,"n_markets":n_markets,"attained":attained_b1,
                            "next":next_b1_milestone(n_markets),
                            "m3_generated":m3_generated,
                            "bootstrap":bootstrap,
                            "market_bootstrap_fractions":pt.market_bootstrap_fractions(per_market_rows)},
           "b2_status":{"state":b2_status,"n_observations":len(eligible),
                        "second_level_floor":">=1s ambiguity; same-second ms ordering forbidden"},
           "o3_recorder_status":{"supervisor_active":supervisor_status.get("active"),
                                 "exit_reason":supervisor_status.get("exit_reason"),
                                 "analysis_mode":"OFFLINE_CLOSED_SHA_VERIFIED_ONLY",
                                 "active_unclosed_files":sorted(active_files),
                                 "integrity":integrity,
                                 "reliability_gate":recorder_gate,
                                 "recorder_state":recorder_state,
                                 "engineering_fix_version":recorder_gate.get("engineering_fix_version"),
                                 "collector_version_unchanged":recorder_gate.get("collector_version_unchanged"),
                                 "version_bump_required":recorder_gate.get("version_bump_required")},
           "v2_reassessment":{"v2_artifact":v2_artifact["artifact"] if v2_artifact else None,
                              "v2_run_id":v2_artifact["run_id"] if v2_artifact else None,
                              "v2_n_markets":v2_artifact["n_markets"] if v2_artifact else None,
                              "v2_method_a_lag_ms":v2_artifact["method_a_lag_ms"] if v2_artifact else None,
                              "v2_direction":v2_artifact["direction"] if v2_artifact else None,
                              "v3_n_markets":n_markets,"v3_btc_lead_count":btc_lead_count,
                              "v3_resolved_count":resolved_count,
                              "outcome":reassessment_outcome,
                              "v2_result_label":V2_RESULT_LABELS[reassessment_outcome],
                              "reasoning":reasoning},
           "interpretation":{**interpretation,"erratum_artifact":str(erratum_path) if erratum_path else None},
           "limitations":limitations,
           "timing_decision":timing_decisions,
           "timing_confidence":timing_confidence,
           "decision":list(timing_decisions),
           "artifacts":{"timing_diagnostics":str(timing_path),"per_market_lead_lag_v3":str(pmll_path)}}
    audit_json=reports/f"phase2b_timing_audit_{run_id}.json";audit_md=audit_json.with_suffix(".md")
    atomic_json(audit_json,audit);audit_md.write_text(markdown_timing_audit(audit),encoding="utf-8")
    report["timing_audit"]["artifact"]=str(audit_json)
    market_json=reports/f"phase2b_market_microstructure_{run_id}.json";market_md=market_json.with_suffix(".md")
    research_json=reports/f"phase2b_research_v3_{run_id}.json";research_md=research_json.with_suffix(".md")
    atomic_json(market_json,{k:report[k] for k in ("research_spec_version","data_maturity","btc_to_pm_lead_lag","market_lifecycle_effects","spread_depth_obi_context","latency_decay","limitations","artifacts")})
    market_md.write_text(markdown_legacy(report),encoding="utf-8")
    atomic_json(research_json,report);research_md.write_text(markdown_at(report),encoding="utf-8")
    governance={"research_spec_version":pt.RESEARCH_SPEC_VERSION_V3,"timing_semantics_version":pt.TIMING_SEMANTICS_VERSION,"phase2b_research":"AUTHORIZED_EXPLORATORY","phase2b_confirmed":"NOT_AUTHORIZED","strategy_research":"NOT_AUTHORIZED","pnl_execution":"NOT_AUTHORIZED","primary_dataset_version":PRIMARY_COLLECTOR_VERSION,"cohort_version":COHORT_VERSION}
    atomic_json(state/"phase2b_research_governance.json",governance)
    print(json.dumps({"status":decision,"timing_decision":timing_decisions,"v2_reassessment":reassessment_outcome,"report":str(research_json),"timing_audit":str(audit_json),"per_market_lead_lag":str(pmll_path),"timing_diagnostics":str(timing_path),"cache":{"hits":cache_hits,"misses":cache_misses},"frozen":frozen["status"]},indent=2))
    return 0 if frozen["status"]=="PASS" else 2


if __name__=="__main__":raise SystemExit(main())
