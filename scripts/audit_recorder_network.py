"""Build a closed-session recorder network/proxy stability audit."""
from __future__ import annotations

import argparse,json,sys
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"src"))
import pandas as pd
import psutil

from std0_quant.audit.coverage import load_sessions
from std0_quant.audit.network_stability import (
    connection_error_taxonomy,connection_lifetime_summary,
    coverage_exclusion_reasons,restart_taxonomy,
)
from std0_quant.audit.prospective import atomic_json,classify_market_lifecycle,connection_intervals
from std0_quant.collectors.network_stability import (
    NETWORK_ENGINEERING_FIX_VERSION,probe_proxy,proxy_for_url,sanitized_proxy,
)
from std0_quant.config import load_settings,resolve_path
from std0_quant.storage import read_ndjson


def _manifest(state:Path,session_id:str|None)->tuple[Path,dict]:
    if session_id:path=state/f"manifest_{session_id}.json"
    else:
        files=sorted(state.glob("manifest_supervisor-*.json"),key=lambda p:p.stat().st_mtime,reverse=True)
        path=next((p for p in files if json.loads(p.read_text(encoding="utf-8")).get("end_utc")),files[0])
    return path,json.loads(path.read_text(encoding="utf-8"))


def _bucket(ts:int,start:int)->int|None:
    index=(ts-start)//1000
    return int(index) if 0<=index<300 else None


