"""Build/update the prospective cohort and run data-quality checkpoints only."""
from __future__ import annotations
import argparse, json, math, re, sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"src"))
import pyarrow.parquet as pq
import numpy as np
from std0_quant.audit.prospective import (
    COHORT_VERSION,PRIMARY_COLLECTOR_VERSION,CohortManifest,atomic_json,covered_calendar_days,
    distribution_summary,feature_missingness,fully_covered_observation,lineage_audit,percentile_summary,
    sanity_audit,trigger_checkpoints,verify_baseline_snapshot,
)
from std0_quant.audit.selection_bias import compute_smd
from std0_quant.audit.feature_drift import _psi,_smd
from std0_quant.config import load_settings,resolve_path
from std0_quant.storage import read_ndjson

METADATA={"feature_row_id","condition_id","prediction_ts_ms","feature_cutoff_ms","cutoff_mode","market_start_ms","market_end_ms","y30","iso_week","online_regime_id","model_eligible","model_ineligible_reason"}
DIST_FIELDS=("y30","initial_qty","first_opp_qty","opp_ratio","initial_to_opp_seconds","seconds_to_expiry","btc_distance_bps","btc_ret_1s","btc_ret_5s","btc_ret_30s","btc_rv_30s","opp_mid","opp_spread","opp_obi_1","opp_obi_3")

def latest(directory:Path,prefix:str)->Path|None:
    files=sorted(directory.glob(f"{prefix}*.parquet"),key=lambda p:p.stat().st_mtime)
    return files[-1] if files else None

def source_context(provenance:list[dict])->dict:
    files=sorted({name for row in provenance for name in str(row.get("source_file") or "").split(";") if name})
    sessions=set();connections=set();collectors=set();schemas=set();btc=[];book=[]
    for name in files:
        path=Path(name)
        if "btc_ticks" in path.parts:btc.append(name)
        if "polymarket_book" in path.parts:book.append(name)
        if path.suffix==".ndjson" and path.exists():
            try:
                for row in read_ndjson(path):
                    if row.get("session_id"):sessions.add(str(row["session_id"]))
                    if row.get("connection_id"):connections.add(str(row["connection_id"]))
                    if row.get("collector_version"):collectors.add(str(row["collector_version"]))
                    if row.get("schema_version"):schemas.add(str(row["schema_version"]))
            except ValueError:pass
    return {"btc_raw_files":btc,"book_raw_files":book,"session_ids":sorted(sessions),"connection_ids":sorted(connections),"collector_versions":sorted(collectors),"parser_versions":sorted(schemas)}

def selection_audit(all_rows:list[dict],covered_ids:set[str])->dict:
    if not covered_ids:return {"status":"NOT_COMPARABLE","covered_n":0,"uncovered_n":0,"comparisons":[]}
    dates={datetime.fromtimestamp(int(r["market_start_ms"])/1000,timezone.utc).date() for r in all_rows if r.get("condition_id") in covered_ids}
    period=[r for r in all_rows if datetime.fromtimestamp(int(r["market_start_ms"])/1000,timezone.utc).date() in dates]
    covered=[r for r in period if r.get("condition_id") in covered_ids];uncovered=[r for r in period if r.get("condition_id") not in covered_ids]
    fields=("y30","initial_qty","first_opp_qty","first_opp_fill_count","initial_to_opp_seconds","seconds_to_expiry","online_regime_id")
    comparisons=[]
    for field in fields:
        smd,note=compute_smd([r.get(field) for r in covered],[r.get(field) for r in uncovered])
        comparisons.append({"variable":field,"smd":smd,"note":note})
    if not uncovered:return {"status":"NOT_COMPARABLE","covered_n":len(covered),"uncovered_n":0,"comparisons":comparisons}
    material=sum(c["smd"] is not None and abs(c["smd"])>.2 for c in comparisons)
    return {"status":"COVERAGE_SELECTION_WARNING" if material>=2 else "COVERAGE_SELECTION_PASS","covered_n":len(covered),"uncovered_n":len(uncovered),"y30_delta":sum(r["y30"] for r in covered)/len(covered)-sum(r["y30"] for r in uncovered)/len(uncovered),"comparisons":comparisons}

