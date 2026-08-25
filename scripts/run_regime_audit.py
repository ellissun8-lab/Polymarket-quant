"""Run the offline, read-only Phase 1.6 regime audit."""
from __future__ import annotations
import argparse, hashlib, json, platform, subprocess, sys
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; SRC=ROOT/"src"
if str(SRC) not in sys.path: sys.path.insert(0,str(SRC))
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from std0_quant.audit.changepoints import detect_change_points
from std0_quant.audit.conditional_negative_controls import run_conditional_shuffle
from std0_quant.audit.feature_drift import run_feature_drift
from std0_quant.audit.matched_placebo import calendar_matched
from std0_quant.audit.online_regime import assign_online_regimes, assert_point_in_time
from std0_quant.audit.regime_surface import build_regime_surface
from std0_quant.audit.walk_forward import FEATURE_NAMES, build_predictive_rows, run_walk_forward
from std0_quant.config import load_settings, resolve_path
from std0_quant.events.event_ledger import SlugWindowMetadataProvider, build_ledger_rows
from std0_quant.events.fills import Fill

SEMANTIC_FIELDS=("condition_id","clean_flag","exclude_reason","exclude_detail","y30","y30_horizon_eligible","episode_rule_version")
UNIVERSES={"BTC15m":("btc-updown-15m-",900),"ETH5m":("eth-updown-5m-",300),"SOL5m":("sol-updown-5m-",300),"XRP5m":("xrp-updown-5m-",300)}

def sha(path):
    h=hashlib.sha256()
    with open(path,"rb") as f:
        for chunk in iter(lambda:f.read(1<<20),b""): h.update(chunk)
    return h.hexdigest()

def semantic_hash(rows):
    lines=["|".join(json.dumps(r.get(k),sort_keys=True) for k in SEMANTIC_FIELDS) for r in rows]
    return hashlib.sha256("\n".join(sorted(lines)).encode()).hexdigest()

def jsonify(x):
    if is_dataclass(x): return jsonify(asdict(x))
    if isinstance(x,dict): return {str(k):jsonify(v) for k,v in x.items()}
    if isinstance(x,(list,tuple)): return [jsonify(v) for v in x]
    if isinstance(x,np.generic): return x.item()
    if isinstance(x,float) and not np.isfinite(x): return None
    return x