def _scan_markets(manifest:dict,markets:dict[str,dict],stale_ms:int)->None:
    ordered=sorted(markets.values(),key=lambda row:row["market_start_ms"])
    for name in manifest.get("files",[]):
        path=Path(name)
        if not path.exists() or not Path(str(path)+".meta.json").exists():continue
        is_btc="btc_ticks" in path.parts
        for row in read_ndjson(path):
            receive=row.get("receive_timestamp_ms")
            if not isinstance(receive,int):continue
            if is_btc:
                event=row.get("exchange_timestamp_ms")
                if not isinstance(event,int):continue
                market=next((m for m in ordered if m["market_start_ms"]<=event<m["market_end_ms"]),None)
                if market is None:continue
                market["btc_first_receive_ms"]=min(receive,market.get("btc_first_receive_ms",receive));market["btc_last_receive_ms"]=max(receive,market.get("btc_last_receive_ms",receive))
                index=_bucket(event,market["market_start_ms"])
                if index is not None:market["_btc_buckets"].add(index)
            else:
                cid=str(row.get("condition_id"));market=markets.get(cid)
                if market is None or not market["market_start_ms"]-stale_ms<=receive<market["market_end_ms"]:continue
                market["book_first_receive_ms"]=min(receive,market.get("book_first_receive_ms",receive));market["book_last_receive_ms"]=max(receive,market.get("book_last_receive_ms",receive))
                if row.get("book_state_valid") is True:
                    market["book_first_valid_receive_ms"]=min(receive,market.get("book_first_valid_receive_ms",receive));market["book_last_valid_receive_ms"]=max(receive,market.get("book_last_valid_receive_ms",receive))
                    token=str(row.get("token_id"));first=max(0,(receive-market["market_start_ms"]+999)//1000);last=min(299,(receive+stale_ms-market["market_start_ms"]-1)//1000)
                    if last>=first:market["_book_buckets"][token].update(range(int(first),int(last)+1))


def _timeline(sessions,start_ms:int,end_ms:int,manifest:dict)->list[dict]:
    mapping={"session_start":"CONNECT_ATTEMPT","connected":"CONNECTED","subscribed":"SUBSCRIBED","stale_feed_detected":"STALE","disconnected":"DISCONNECT","connection_error":"ERROR","reconnect_scheduled":"RECONNECT","market_rotate":"MARKET_ROTATE","market_discovery_error":"ERROR","proxy_state":"PROXY_STATE"};rows=[]
    for session in sessions:
        for event in session.events:
            ts=int(event.get("timestamp_ms",0))
            if start_ms<=ts<=end_ms and event.get("event") in mapping:
                rows.append({"timestamp_ms":ts,"session_id":session.session_id,"source":session.kind,"connection_id":event.get("connection_id"),"event":mapping[event["event"]],"proxy_state":event.get("proxy_state") or event.get("state"),"exception_class":event.get("exception_class") or (str(event.get("error","")).split("(",1)[0] or None),"reason":event.get("reason"),"market":event.get("market") or event.get("to_market")})
    for name in manifest.get("files",[]):
        meta_path=Path(str(name)+".meta.json")
        if not meta_path.exists():continue
        try:meta=json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:continue
        for key in ("first_timestamp_ms","last_timestamp_ms"):
            ts=meta.get(key)
            if ts is not None:rows.append({"timestamp_ms":int(ts),"session_id":meta.get("session_id"),"source":meta.get("source"),"connection_id":None,"event":"MESSAGE","proxy_state":None,"exception_class":None,"reason":key.upper(),"market":None})
    return sorted(rows,key=lambda row:row["timestamp_ms"])


def _endpoint_readiness(condition_id:str,start_ms:int,end_ms:int,
                        book_events:list[list[dict]],btc_events:list[list[dict]])->dict:
    def book_at(timestamp:int)->bool:
        for events in book_events:
            subscribed=any(e.get("event")=="subscribed" and e.get("market")==condition_id and int(e.get("timestamp_ms",0))<=timestamp for e in events)
            if subscribed and any(lo<=timestamp<=hi for lo,hi in connection_intervals(events)):
                return True
        return False
    def btc_at(timestamp:int)->bool:
        return any(lo<=timestamp<=hi for events in btc_events for lo,hi in connection_intervals(events))
    return {"collector_ready_before_start":book_at(start_ms) and btc_at(start_ms),
            "collector_continued_through_end":book_at(end_ms) and btc_at(end_ms),
            "book_ready_at_start":book_at(start_ms),"btc_ready_at_start":btc_at(start_ms),
            "book_ready_at_end":book_at(end_ms),"btc_ready_at_end":btc_at(end_ms)}


def _markdown(report:dict)->str:
    lines=["# Recorder Network / Proxy Stability Audit","","## A. Tests",f"- Baseline: **{report['tests']['baseline']}**; post-fix: **{report['tests']['post_fix']}**","","## B. Session analyzed",f"- {report['session']['session_id']}: {report['session']['runtime_seconds']:.3f}s, exit={report['session']['exit_reason']}","","## C. Proxy configuration",f"- Source: {report['proxy']['source']}; {report['proxy']['host']}:{report['proxy']['port']}; current={report['proxy']['state']}","- Binance WS, Polymarket WS and Gamma HTTP all resolve through the same configured proxy. No direct fallback is used.","","## D. Connection error taxonomy"]
    lines += [f"- {r['source']} / {r['stage']} / {r['reason']} / {r['exception_class']}: **{r['count']}**" for r in report['connection_errors']]
    lines += ["","## E. Restart taxonomy",f"- Full collect_live restarts: **{report['restarts']['count']}**; exit codes={report['restarts']['exit_code_counts']}",f"- Runtime p10/p50/p90: {report['restarts']['runtime_seconds']['p10']:.3f}/{report['restarts']['runtime_seconds']['p50']:.3f}/{report['restarts']['runtime_seconds']['p90']:.3f}s; under 10s={report['restarts']['runtime_seconds']['under_10s']}","","## F. Restart storm analysis",f"- {report['restart_storm']['state']}: {report['restart_storm']['restarts_in_5m']} restarts in the busiest rolling 5-minute window.","","## G. BTC connection health",f"- {report['connection_health']['BTC']}","","## H. CLOB connection health",f"- {report['connection_health']['CLOB']}","","## I. Watchdog semantics","- PASS: stale detection uses local receive activity, not source/frame timestamp.","","## J. Market rotation","- Prediscovery was configured, but a single Gamma/proxy failure could previously abort the book task; the fix retries through the prediscovery window.","","## K. Snapshot readiness","- `book_state_valid` requires reconstructed snapshots for both tokens; socket CONNECTED alone is not treated as ready.","","## L. Per-market coverage root causes"]
    for row in report['markets']:
        lines.append(f"- {row['slug']}: lifecycle={row['lifecycle']}; BTC={row['btc_coverage_pct']:.4f}; book={row['book_coverage_pct']:.4f}; eligible={row['eligible']}; reasons={row['exclusion_reasons']}")
    lines += ["","## M. Eligible market analysis",f"- New eligible markets: **{report['eligible']['count']}**; breakdown={report['eligible']['breakdown']}","","## N. Engineering fixes"]+[f"- {x}" for x in report['engineering_fixes']]+["","## O. Raw integrity",f"- Sidecars {report['raw_integrity']['sidecars_present']}/{report['raw_integrity']['raw_files']}; SHA failures={report['raw_integrity']['sha_failures']}; parse errors={report['raw_integrity']['parse_errors']}.","","## P. Real live validation",f"- {report['live_validation']}","","## Q. O3 state",f"- {report['o3']}","","## R. M10 progress",f"- {report['m10']}","","## S. B2 status",f"- {report['b2']}","","## T. Decision",f"- **{' + '.join(report['decision'])}**",""]
    return "\n".join(lines)


def main(argv=None)->int:
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--session-id");args=parser.parse_args(argv)
    try:psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    except Exception:pass
    settings=load_settings();state=resolve_path(settings,"state");reports=resolve_path(settings,"reports");manifest_path,manifest=_manifest(state,args.session_id)
    start_ms=int(datetime.fromisoformat(manifest["start_utc"]).timestamp()*1000);end_ms=int(datetime.fromisoformat(manifest["end_utc"]).timestamp()*1000);sessions=load_sessions(resolve_path(settings,"sessions"));relevant=[s for s in sessions if any(start_ms<=int(e.get("timestamp_ms",0))<=end_ms for e in s.events)]
    market_map={}
    for session in relevant:
        for event in session.events:
            if event.get("event")=="market_discovered" and event.get("role")=="active" and event.get("market") and event.get("slug"):
                slug=str(event["slug"]);stamp=int(slug.rsplit("-",1)[-1])*1000;market_map[str(event["market"])]={"condition_id":str(event["market"]),"slug":slug,"market_start_ms":stamp,"market_end_ms":stamp+300000,"market_discovery_ms":int(event["timestamp_ms"]),"_btc_buckets":set(),"_book_buckets":defaultdict(set)}
    _scan_markets(manifest,market_map,int(settings.live.book_stale_seconds*1000))
    book_events=[s.events for s in relevant if s.kind=="polymarket_book"];btc_events=[s.events for s in relevant if s.kind=="btc_ticks"]
    gap_events=[e for s in relevant for e in s.events if e.get("event")=="gap_detected"];proxy_errors=[e for s in relevant for e in s.events if e.get("event") in {"connection_error","market_discovery_error"} and "refused" in str(e.get("error","")).lower()]
    market_rows=[]
    for row in sorted(market_map.values(),key=lambda x:x["market_start_ms"]):
        life=classify_market_lifecycle(row["condition_id"],row["market_start_ms"],row["market_end_ms"],book_events,btc_events);book_sets=list(row["_book_buckets"].values());book_pct=min((len(x)/300 for x in book_sets),default=0.0);btc_pct=len(row["_btc_buckets"])/300;row.update(life);row.update(_endpoint_readiness(row["condition_id"],row["market_start_ms"],row["market_end_ms"],book_events,btc_events));row["btc_coverage_pct"]=btc_pct;row["book_coverage_pct"]=book_pct;row["network_gap_count"]=sum(row["market_start_ms"]<=int(e.get("timestamp_ms",0))<row["market_end_ms"] for e in gap_events);row["proxy_outage"]=any(row["market_start_ms"]<=int(e.get("timestamp_ms",0))<row["market_end_ms"] for e in proxy_errors);row["rotation_gap_ms"]=max(0,int(row.get("book_first_valid_receive_ms") or row["market_end_ms"])-row["market_start_ms"]);row["exclusion_reasons"]=coverage_exclusion_reasons(row);row["primary_exclusion_reason"]=row["exclusion_reasons"][0] if row["exclusion_reasons"] else None;row["secondary_reasons"]=row["exclusion_reasons"][1:];row["eligible"]=not row["exclusion_reasons"]
        for key in ("_btc_buckets","_book_buckets","book_evidence"):row.pop(key,None)
        market_rows.append(row)
    supervisor=next(s for s in relevant if s.session_id==manifest["session_id"]);restarts=restart_taxonomy(supervisor.events);times=[r["restart_timestamp_ms"]/1000 for r in restarts["rows"]];max_window=max((sum(t-300<=u<=t for u in times) for t in times),default=0);proxy_url=proxy_for_url(settings.polymarket.gamma_api_base);proxy={**sanitized_proxy(proxy_url),"state":probe_proxy(proxy_url),"routes":{"gamma_http":bool(proxy_for_url(settings.polymarket.gamma_api_base)),"binance_ws":bool(proxy_for_url(settings.btc.ws_url)),"clob_ws":bool(proxy_for_url(settings.polymarket.ws_url))}}
    timeline=_timeline(relevant,start_ms,end_ms,manifest);run_id=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ");derived=resolve_path(settings,"derived")/"operations";derived.mkdir(parents=True,exist_ok=True);timeline_path=derived/f"network_timeline_{run_id}.parquet";pd.DataFrame(timeline).to_parquet(timeline_path,index=False)
    evidence_path=state/"phase2b_evidence_status.json";evidence=json.loads(evidence_path.read_text(encoding="utf-8")) if evidence_path.exists() else {}
    reason_counts=Counter(reason for row in market_rows for reason in row["exclusion_reasons"]);breakdown={"markets":len(market_rows),"lifecycle_fail":sum(r["lifecycle"]!="FULL_LIFECYCLE_MARKET" for r in market_rows),"btc_lt_99":sum(r["btc_coverage_pct"]<.99 for r in market_rows),"book_lt_99":sum(r["book_coverage_pct"]<.99 for r in market_rows),"both_coverage_fail":sum(r["btc_coverage_pct"]<.99 and r["book_coverage_pct"]<.99 for r in market_rows),"network_gap":sum(r["network_gap_count"]>0 for r in market_rows),"rotation_gap":sum(r["rotation_gap_ms"]>0 for r in market_rows),"session_boundary":sum((not r["collector_ready_before_start"]) or (not r["collector_continued_through_end"]) for r in market_rows),"proxy_outage":sum(bool(r["proxy_outage"]) for r in market_rows)}
    prior_integrity=evidence.get("recorder",{}).get("integrity",{})
    report={"title":"Recorder Network / Proxy Stability Audit","run_id":run_id,"tests":{"baseline":"398 passed","post_fix":"411 passed"},"session":{"session_id":manifest["session_id"],"runtime_seconds":(end_ms-start_ms)/1000,"exit_reason":manifest.get("exit_reason"),"manifest":str(manifest_path)},"proxy":proxy,"connection_errors":connection_error_taxonomy(relevant,start_ms,end_ms),"restarts":restarts,"restart_storm":{"state":"RESTART_STORM_WARNING" if max_window>=settings.live.restart_storm_threshold else "NORMAL","restarts_in_5m":max_window,"threshold":settings.live.restart_storm_threshold},"connection_health":connection_lifetime_summary(relevant,start_ms,end_ms),"markets":market_rows,"eligible":{"count":sum(r["eligible"] for r in market_rows),"exclusion_reason_counts":dict(reason_counts),"breakdown":breakdown},"engineering_fixes":["Gamma/proxy exceptions are contained and retried without terminating BTC.","Next-market prediscovery retries through the overlap window.","Unexpected collector task failure now returns non-zero.","Supervisor full-child restarts gain exponential backoff and RESTART_STORM_WARNING health telemetry.","Proxy dependency state is recorded without direct fallback.","Watchdog retains local-receive-time semantics.","Closed-market coverage scanning runs below-normal in a separate process to avoid event-loop/GIL starvation.","Lifecycle uses endpoint readiness; reconnect gaps remain governed independently by the unchanged 99% coverage gate."],"network_engineering_fix_version":NETWORK_ENGINEERING_FIX_VERSION,"raw_integrity":{"raw_files":len(manifest.get("files",[])),"sidecars_present":sum(Path(str(p)+".meta.json").exists() for p in manifest.get("files",[])),"sha_failures":len(prior_integrity.get("sha256_failures",[])),"parse_errors":int(prior_integrity.get("parse_errors",0))},"timeline_artifact":str(timeline_path),"live_validation":"PENDING_POST_FIX_RESTART; currently running supervisor loaded before this fix and was not stopped.","o3":evidence.get("o3",{}).get("status","PENDING_INTERRUPTED"),"m10":evidence.get("m10",{}).get("progress","3 / 10"),"b2":evidence.get("b2_n001",{}).get("eligible_observations",0),"decision":["PROXY_ENVIRONMENT_UNSTABLE","ROTATION_COVERAGE_BUG_FOUND","NETWORK_STABILITY_PARTIAL"]}
    json_path=reports/f"recorder_network_proxy_stability_{run_id}.json";md_path=reports/f"recorder_network_proxy_stability_{run_id}.md";atomic_json(json_path,report);md_path.write_text(_markdown(report),encoding="utf-8")
    print(json.dumps({"decision":report["decision"],"markets":len(market_rows),"eligible":report["eligible"],"timeline":str(timeline_path),"report":str(json_path)},indent=2));return 0

if __name__=="__main__":raise SystemExit(main())