def markdown(report:dict)->str:
    return "\n".join([f"# Prospective Checkpoint {report['checkpoint']}","","**RESEARCH / DATA QUALITY ONLY — NO REAL TRADING**","",f"- Status: **{report['status']}**",f"- Fully covered: {report['cohort']['fully_covered']}",f"- Calendar days: {report['cohort']['calendar_days']}",f"- Cohort version: `{report['cohort']['version']}`",f"- Public timestamp violations: {report['provenance']['public_timestamp_violations']}",f"- Truth timestamp violations: {report['provenance']['truth_timestamp_violations']}",f"- Sanity: {report['sanity']['status']}",f"- Selection: {report['selection']['status']}","","No model fitting or Phase 2B action was run.",""])

def drift_audit(rows:list[dict])->dict:
    weeks=sorted({r.get("iso_week") for r in rows if r.get("iso_week")})
    if len(weeks)<2:return {"status":"NOT_COMPARABLE","records":[]}
    records=[];worst="LOW"
    for field in DIST_FIELDS:
        reference=np.asarray([float(r[field]) for r in rows if r.get("iso_week")==weeks[0] and r.get(field) is not None])
        ref_missing=sum(r.get(field) is None for r in rows if r.get("iso_week")==weeks[0])/sum(r.get("iso_week")==weeks[0] for r in rows)
        for week in weeks[1:]:
            group=[r for r in rows if r.get("iso_week")==week];actual=np.asarray([float(r[field]) for r in group if r.get(field) is not None]);psi=_psi(reference,actual);smd=_smd(reference,actual);label="UNDEFINED" if psi is None else "LOW" if psi<.1 else "NOTICEABLE" if psi<=.25 else "MATERIAL"
            if label=="MATERIAL":worst="MATERIAL"
            elif label=="NOTICEABLE" and worst=="LOW":worst="NOTICEABLE"
            records.append({"feature":field,"week":week,"psi":psi,"smd":smd,"missing_rate":sum(r.get(field) is None for r in group)/len(group),"missing_rate_drift":sum(r.get(field) is None for r in group)/len(group)-ref_missing,"label":label})
    return {"status":worst,"records":records}

