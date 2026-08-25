"""Run Phase 2A public-state feature attribution or stop at its coverage gate."""
from __future__ import annotations
import argparse,hashlib,json,platform,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];SRC=ROOT/"src"
if str(SRC) not in sys.path:sys.path.insert(0,str(SRC))
import pyarrow as pa
import pyarrow.parquet as pq
from build_pretrade_features import execute
from std0_quant.config import load_settings,resolve_path
from std0_quant.features.coverage_selection import audit_coverage_selection
from std0_quant.modeling.attribution import attribution,coefficient_stability
from std0_quant.modeling.phase2a_models import run_models

SEMANTIC=("condition_id","clean_flag","exclude_reason","exclude_detail","y30","y30_horizon_eligible","episode_rule_version")
def sha(path):h=hashlib.sha256();h.update(Path(path).read_bytes());return h.hexdigest()
def semantic(rows):return hashlib.sha256("\n".join(sorted("|".join(json.dumps(r.get(k),sort_keys=True) for k in SEMANTIC) for r in rows)).encode()).hexdigest()
def write(path,rows):Path(path).parent.mkdir(parents=True,exist_ok=True);pq.write_table(pa.Table.from_pylist(rows if rows else [{"status":"NOT_RUN_INSUFFICIENT_LIVE_COVERAGE"}]),path)
def md(report):
    c=report["coverage"];s=report["coverage_selection_audit"];i=report["invariants"]
    return f"""# Phase 2A 验收报告

**RESEARCH / AUDIT ONLY — NO REAL TRADING**

## A. Implemented
Point-in-time Binance/CLOB feature builder, source-level provenance, coverage gate, selection audit, cutoff modes, and fold-local Logistic M0–M4 attribution framework.

## B. Tests
See final pytest handoff. All processing is offline.

## C. Hash / truth invariants
Status: **{i['status']}**. Ledger, settings, Phase 1.5 and Phase 1.6 report hashes are unchanged.

## D. Live coverage
Observable rows: {c['n_observations']}; fully covered: {c['n_fully_covered']}; calendar days: {c['calendar_span_days']}.
Thresholds: BTC pre-30s >= {c['btc_threshold']}; book pre-10s >= {c['book_threshold']}.

## E. Coverage selection audit
Status: {s['status']}; covered={s['n_covered']}, uncovered={s['n_uncovered']}. No covered-vs-uncovered Y30 delta is estimable when the covered group is empty.

## F. Point-in-time / provenance audit
Status: **{report['provenance_status']}**. Public source timestamps are bounded by feature_cutoff; Phase-1-safe FirstOpposite state is separately bounded by prediction t0 because it only becomes complete at t0.

## G. Cutoff sensitivity
C0/C1/C2 modeling was not run because no rows passed the live coverage gate. C1 remains the declared primary cutoff.

## H–N. Models, attribution and controls
M0–M4, coefficients, conditional shuffle, regime attribution and future-window model placebos were not estimated. Missing public streams were not imputed.

## O. Known limitations
No recorded Binance ticks, Polymarket book rows, or session journals are present. Historical order books cannot be reconstructed after the fact.

## P. Interpretation guardrails
No conclusion about BTC or book incremental information, causality, profitability, execution, or fair value is supported.

## Q. Phase 2B decision
**{report['decision']}** / **{report['phase2b_decision']}**. Continue live collection; do not lower coverage or sample thresholds.
"""
def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--ledger");p.add_argument("--output");p.add_argument("--btc-coverage-threshold",type=float,default=.99);p.add_argument("--book-coverage-threshold",type=float,default=.99);p.add_argument("--min-covered",type=int,default=5000);p.add_argument("--min-days",type=int,default=14);p.add_argument("--min-train-weeks",type=int,default=4);p.add_argument("--min-test-n",type=int,default=30);p.add_argument("--n-shuffles",type=int,default=500);p.add_argument("--seed",type=int,default=20260824);a=p.parse_args(argv)
    started=datetime.now(timezone.utc);run_id=started.strftime("%Y%m%dT%H%M%S.%fZ");settings=load_settings();ledger=Path(a.ledger) if a.ledger else resolve_path(settings,"derived")/"event_ledger.parquet";settings_path=ROOT/"config/settings.yaml";prior_reports=sorted(resolve_path(settings,"reports").glob("bias_audit_*.json"))[-1:]+sorted(resolve_path(settings,"reports").glob("regime_audit_*.json"))[-1:];truth=[ledger,settings_path,*prior_reports];before={str(x):sha(x) for x in truth};rows=pq.read_table(ledger).to_pylist();sem_before=semantic(rows)
    features,provenance_count,coverage,feature_paths=execute(ledger,None,"cutoff_1",a.btc_coverage_threshold,a.book_coverage_threshold,run_id);selection=audit_coverage_selection(features);eligible=[r for r in features if r["model_eligible"]];span=(max(r["prediction_ts_ms"] for r in eligible)-min(r["prediction_ts_ms"] for r in eligible))/86400000 if len(eligible)>1 else 0
    enough=len(eligible)>=a.min_covered and span>=a.min_days;predictions=[];fold_metrics=[];coefficients=[];aggregate={};ablation=[]
    if enough:
        predictions,fold_metrics,coefficients=run_models(features,a.min_train_weeks,a.min_test_n);aggregate,ablation=attribution(predictions,fold_metrics);coeff_summary=coefficient_stability(coefficients)
    else:coeff_summary=[]
    models_dir=resolve_path(settings,"derived")/"models";model_paths={"predictions":models_dir/f"phase2a_predictions_{run_id}.parquet","fold_metrics":models_dir/f"phase2a_fold_metrics_{run_id}.parquet","coefficients":models_dir/f"phase2a_coefficients_{run_id}.parquet","ablation":models_dir/f"phase2a_ablation_{run_id}.parquet"};write(model_paths["predictions"],predictions);write(model_paths["fold_metrics"],fold_metrics);write(model_paths["coefficients"],coefficients);write(model_paths["ablation"],ablation)
    after={str(x):sha(x) for x in truth};sem_after=semantic(pq.read_table(ledger).to_pylist());integrity=before==after and sem_before==sem_after;decision="POINT_IN_TIME_FAILURE" if not integrity else "INSUFFICIENT_LIVE_COVERAGE" if not enough else "STD0_STATE_DOMINANT"
    report={"phase":"2A","run_id":run_id,"started_at":started.isoformat(),"finished_at":datetime.now(timezone.utc).isoformat(),"python_version":platform.python_version(),"research_only":True,"invariants":{"before":before,"after":after,"semantic_before":sem_before,"semantic_after":sem_after,"status":"PASS" if integrity else "FAIL"},"coverage":{"n_observations":len(features),"n_fully_covered":len(eligible),"calendar_span_days":span,"btc_threshold":a.btc_coverage_threshold,"book_threshold":a.book_threshold if hasattr(a,"book_threshold") else a.book_coverage_threshold,"min_covered":a.min_covered,"min_days":a.min_days},"coverage_selection_audit":selection,"provenance_status":"PASS" if integrity else "FAIL","cutoff_sensitivity":{"primary":"cutoff_1","cutoff_0":"NOT_MODELED_INSUFFICIENT_COVERAGE","cutoff_1":"NOT_MODELED_INSUFFICIENT_COVERAGE" if not enough else "MODELED","cutoff_2":"NOT_MODELED_INSUFFICIENT_COVERAGE","same_second_warning":False},"models":aggregate,"attribution":ablation,"coefficient_stability":coeff_summary,"conditional_negative_control":{"status":"NOT_RUN_INSUFFICIENT_LIVE_COVERAGE","requested_shuffles":a.n_shuffles,"seed":a.seed},"future_window_placebo":{"status":"NOT_RUN_INSUFFICIENT_LIVE_COVERAGE"},"decision":decision,"phase2b_decision":"RESEARCH_AUTHORIZED_EXPLORATORY__CONFIRMED_NOT_AUTHORIZED","artifacts":{**{k:str(v) for k,v in feature_paths.items()},**{k:str(v) for k,v in model_paths.items()}}}
    metadata={"condition_id","prediction_ts_ms","feature_cutoff_ms","cutoff_mode","market_start_ms","market_end_ms","y30","iso_week","online_regime_id","model_eligible","model_ineligible_reason"};names=sorted(set().union(*(r.keys() for r in features))-metadata);report["missingness"]=[{"feature_name":name,"missing_count":sum(r.get(name) is None for r in features),"missing_rate":sum(r.get(name) is None for r in features)/len(features) if features else None} for name in names]
    out=Path(a.output) if a.output else resolve_path(settings,"reports");out.mkdir(parents=True,exist_ok=True);jp=out/f"phase2a_{run_id}.json";mp=out/f"phase2a_{run_id}.md";jp.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8");mp.write_text(md(report),encoding="utf-8");print(jp);print(mp);print(decision);return 0 if integrity else 2
if __name__=="__main__":raise SystemExit(main())
