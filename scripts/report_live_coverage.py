"""Generate daily prospective and rolling operations data-quality reports."""
from __future__ import annotations
import argparse,json,re,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"src"))
import pyarrow.parquet as pq
from std0_quant.audit.coverage import FileCoverageProvider,load_sessions
from std0_quant.audit.prospective import COHORT_VERSION,CohortManifest,atomic_json,build_operations_summary,classify_market_lifecycle,covered_calendar_days,fully_covered_observation,schema_profile,verify_raw_sidecars
from std0_quant.collectors.live_health import build_health
from std0_quant.config import load_settings,resolve_path
from std0_quant.storage import read_ndjson

BOOK_EXPECTED={"source","schema_version","receive_timestamp_ms","exchange_timestamp_ms","condition_id","token_id","outcome","event_type","raw_message","collector_version","session_id","connection_id","book_state_valid","book_state_status"}
BTC_EXPECTED={"source","schema_version","receive_timestamp_ms","exchange_timestamp_ms","price","size","trade_id","raw_message","collector_version","session_id","connection_id"}

def _schema_rows(root:Path,limit:int=20000):
    count=0
    for path in sorted(root.rglob("*.ndjson"),key=lambda p:p.stat().st_mtime,reverse=True):
        try:
            for row in read_ndjson(path):
                yield row;count+=1
                if count>=limit:return
        except ValueError:continue

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--hours",type=float,default=24);args=parser.parse_args(argv)
    settings=load_settings();sessions=load_sessions(resolve_path(settings,"sessions"));now=int(datetime.now(timezone.utc).timestamp()*1000);floor=now-int(args.hours*3600*1000)
    timestamps=[int(e.get("timestamp_ms",0)) for s in sessions for e in s.events if int(e.get("timestamp_ms",0))>=floor];audit_start=min(timestamps,default=floor);audit_end=max(timestamps,default=now)
    raw_roots=[resolve_path(settings,"raw_btc_ticks"),resolve_path(settings,"raw_polymarket_book")];raw_files=[p for root in raw_roots for p in root.rglob("*.ndjson")]
    opened={str(e.get("file")) for s in sessions for e in s.events if e.get("event")=="file_open"};closed={str(e.get("file")) for s in sessions for e in s.events if e.get("event")=="file_close"};active={name for name in opened-closed if Path(name).exists() and not Path(name+".meta.json").exists()}
    integrity=verify_raw_sidecars([p for p in raw_files if str(p) not in active]);integrity["raw_file_count"]=len(raw_files);integrity["sidecar_count"]=sum(Path(str(p)+".meta.json").exists() for p in raw_files);integrity["active_raw_files"]=sorted(active)
    metas=[]
    for path in raw_files:
        sidecar=Path(str(path)+".meta.json")
        if sidecar.exists():
            try:metas.append(json.loads(sidecar.read_text(encoding="utf-8")))
            except json.JSONDecodeError:pass
    last_by_session={str(m.get("session_id")):int(m.get("last_timestamp_ms") or 0) for m in metas if m.get("session_id")}
    def effective_events(session):
        rows=list(session.events)
        if any(e.get("event")=="connected" for e in rows) and not any(e.get("event") in {"disconnected","session_end"} for e in rows):
            last=last_by_session.get(session.session_id)
            if last:rows.append({"event":"session_end","timestamp_ms":last,"recovered_from_raw_sidecar":True})
        return rows
    book_events=[effective_events(s) for s in sessions if s.kind=="polymarket_book"];btc_events=[effective_events(s) for s in sessions if s.kind=="btc_ticks"];markets={}
    for session in sessions:
        for event in session.events:
            if event.get("event")=="market_discovered" and event.get("role")=="active" and event.get("market") and event.get("slug"):markets[str(event["market"])]=str(event["slug"])
    provider=FileCoverageProvider(resolve_path(settings,"raw_polymarket_book"),resolve_path(settings,"raw_btc_ticks"),resolve_path(settings,"sessions"),settings.coverage.bucket_seconds,settings.coverage.gap_threshold_seconds,settings.live.book_stale_seconds)
    audits=[];market_warnings=[]
    for condition_id,slug in markets.items():
        match=re.fullmatch(r"btc-updown-5m-(\d+)",slug)
        if not match or int(match.group(1))%300:market_warnings.append({"condition_id":condition_id,"slug":slug,"warning":"MARKET_SCHEMA_CHANGE"});continue
        start=int(match.group(1))*1000;end=start+settings.polymarket.book.market_window_seconds*1000
        if end<audit_start or start>audit_end:continue
        lifecycle=classify_market_lifecycle(condition_id,start,end,book_events,btc_events);coverage=provider.market_report(condition_id,start,end);subscribed=next((e.get("tokens",[]) for s in sessions for e in s.events if e.get("event")=="subscribed" and e.get("market")==condition_id),[])
        market_files=[e.get("file") for s in sessions for e in s.events if e.get("event")=="file_open" and e.get("file") and any(x.get("event")=="subscribed" and x.get("market")==condition_id for x in s.events)]
        collector_version=None
        for name in market_files:
            try:collector_version=next(read_ndjson(name)).get("collector_version");break
            except (OSError,ValueError,StopIteration):continue
        if len(subscribed)!=2:market_warnings.append({"condition_id":condition_id,"slug":slug,"warning":"MARKET_SCHEMA_CHANGE","token_count":len(subscribed)})
        audits.append({"slug":slug,"collector_version":collector_version,"market_start_ms":start,"market_end_ms":end,**lifecycle,"btc_coverage_pct":coverage["btc_coverage_pct"],"book_coverage_pct":coverage["book_coverage_pct"]})
    operations=build_operations_summary(audit_start,audit_end,sessions,audits,integrity,metas);schema={"btc":schema_profile(_schema_rows(raw_roots[0]),BTC_EXPECTED),"book":schema_profile(_schema_rows(raw_roots[1]),BOOK_EXPECTED),"market_metadata_warnings":market_warnings}
    if schema["btc"]["status"]!="PASS" or schema["book"]["status"]!="PASS" or market_warnings:
        if operations["status"]=="PASS":operations["status"]="DATA_QUALITY_WARNING"
    subprocess.run([sys.executable,str(ROOT/"scripts/run_prospective_checkpoint.py")],cwd=ROOT,check=False,capture_output=True,text=True)
    manifest=CohortManifest(resolve_path(settings,"state")/"prospective_cohort.json");observations=manifest.observations(COHORT_VERSION);eligible=[r for r in observations if fully_covered_observation(r)];fully=len(eligible);days=covered_calendar_days(eligible)
    ledger=pq.read_table(resolve_path(settings,"derived")/"event_ledger.parquet").to_pylist();new_fo=sum(bool(r.get("first_opp_end_ms") is not None and audit_start<=int(r["first_opp_end_ms"])<=audit_end) for r in ledger);new_y30=sum(bool(r.get("y30_horizon_eligible") and r.get("first_opp_end_ms") is not None and audit_start<=int(r["first_opp_end_ms"])<=audit_end) for r in ledger)
    health=build_health(settings);healthy_day=operations["operations_24h"]["eligible_for_audit"] and operations["full_lifecycle_coverage"]["status"]=="PASS" and operations["status"]=="PASS";readiness="READY_FOR_PHASE2A_REVALIDATION" if fully>=5000 and days>=14 and operations["status"]!="RECORDER_INTEGRITY_FAILURE" else "RECORDER_INTEGRITY_FAILURE" if operations["status"]=="RECORDER_INTEGRITY_FAILURE" else "DATA_QUALITY_WARNING" if operations["status"]=="DATA_QUALITY_WARNING" else "ACCUMULATING_LIVE_DATA"
    day=datetime.now(timezone.utc).date().isoformat();new_fully=sum(audit_start<=int(r.get("prediction_ts_ms",0))<=audit_end for r in eligible);payload={"date_utc":day,"cohort_version":COHORT_VERSION,"operations":operations,"markets":audits,"schema":schema,"new_first_opposite":new_fo,"new_observable_y30":new_y30,"new_fully_covered":new_fully,"cumulative_fully_covered":fully,"covered_calendar_days":days,"healthy_recorder_day":healthy_day,"point_in_time_violations":sum(not r.get("lineage_pass",False) for r in observations),"provenance_violations":sum(not r.get("provenance_pass",False) for r in observations),"schema_warnings":sum(x["status"]!="PASS" for x in (schema["btc"],schema["book"]))+len(market_warnings),"sanity_warnings":sum(not r.get("sanity_pass",False) for r in observations),"disk_free_gb":health["disk_free_gb"],"readiness_status":readiness}
    out=resolve_path(settings,"reports");stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ");ops_json=out/f"live_operations_24h_{stamp}.json";ops_md=ops_json.with_suffix(".md");daily_json=out/f"prospective_daily_{day.replace('-','')}.json";daily_md=daily_json.with_suffix(".md")
    atomic_json(ops_json,{"operations":operations,"markets":audits,"schema":schema});ops_md.write_text(f"# Live Operations Audit\n\n- Observed span: {operations['runtime_seconds']:.0f}s\n- True continuous 24h: **{operations['operations_24h']['status']}**\n- Longest continuous supervisor session: {operations['operations_24h']['longest_continuous_runtime_seconds']:.0f}s\n- Full lifecycle markets: {operations['full_lifecycle_markets']}\n- Partial markets: {operations['partial_session_markets']}\n- Missed markets: {len(operations['missed_markets'])}\n- In/post-market stale: {operations['in_market_stale']}/{operations['post_market_stale']}\n- BTC coverage p05: {operations['full_lifecycle_coverage']['btc']['p05']}\n- Book coverage p05: {operations['full_lifecycle_coverage']['book']['p05']}\n- Status: **{operations['status']}**\n",encoding="utf-8")
    atomic_json(daily_json,payload);daily_md.write_text(f"# Prospective Daily {day}\n\n- Runtime: {operations['runtime_seconds']:.0f}s\n- Full/partial/missed: {operations['full_lifecycle_markets']}/{operations['partial_session_markets']}/{len(operations['missed_markets'])}\n- BTC/book records: {operations['btc_records']}/{operations['book_records']}\n- Fully covered: {fully}/5000\n- Calendar days: {days}/14\n- Healthy recorder day: {healthy_day}\n- Status: **{readiness}**\n",encoding="utf-8");atomic_json(out/f"live_coverage_{day.replace('-','')}.json",payload)
    print(ops_json);print(ops_md);print(daily_json);print(daily_md);return 0 if operations["status"]!="RECORDER_INTEGRITY_FAILURE" else 3
if __name__=="__main__":raise SystemExit(main())