def execute(checkpoint:int|None=None,manual:bool=False)->list[Path]:
    settings=load_settings();features_dir=resolve_path(settings,"derived")/"features";state=resolve_path(settings,"state");reports=resolve_path(settings,"reports")
    feature_path=latest(features_dir,"pretrade_features_");prov_path=latest(features_dir,"feature_provenance_")
    if feature_path is None or prov_path is None:raise RuntimeError("no feature/provenance artifacts")
    features=pq.read_table(feature_path).to_pylist();provenance=pq.read_table(prov_path).to_pylist();ledger_path=resolve_path(settings,"derived")/"event_ledger.parquet";ledger=pq.read_table(ledger_path).to_pylist();ledger_by={r["condition_id"]:r for r in ledger}
    snapshot_path=state/"baseline_truth_snapshot.json"
    if not snapshot_path.exists():raise RuntimeError("run init_prospective_baseline.py first")
    invariance=verify_baseline_snapshot(json.loads(snapshot_path.read_text(encoding="utf-8")),ledger)
    if invariance["status"]!="PASS":raise RuntimeError("historical baseline invariance failure")
    prov_by=defaultdict(list)
    for row in provenance:prov_by[row.get("condition_id")].append(row)
    manifest=CohortManifest(state/"prospective_cohort.json");manifest.freeze_primary();candidates=[];lineages={};contexts={}
    for feature in features:
        if feature.get("cutoff_mode")!="cutoff_1" or not feature.get("model_eligible"):continue
        cid=feature["condition_id"];rows=prov_by[cid];lineage=lineage_audit(feature,rows);context=source_context(rows);truth=ledger_by.get(cid,{})
        lineages[cid]=lineage;contexts[cid]=context;sanity=sanity_audit([feature])
        start=int(feature["market_start_ms"]);date=datetime.fromtimestamp(start/1000,timezone.utc)
        collector_version=PRIMARY_COLLECTOR_VERSION if context["collector_versions"] and set(context["collector_versions"])=={PRIMARY_COLLECTOR_VERSION} else ";".join(context["collector_versions"]) or None
        candidates.append({"condition_id":cid,"prediction_ts_ms":int(feature["prediction_ts_ms"]),"market_start_ms":start,"calendar_date":date.date().isoformat(),"iso_week":feature["iso_week"],"cutoff_mode":"cutoff_1","coverage_pass":True,"provenance_pass":lineage["status"]=="LINEAGE_PASS","sanity_pass":sanity["status"]=="PASS","lineage_pass":lineage["status"]=="LINEAGE_PASS","included_run_id":feature_path.stem,"first_seen_session_id":context["session_ids"][0] if context["session_ids"] else None,"feature_artifact":str(feature_path),"provenance_artifact":str(prov_path),"feature_row_id":feature.get("feature_row_id"),"collector_version":collector_version,"parser_version":";".join(context["parser_versions"]) or None,"feature_version":"point_in_time_v2_valid_book","slug":truth.get("slug")})
    update=manifest.upsert(candidates,COHORT_VERSION);observations=manifest.observations(COHORT_VERSION);eligible=[r for r in observations if fully_covered_observation(r)];covered_ids={r["condition_id"] for r in eligible};count=len(covered_ids);days=covered_calendar_days(eligible)
    first_path=reports/f"first_fully_covered_v4_{eligible[0]['condition_id']}.json" if eligible else None
    if eligible and first_path and not first_path.exists():
        obs=eligible[0];feature=next(r for r in features if r.get("condition_id")==obs["condition_id"]);truth=ledger_by[obs["condition_id"]];lineage=lineages.get(obs["condition_id"],lineage_audit(feature,prov_by[obs["condition_id"]]));context=contexts.get(obs["condition_id"],source_context(prov_by[obs["condition_id"]]))
        fill_files=[];fill_api_references=[]
        for path in resolve_path(settings,"raw_std0_trades").rglob("*.ndjson"):
            matches=[row for row in read_ndjson(path) if (row.get("record") or {}).get("conditionId")==obs["condition_id"]]
            if matches:
                fill_files.append(str(path));fill_api_references.extend({"raw_file":str(path),"sync_run_id":row.get("sync_run_id"),"fetched_at_ms":row.get("fetched_at_ms"),"transaction_hash":(row.get("record") or {}).get("transactionHash")} for row in matches)
        episode_rows=pq.read_table(resolve_path(settings,"derived")/"episodes.parquet",filters=[("market_id","=",obs["condition_id"])]).to_pylist()
        parent=next((r for r in episode_rows if r.get("episode_start_ms")==truth.get("first_opp_start_ms") and r.get("episode_end_ms")==truth.get("first_opp_end_ms")),None)
        constituent_ids=set(parent.get("constituent_fill_ids") or []) if parent else set()
        normalized_rows=pq.read_table(resolve_path(settings,"normalized")/"fills.parquet",filters=[("condition_id","=",obs["condition_id"])]).to_pylist()
        normalized_lineage=[r for r in normalized_rows if r.get("fill_id") in constituent_ids]
        artifact={"condition_id":obs["condition_id"],"slug":truth.get("slug"),"market_start_ms":truth.get("market_start_ms"),"market_end_ms":truth.get("market_end_ms"),"initial_direction":truth.get("initial_direction"),"first_opp_start_ms":truth.get("first_opp_start_ms"),"first_opp_end_ms":truth.get("first_opp_end_ms"),"prediction_ts_ms":feature["prediction_ts_ms"],"feature_cutoff_ms":feature["feature_cutoff_ms"],"y30":feature["y30"],"y30_horizon_eligible":truth.get("y30_horizon_eligible"),"btc_pre30_coverage_pct":feature.get("btc_pre30_coverage_pct"),"book_pre10_coverage_pct":feature.get("book_pre10_coverage_pct"),"btc_source_timestamp_min":min((r.get("source_timestamp_min_ms") for r in prov_by[obs["condition_id"]] if r.get("source_type")=="binance_btc" and r.get("source_timestamp_min_ms") is not None),default=None),"btc_source_timestamp_max":max((r.get("source_timestamp_max_ms") for r in prov_by[obs["condition_id"]] if r.get("source_type")=="binance_btc" and r.get("source_timestamp_max_ms") is not None),default=None),"book_source_timestamp_min":min((r.get("source_timestamp_min_ms") for r in prov_by[obs["condition_id"]] if r.get("source_type")=="polymarket_book" and r.get("source_timestamp_min_ms") is not None),default=None),"book_source_timestamp_max":max((r.get("source_timestamp_max_ms") for r in prov_by[obs["condition_id"]] if r.get("source_type")=="polymarket_book" and r.get("source_timestamp_max_ms") is not None),default=None),"std0_source_timestamp_max":max((r.get("source_timestamp_max_ms") for r in prov_by[obs["condition_id"]] if r.get("source_type")=="phase1_truth" and r.get("source_timestamp_max_ms") is not None),default=None),**context,"fill_source_files":fill_files,"fill_raw_api_references":fill_api_references,"parent_episode":parent,"normalized_first_opposite_fills":normalized_lineage,"feature_row_id":feature.get("feature_row_id"),"provenance_row_count":len(prov_by[obs["condition_id"]]),**lineage}
        atomic_json(first_path,artifact);first_path.with_suffix(".md").write_text(f"# First Fully-Covered Observation\n\n- condition_id: `{obs['condition_id']}`\n- status: **{lineage['status']}**\n- public cutoff violations: {len(lineage['public_timestamp_violations'])}\n- truth violations: {len(lineage['truth_timestamp_violations'])}\n",encoding="utf-8")
    due=trigger_checkpoints(count,state/"prospective_checkpoint_state.json",checkpoint if manual else None)
    if checkpoint is not None and not manual:due=[checkpoint] if count>=checkpoint else [checkpoint]
    if checkpoint is None and not due:due=[]
    outputs=[]
    for point in due:
        selected=[r for r in features if r.get("condition_id") in covered_ids][:point]
        selected_prov=[r for r in provenance if r.get("condition_id") in {x["condition_id"] for x in selected}]
        lineage_results=[lineage_audit(r,prov_by[r["condition_id"]]) for r in selected]
        public_v=sum(len(x["public_timestamp_violations"]) for x in lineage_results);truth_v=sum(len(x["truth_timestamp_violations"]) for x in lineage_results)
        duplicate_prov=len(selected_prov)-len({(r.get("condition_id"),r.get("feature_name"),r.get("source_type")) for r in selected_prov})
        source_contexts=[source_context(prov_by[r["condition_id"]]) for r in selected];manifest_files={name for path in state.glob("manifest_*.json") for name in json.loads(path.read_text(encoding="utf-8")).get("files",[])}
        selected_source_files={name for row in selected_prov for name in str(row.get("source_file") or "").split(";") if name and "event_ledger" not in name}
        report={"checkpoint":point,"run_id":datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),"status":"PASS" if count>=point and not public_v and not truth_v else "NOT_REACHED" if count<point else "DATA_QUALITY_WARNING","cohort":{"version":COHORT_VERSION,"fully_covered":count,"calendar_days":days,"update":update},"historical_baseline_invariance":invariance,"coverage":{"btc_pre30":percentile_summary([r.get("btc_pre30_coverage_pct") for r in selected]),"book_pre10":percentile_summary([r.get("book_pre10_coverage_pct") for r in selected]),"exactly_at_threshold_btc":sum(r.get("btc_pre30_coverage_pct")==.99 for r in selected),"exactly_at_threshold_book":sum(r.get("book_pre10_coverage_pct")==.99 for r in selected)},"missingness":feature_missingness(selected,METADATA),"provenance":{"feature_provenance_rows":len(selected_prov),"features_per_observation":len(selected_prov)/len(selected) if selected else None,"public_timestamp_violations":public_v,"truth_timestamp_violations":truth_v,"missing_provenance":sum(x["status"]=="LINEAGE_FAIL" for x in lineage_results),"duplicate_provenance":duplicate_prov,"unknown_source_file":sum(len(x["missing_source_files"]) for x in lineage_results),"missing_session_reference":sum(not c["session_ids"] for c in source_contexts),"missing_manifest_reference":len(selected_source_files-manifest_files)},"selection":selection_audit(features,covered_ids),"sanity":sanity_audit(selected),"distribution":distribution_summary(selected,DIST_FIELDS,("iso_week","online_regime_id","initial_direction_up")) if point>=500 and count>=point else [],"drift":drift_audit(selected) if point>=1000 and count>=point else {"status":"NOT_RUN_BELOW_1000","records":[]},"research_models_run":False}
        jp=reports/f"prospective_checkpoint_{point}_{report['run_id']}.json";mp=jp.with_suffix(".md");atomic_json(jp,report);mp.write_text(markdown(report),encoding="utf-8");outputs.extend((jp,mp))
    print(json.dumps({"cohort":str(manifest.path),"fully_covered":count,"calendar_days":days,"outputs":[str(p) for p in outputs]},indent=2));return outputs

def main(argv=None)->int:
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--checkpoint",type=int,choices=(1,100,500,1000,5000));parser.add_argument("--manual-rerun",action="store_true");args=parser.parse_args(argv)
    execute(args.checkpoint,args.manual_rerun);return 0
if __name__=="__main__":raise SystemExit(main())