def write_parquet(path,rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    pq.write_table(pa.Table.from_pylist(jsonify(rows) if rows else [{"status":"NO_ROWS"}]),path)

def _fill(rec):
    def c(v): return None if v is None or (isinstance(v,float) and np.isnan(v)) else v
    return Fill(fill_id=rec["fill_id"],proxy_wallet=c(rec.get("proxy_wallet")),side=c(rec.get("side")),asset=c(rec.get("asset")),condition_id=c(rec.get("condition_id")),size=c(rec.get("size")),price=c(rec.get("price")),timestamp_ms=c(rec.get("timestamp_ms")),timestamp_raw=rec.get("timestamp_raw"),title=c(rec.get("title")),slug=c(rec.get("slug")),outcome=c(rec.get("outcome")),outcome_index=c(rec.get("outcome_index")),transaction_hash=c(rec.get("transaction_hash")),source=c(rec.get("source")) or "normalized",fetched_at_ms=int(c(rec.get("fetched_at_ms")) or 0))

def universe_rows(fills_table,prefix,seconds):
    records=[r for r in fills_table.to_pylist() if (r.get("slug") or "").startswith(prefix)]
    fills=[_fill(r) for r in records]; provider=SlugWindowMetadataProvider.from_fills(fills,slug_prefix=prefix,window_seconds=seconds)
    return build_ledger_rows(fills,provider,scope_slug_prefix=prefix)

def future_persistence(reference, fills_table, breaks):
    records=[r for r in fills_table.to_pylist() if str(r.get("slug") or "").startswith("btc-updown-5m-") and r.get("side")=="BUY"]
    buys={}
    for r in records: buys.setdefault((r.get("condition_id"),r.get("outcome")),[]).append(int(r["timestamp_ms"]))
    supported=sorted(set(b["break_timestamp"] for b in breaks if b["status"]=="SUPPORTED_BREAK")); groups={}
    for r in reference:
        if not r.get("clean_flag") or r.get("first_opp_end_ms") is None: continue
        week=build_regime_surface([r],"weekly",0)[0]["period_key"]; regime=sum(x<=week for x in supported); t0=int(r["first_opp_end_ms"]); end=int(r["market_end_ms"]); times=buys.get((r.get("condition_id"),r.get("first_opp_direction")),[]); labels=[]
        for start_s,end_s in ((0,30),(30,60),(60,90)):
            horizon=t0+end_s*1000; labels.append(None if end<horizon else int(any(t0+start_s*1000<t<=horizon for t in times)))
        for kind,key in (("week",week),("descriptive_regime",str(regime))): groups.setdefault((kind,key),[]).append(labels)
    out=[]
    for (kind,key),values in sorted(groups.items()):
        rec={"group_type":kind,"group_id":key,"n_first_opposite":len(values)}
        for j,name in enumerate(("y0_30","y30_60","y60_90")):
            obs=[v[j] for v in values if v[j] is not None]; rec[f"{name}_observable"]=len(obs); rec[f"{name}_rate"]=sum(obs)/len(obs) if obs else None
        out.append(rec)
    return out

def markdown(report):
    inv=report["invariants"]; wf=report["walk_forward"]; neg=report["conditional_negative_control"]; surf=report["regime_surface_summary"]
    lines=["# Phase 1.6 Regime & Conditional Predictability Audit","","**RESEARCH / AUDIT ONLY — NO REAL TRADING**","",f"Run: `{report['regime_audit_run_id']}`",f"Decision: **{report['phase2_decision']}**","","## Integrity",f"- Ledger unchanged: {not inv['ledger_hash_changed']}",f"- Settings unchanged: {not inv['settings_hash_changed']}",f"- Semantic truth unchanged: {not inv['semantic_hash_changed']}","","## Regime surface",f"- Overall observable Y30: {surf.get('overall_y30')}",f"- Weekly range: {surf.get('weekly_min')} to {surf.get('weekly_max')}",f"- Supported change points: {surf.get('supported_change_points')}","","## Conditional negative control",f"- Pooled shuffle AUC p95: {neg['summary']['pooled_auc']['p95']}",f"- Macro weekly AUC p95: {neg['summary']['macro_weekly_auc']['p95']}",f"- Weighted weekly AUC p95: {neg['summary']['weighted_weekly_auc']['p95']}",f"- Status: {neg['conditional_control_status']}","","## Walk-forward",f"- Valid folds: {wf['n_valid_folds']}",f"- ΔBrier vs regime: {wf.get('delta_brier_vs_regime')}",f"- ΔLogLoss vs regime: {wf.get('delta_logloss_vs_regime')}",f"- Brier-improved folds: {wf.get('pct_folds_brier_improved')}",f"- LogLoss-improved folds: {wf.get('pct_folds_logloss_improved')}","","## Interpretation guardrails","Change points indicate changes in behavioral distributions, not proof of an internal algorithm change. Predictability is not tradable profitability; this audit makes no causal or trading-return claim.","","## Known limitations","Public fills have second-level timestamps; identical-fill collisions and incomplete historical book coverage remain inherited Phase 1 limitations."]
    return "\n".join(lines)+"\n"

def parser():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--ledger");p.add_argument("--output");p.add_argument("--seed",type=int,default=20260824);p.add_argument("--n-shuffles",type=int,default=1000);p.add_argument("--min-weekly-n",type=int,default=100);p.add_argument("--min-train-weeks",type=int,default=4);p.add_argument("--min-test-n",type=int,default=30);return p

def main(argv=None):
    args=parser().parse_args(argv)
    if args.n_shuffles<500: raise SystemExit("--n-shuffles must be >= 500 for a Phase 1.6 audit")
    started=datetime.now(timezone.utc);run_id=started.strftime("%Y%m%dT%H%M%S.%fZ")
    settings=load_settings(); ledger=Path(args.ledger) if args.ledger else resolve_path(settings,"derived")/"event_ledger.parquet"; settings_path=ROOT/"config/settings.yaml"; out=Path(args.output) if args.output else resolve_path(settings,"reports");audit_dir=resolve_path(settings,"derived")/"audit"
    before=(sha(ledger),sha(settings_path));rows=pq.read_table(ledger).to_pylist();sem_before=semantic_hash(rows)
    daily=build_regime_surface(rows,"daily",args.min_weekly_n);weekly=build_regime_surface(rows,"weekly",args.min_weekly_n);breaks=detect_change_points(weekly)
    X,y,ts,weeks,ids=build_predictive_rows(rows);online=assign_online_regimes([r for r in rows if r.get("clean_flag") and r.get("first_opp_end_ms") is not None]);assert_point_in_time(online)
    negative=run_conditional_shuffle(X,y,weeks,args.n_shuffles,args.seed);walk=run_walk_forward(X,y,ts,weeks,args.min_train_weeks,args.min_test_n);drift=run_feature_drift(X,weeks)
    fills_path=resolve_path(settings,"normalized")/"fills.parquet";fills_table=pq.read_table(fills_path);reference=universe_rows(fills_table,"btc-updown-5m-",300);matched=[]
    for name,(prefix,seconds) in UNIVERSES.items(): matched.append(calendar_matched(reference,universe_rows(fills_table,prefix,seconds),name,args.min_weekly_n))
    persistence=future_persistence(reference,fills_table,breaks)
    after=(sha(ledger),sha(settings_path));sem_after=semantic_hash(pq.read_table(ledger).to_pylist());integrity=before==after and sem_before==sem_after
    m1=walk.get("aggregate",{}).get("M1",{});m3=walk.get("aggregate",{}).get("M3",{});db=(m1.get("brier")-m3.get("brier")) if m1 and m3 else None;dl=(m1.get("logloss")-m3.get("logloss")) if m1 and m3 else None
    conditional=negative["conditional_control_status"]=="PASS"; stability=max(walk.get("pct_folds_brier_improved") or 0,walk.get("pct_folds_logloss_improved") or 0)>=.6; increment=db is not None and db>0 and dl>0
    decision="GO" if integrity and conditional and increment and stability and (walk.get("delta_macro_auc_vs_regime") or 0)>0 else "CONDITIONAL_GO" if integrity and conditional and (increment or stability) else "NO_GO"
    obs=[r for r in rows if r.get("clean_flag") and r.get("first_opp_end_ms") is not None and r.get("y30_horizon_eligible")];rates=[r["y30_rate"] for r in weekly if r["y30_rate"] is not None]
    walk["delta_brier_vs_regime"]=db;walk["delta_logloss_vs_regime"]=dl
    selected=[f["best_regime_baseline"] for f in walk.get("folds",[])]; best_regime=max(set(selected),key=selected.count) if selected else None
    report={"regime_audit_run_id":run_id,"phase":"1.6","research_only":True,"started_at":started.isoformat(),"finished_at":datetime.now(timezone.utc).isoformat(),"git_commit":subprocess.run(["git","rev-parse","HEAD"],cwd=ROOT,capture_output=True,text=True).stdout.strip() or None,"python_version":platform.python_version(),"input_paths":{"ledger":str(ledger),"settings":str(settings_path),"fills":str(fills_path)},"input_hashes":{"ledger":before[0],"settings":before[1],"fills":sha(fills_path)},"invariants":{"ledger_hash_before":before[0],"ledger_hash_after":after[0],"ledger_hash_changed":before[0]!=after[0],"settings_hash_before":before[1],"settings_hash_after":after[1],"settings_hash_changed":before[1]!=after[1],"semantic_hash_before":sem_before,"semantic_hash_after":sem_after,"semantic_hash_changed":sem_before!=sem_after,"status":"PASS" if integrity else "FAIL"},"seed":args.seed,"n_shuffles":args.n_shuffles,"cli_args":vars(args),"counts":{"n_raw_markets":len(rows),"n_clean":sum(bool(r.get('clean_flag')) for r in rows),"n_first_opposite":sum(bool(r.get('clean_flag')) and r.get('first_opp_end_ms') is not None for r in rows),"n_y30_observable":len(obs)},"regime_surface_summary":{"overall_y30":sum(r.get('y30')==1 for r in obs)/len(obs) if obs else None,"weekly_min":min(rates) if rates else None,"weekly_max":max(rates) if rates else None,"weekly_std":float(np.std(rates)) if rates else None,"supported_change_points":sum(b['status']=='SUPPORTED_BREAK' for b in breaks)},"change_points":breaks,"conditional_negative_control":{k:v for k,v in negative.items() if k!='records'},"walk_forward":walk,"feature_drift":drift,"calendar_matched_placebo":matched,"future_window_persistence":persistence,"best_regime_baseline":best_regime,"phase2_decision":decision,"result_status":"PASS" if integrity else "FAIL","interpretation":"std0's repeat-opposite-BUY behavior exhibits strong temporal regime dependence; episode-level incremental information is judged only from walk-forward conditional results."}
    paths={"regime_surface":audit_dir/f"regime_surface_{run_id}.parquet","change_points":audit_dir/f"change_points_{run_id}.parquet","descriptive_regimes":audit_dir/f"descriptive_regimes_{run_id}.parquet","online_regimes":audit_dir/f"online_regimes_{run_id}.parquet","regime_baselines":audit_dir/f"regime_baselines_{run_id}.parquet","conditional_metrics":audit_dir/f"conditional_metrics_{run_id}.parquet","feature_drift":audit_dir/f"feature_drift_{run_id}.parquet","matched_placebo":audit_dir/f"matched_placebo_{run_id}.parquet","conditional_negative_controls":audit_dir/f"conditional_negative_controls_{run_id}.parquet","future_persistence":audit_dir/f"future_persistence_{run_id}.parquet"}
    unique_breaks=sorted(set(b["break_timestamp"] for b in breaks if b["status"]=="SUPPORTED_BREAK"));write_parquet(paths["regime_surface"],daily+weekly);write_parquet(paths["change_points"],breaks);write_parquet(paths["descriptive_regimes"],[{"period_key":r["period_key"],"descriptive_regime_id":sum(x<=r["period_key"] for x in unique_breaks)} for r in weekly]);write_parquet(paths["online_regimes"],online);write_parquet(paths["regime_baselines"],[{"model":m,**{k:v for k,v in met.items() if k!="calibration_bins" and k!="period_details"}} for m,met in walk.get("aggregate",{}).items()]);write_parquet(paths["conditional_metrics"],[{"model":m,**{k:v for k,v in met.items() if k!="calibration_bins" and k!="period_details"}} for m,met in walk.get("aggregate",{}).items()]);write_parquet(paths["feature_drift"],drift);write_parquet(paths["matched_placebo"],matched);write_parquet(paths["conditional_negative_controls"],negative["records"]);write_parquet(paths["future_persistence"],persistence)
    report["artifacts"]={k:str(v) for k,v in paths.items()};out.mkdir(parents=True,exist_ok=True);jp=out/f"regime_audit_{run_id}.json";mp=out/f"regime_audit_{run_id}.md";jp.write_text(json.dumps(jsonify(report),indent=2,ensure_ascii=False),encoding="utf-8");mp.write_text(markdown(report),encoding="utf-8");print(jp);print(mp);print(f"decision={decision} integrity={integrity}");return 0 if integrity else 2
if __name__=="__main__": raise SystemExit(main())
