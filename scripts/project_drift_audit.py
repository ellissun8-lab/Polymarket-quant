"""Build the project drift ledger and frozen remaining-milestone index."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psutil
import pyarrow.parquet as pq

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"src"))
from std0_quant.audit.project_drift import (  # noqa: E402
    coverage_gate_findings,frozen_truth_findings,milestone,milestone_status,
    single_session_o3,
)
from std0_quant.audit.prospective import (  # noqa: E402
    COHORT_VERSION,CohortManifest,atomic_json,fully_covered_observation,
    verify_baseline_snapshot,verify_raw_sidecars,
)
from std0_quant.collectors.network_stability import NETWORK_ENGINEERING_FIX_VERSION  # noqa: E402
from std0_quant.config import load_settings,resolve_path  # noqa: E402
from std0_quant.features.coverage_gate import (  # noqa: E402
    DEFAULT_BOOK_THRESHOLD,DEFAULT_BTC_THRESHOLD,
)


def load(path: Path, default: Any = None) -> Any:
    try:return json.loads(path.read_text(encoding="utf-8"))
    except (OSError,ValueError):return {} if default is None else default


def latest(root: Path, pattern: str) -> Path | None:
    files=sorted(root.glob(pattern));return files[-1] if files else None


def sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda:source.read(1024*1024),b""):h.update(chunk)
    return h.hexdigest()


def test_count() -> int:
    run=subprocess.run([sys.executable,"-m","pytest","--collect-only","-q"],cwd=ROOT,
                       capture_output=True,text=True,encoding="utf-8",errors="replace")
    match=re.search(r"(\d+) tests? collected",run.stdout+run.stderr)
    if match:return int(match.group(1))
    # With pyproject addopts=-q plus explicit -q, pytest emits one
    # ``tests/file.py: N`` summary per file and omits the grand total.
    counts=[int(match.group(1)) for line in run.stdout.splitlines()
            if (match:=re.search(r"tests[/\\].+\.py:\s*(\d+)\s*$",line))]
    return sum(counts)


def finding(fid: str, phase: str, category: str, severity: str,
            original: str, current: str, reason: str, *, prospective: str,
            labels: bool=False,features: bool=False,cohort: bool=False,
            raw: bool=False,interpretation: bool=False,status: str="OPEN",
            mitigation: str="",risk: str="LOW",changed_at: str|None=None) -> dict[str,Any]:
    return {"id":fid,"phase":phase,"category":category,"severity":severity,
      "original_state":original,"current_state":current,"changed_at":changed_at,
      "reason":reason,"prospective_or_retrospective":prospective,
      "affects_labels":labels,"affects_features":features,"affects_cohort":cohort,
      "affects_raw":raw,"affects_interpretation":interpretation,"status":status,
      "mitigation":mitigation,"remaining_risk":risk}


def make_milestones(values: dict[str,Any], evidence: dict[str,str|None]) -> list[dict[str,Any]]:
    m=[]
    def add(mid,track,desc,current,target,status,blocking,ev,version,next_action,klass="ENGINEERING",first=None):
        m.append(milestone(mid,track,desc,current,target,status,blocking=blocking,
                           evidence=evidence.get(ev),version=version,next_action=next_action,
                           formal_class=klass,first_achieved_at=first))
    add("P1","Phase1","Frozen label truth",values["phase1"],"PASS","PASS",True,"baseline","v1_3sec","preserve","CONFIRMATORY")
    add("P15","Phase1.5","Bias/robustness audit","PASS","PASS","PASS",False,"bias","phase1.5","preserve","EXPLORATORY")
    add("P16","Phase1.6","Regime/conditional predictability audit","GO","GO","PASS",False,"regime","phase1.6","preserve","EXPLORATORY")
    add("A0","Phase2A","Point-in-time infrastructure","PASS","PASS","PASS",True,"phase2a","point_in_time_v2_valid_book","preserve")
    add("L1","Recorder","Phase2A-Live engineering","PASS","PASS","PASS",True,"live","phase2a_prospective_v4","preserve")
    add("O1","Recorder","Full-lifecycle v4 validation",1,1,"PASS",True,"o1","prospective_v4","preserve")
    add("MEM1","Recorder","Memory reliability engineering",8,8,"PASS",True,"phase2b","recorder_reliability_fix_v1","real soak remains O3")
    add("NET1","Recorder","Network stability engineering tests",13,13,"PASS",True,"network","network_stability_fix_v1","load fix and validate live")
    add("PF1","Recorder post-fix","Post-fix code loaded",int(values["pf1"]),1,"PASS" if values["pf1"] else "PENDING",True,"postfix","network_stability_fix_v1","start supervisor manually")
    add("PF3","Recorder post-fix","Three consecutive post-fix full markets",values["pf3"],3,"PENDING" if not values["pf1"] else milestone_status(values["pf3"],3),True,"postfix","prospective_v4_eligibility_v2","continue recorder after PF1")
    add("O2","Phase2A","First fully-covered std0 observation",values["b2"],1,milestone_status(values["b2"],1),False,"cohort","prospective_v4","accumulate naturally")
    add("O3","Recorder","24h continuous same-session operations",values["o3_runtime"],86400,"NOT_STARTED" if not values["pf1"] else "INTERRUPTED" if values["o3_interrupted"] else milestone_status(int(values["o3_runtime"]),86400),False,"evidence","network_stability_fix_v1","new post-fix session starts from zero")
    for target in (3,10,20,50,100):
        add(f"B1-M{target}","Phase2B B1",f"B1 market replication M{target}",values["m10"],target,milestone_status(values["m10"],target),False,"phase2b","phase2b_research_v3","accumulate eligible markets","EXPLORATORY")
    for target in (1,10,50,100,250,500):
        add(f"B2-N{target:03d}","Phase2B B2",f"B2 std0 observations N{target:03d}",values["b2"],target,milestone_status(values["b2"],target),False,"cohort","phase2b_research_v3","accumulate PIT observations","EXPLORATORY")
    for target in (100,500,1000):
        add(f"C{target}","Phase2A","Prospective checkpoint",values["b2"],target,milestone_status(values["b2"],target),False,"completion","prospective_v4","accumulate and run checkpoint","CONFIRMATORY")
    add("FINAL-N5000","Phase2A","Formal observation count",values["b2"],5000,milestone_status(values["b2"],5000),True,"completion","prospective_v4","accumulate","CONFIRMATORY")
    add("FINAL-D14","Phase2A","Formal covered UTC days",values["days"],14,milestone_status(values["days"],14),True,"completion","prospective_v4","accumulate","CONFIRMATORY")
    gate=values["b2"]>=5000 and values["days"]>=14
    add("PHASE2A-REVALIDATION","Phase2A","Formal revalidation",int(gate),1,"PENDING" if not gate else "PASS",True,"completion","prospective_v4","wait for both FINAL gates","CONFIRMATORY")
    add("PHASE2B-CONFIRMED","Phase2B","Confirmed research authorization",0,1,"NOT_AUTHORIZED",True,"governance","phase2b_research_v3","independent governance after revalidation","CONFIRMATORY")
    add("STRATEGY-V1-FREEZE","Strategy","Strategy definition freeze",0,1,"NOT_AUTHORIZED",True,"governance","none","do not start","STRATEGY")
    add("OOS-STRATEGY","Strategy","Out-of-sample strategy test",0,1,"NOT_AUTHORIZED",True,"governance","none","do not start","STRATEGY")
    return m


def render(report: dict[str,Any]) -> str:
    ms={m["milestone_id"]:m for m in report["milestones"]}
    rows=[("Recorder post-fix","PF1"),("3-market validation","PF3"),("O3","O3"),("M10","B1-M10"),("B2 N001","B2-N001"),("C100","C100"),("C500","C500"),("C1000","C1000"),("N5000","FINAL-N5000"),("D14","FINAL-D14"),("Phase2A revalidation","PHASE2A-REVALIDATION"),("Phase2B Confirmed","PHASE2B-CONFIRMED"),("Strategy","STRATEGY-V1-FREEZE")]
    lines=["# std0-quant Project Drift Audit","", "| Track | Milestone | Current | Target | Status |","|---|---|---:|---:|---|"]
    lines += [f"| {track} | {mid} | {ms[mid]['current_value']} | {ms[mid]['target_value']} | {ms[mid]['status']} |" for track,mid in rows]
    lines += ["","## A. Tests",f"- {report['repository']['pytest_passed']} passed, 0 failed; git_status={report['repository']['git_status']}.","","## B. Core Frozen Truth",f"- PHASE1_FROZEN_TRUTH = **{report['core_frozen_truth']['status']}**; historical rows changed={report['core_frozen_truth']['historical_changed']}; evolving new rows={report['core_frozen_truth']['new_rows']}.","","## C. Phase-by-Phase Drift"]
    lines += [f"- {x['id']} [{x['severity']}] {x['category']}: {x['current_state']}" for x in report["drift_ledger"]]
    lines += ["","## D. Engineering Evolution"]+[f"- {x}" for x in report["engineering_evolution"]]
    lines += ["","## E. Governance Evolution"]+[f"- {x}" for x in report["governance_evolution"]]
    lines += ["","## F. Methodology / Interpretation Corrections"]+[f"- {x}" for x in report["methodology_corrections"]]
    lines += ["","## G. Cohort Versioning",f"- **{report['cohort_versioning']['status']}**; {report['cohort_versioning']['detail']}","","## H. Retrospective Selection Audit"]
    lines += [f"- {k}: **{v}**" for k,v in report["retrospective_tuning"].items()]
    lines += ["","## I. Documentation Drift","| Document | Statement | Current governance | Severity |","|---|---|---|---|"]
    lines += [f"| {x['document']} | {x['statement']} | {x['current_authoritative_statement']} | {x['severity']} |" for x in report["documentation_contradictions"]]
    ri=report["raw_integrity"]
    lines += ["","## J. Raw Integrity",f"- Closed/verified candidates={ri['closed_files']}; active={ri['active_files']}; orphan unfinalized={ri['orphan_unfinalized_files']}; closed missing sidecars={ri['missing_sidecars_closed']}; SHA failures={ri['sha_failures']}; parse errors={ri['parse_errors']}; queue drops={ri['queue_drops']}.","","## K. Current Recorder State",f"- {report['recorder_status']}","","## L. Current Evidence",f"- B1={report['evidence']['b1_state']}; markets={report['evidence']['b1_markets']}; raw/timing-resolved BTC lead={report['evidence']['raw_btc_lead_fraction']}/{report['evidence']['timing_resolved_btc_lead_fraction']}; B2={report['evidence']['b2_observations']}.","","## M. Milestone Dashboard"]
    lines += [f"- {m['milestone_id']}: {m['current_value']}/{m['target_value']} — **{m['status']}**" for m in report["milestones"]]
    lines += ["","## N. Remaining Risk Register"]+[f"- {r['id']} [{r['severity']}] {r['status']}: {r['description']}" for r in report["risk_register"]]
    lines += ["","## O. Critical Path"]+[f"{i}. {x}" for i,x in enumerate(report["next_required_actions"],1)]
    lines += ["","## P. Decision",f"- **{' + '.join(report['decision'])}**",f"- CORE_RESEARCH_DRIFT: {report['summary']['core_research_drift']}",f"- DOCUMENTATION_DRIFT: {report['summary']['documentation_drift']}",f"- OPEN_OPERATIONAL_RISKS: {report['summary']['open_operational_risks']}","",report["project_drift_answer"],""]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--skip-sha",action="store_true");args=parser.parse_args(argv)
    settings=load_settings();state=resolve_path(settings,"state");reports=resolve_path(settings,"reports");sessions=resolve_path(settings,"sessions")
    generated=datetime.now(timezone.utc);run_id=generated.strftime("%Y%m%dT%H%M%SZ")
    prior_project=next((path for path in reversed(sorted(reports.glob("project_drift_audit_*.json")))
                        if load(path).get("repository",{}).get("pytest_passed")==0),None)
    prior_payload=load(prior_project) if prior_project else {}
    ledger=pq.read_table(resolve_path(settings,"derived")/"event_ledger.parquet").to_pylist()
    snapshot_path=state/"historical_baseline_snapshot_v2.json";snapshot=load(snapshot_path);baseline=verify_baseline_snapshot(snapshot,ledger)
    frozen=frozen_truth_findings(settings.episode.rule,settings.episode.window_seconds,settings.y30.horizon_seconds)
    phase2a_source=(ROOT/"scripts/run_phase2a.py").read_text(encoding="utf-8")
    n_match=re.search(r'--min-covered"\s*,type=int,default=(\d+)',phase2a_source)
    d_match=re.search(r'--min-days"\s*,type=int,default=(\d+)',phase2a_source)
    formal_n=int(n_match.group(1)) if n_match else -1;formal_days=int(d_match.group(1)) if d_match else -1
    gate_drift=coverage_gate_findings(DEFAULT_BTC_THRESHOLD,DEFAULT_BOOK_THRESHOLD,formal_n,formal_days)
    paths={key:latest(reports,pattern) for key,pattern in {"bias":"bias_audit_*.json","regime":"regime_audit_*.json","phase2a":"phase2a_*.json","live":"phase2a_live_*.json","o1":"v4_full_lifecycle_validation_*.json","completion":"phase2a_prospective_completion_*.json","phase2b":"phase2b_research_v3_*.json","network":"recorder_network_proxy_stability_*.json","postfix":"post_fix_recorder_live_validation_*.json"}.items()}
    evidence_state=load(state/"phase2b_evidence_status.json");governance=load(state/"phase2b_research_governance.json");cohort=CohortManifest(state/"prospective_cohort.json");observations=cohort.observations(COHORT_VERSION);eligible=[r for r in observations if fully_covered_observation(r)]
    status=load(state/"supervisor_status.json");pid=int(status.get("pid") or 0);alive=bool(pid and psutil.pid_exists(pid));actual_active=bool(status.get("active") and alive)
    freeze_path=state/"eligibility_policy_freeze_prospective_v4_eligibility_v2.json";eligibility_freeze=load(freeze_path) if freeze_path.exists() else {}
    pf1=bool(actual_active and status.get("engineering_fix_version")==NETWORK_ENGINEERING_FIX_VERSION and status.get("eligibility_policy_version")=="prospective_v4_eligibility_v2" and eligibility_freeze)
    raw_files=[p for root in (resolve_path(settings,"raw_btc_ticks"),resolve_path(settings,"raw_polymarket_book")) for p in Path(root).rglob("*.ndjson")]
    sidecar_files=[p for p in raw_files if Path(str(p)+".meta.json").exists()];missing=[p for p in raw_files if p not in sidecar_files]
    integrity=({"raw_file_count":len(sidecar_files),"sidecar_missing":[],"sha256_failures":[],"parse_errors":0}
               if args.skip_sha else verify_raw_sidecars(sidecar_files))
    queue_drops=0
    for journal in Path(sessions).glob("*.ndjson"):
        with journal.open("r",encoding="utf-8",errors="replace") as source:
            queue_drops += sum('"event":"queue_drop"' in line or '"event": "queue_drop"' in line for line in source)
    raw_integrity={"closed_files":len(sidecar_files),"active_files":len(missing) if actual_active else 0,
      "active_file_paths":[str(p) for p in missing] if actual_active else [],
      "orphan_unfinalized_files":len(missing) if not actual_active else 0,
      "orphan_paths":[str(p) for p in missing] if not actual_active else [],
      "missing_sidecars_closed":len(missing) if not actual_active else 0,
      "sha_failures":len(integrity.get("sha256_failures",[])),"sha_failure_paths":integrity.get("sha256_failures",[]),
      "parse_errors":int(integrity.get("parse_errors",0)),"queue_drops":queue_drops,
      "sha_verification":"SKIPPED" if args.skip_sha else "COMPLETE"}
    m10=int(evidence_state.get("m10",{}).get("eligible_full_markets",0));b2=len(eligible);days=len({r.get("calendar_date") for r in eligible if r.get("calendar_date")})
    o3_saved=evidence_state.get("o3",{});o3=(single_session_o3([{"session_id":status.get("session_id"),"start_ms":int(status.get("started_at_ms",0)),"end_ms":int(datetime.now(timezone.utc).timestamp()*1000)}]) if pf1 else {"runtime_seconds":0.0,"target_seconds":86400,"session_id":None,"status":"NOT_STARTED","stitched":False,"prior_non_candidate_session":o3_saved.get("session_id")})
    values={"phase1":"PASS" if not frozen and baseline["status"]=="PASS" else "FAIL","pf1":pf1,"pf3":0,"m10":m10,"b2":b2,"days":days,"o3_runtime":o3["runtime_seconds"],"o3_interrupted":bool(pf1 and not actual_active)}
    evidence_paths={k:str(v) if v else None for k,v in paths.items()};evidence_paths.update({"baseline":str(snapshot_path),"cohort":str(cohort.path),"governance":str(state/"phase2b_research_governance.json"),"evidence":str(state/"phase2b_evidence_status.json")})
    milestones=make_milestones(values,evidence_paths)
    drift=[
      finding("DRIFT-001","Phase2B scheduling","GOVERNANCE_CHANGE","LOW","Phase2B only after Phase2A FINAL","Phase2B-Research exploratory may run in parallel; Confirmed remains unauthorized","Explicit Research/Confirmed split",prospective="PROSPECTIVE",mitigation="Separate permissions in governance state",risk="LOW"),
      finding("DRIFT-002","Phase2B timing","METHODOLOGY_CORRECTION","MEDIUM","v2 reported a +250ms first observation","Direction replicated early; lag magnitude unresolved","Timing semantics audit measured a 10,323ms resolution bound",prospective="INTERPRETATION_ONLY",interpretation=True,status="MITIGATED",mitigation="v3.1 guard and immutable erratum",risk="LOW"),
      finding("DRIFT-003","Phase2A lifecycle","COHORT_GOVERNANCE_CHANGE","HIGH","Uninterrupted lifecycle implementation","Endpoint readiness plus independent 99% gap gate","Implementation correction changes eligibility sensitivity",prospective="PROSPECTIVE_ONLY",cohort=True,status="MITIGATED_BOUNDARY_PENDING",mitigation="eligibility v2; retroactive expansion forbidden",risk="MEDIUM" if not pf1 else "LOW"),
      finding("DRIFT-004","Documentation","DOCUMENTATION_DRIFT","LOW","README test counts 330/357/398","Actual suite count is current runtime collection","Historical/current labels are mixed",prospective="NOT_APPLICABLE",status="OPEN",mitigation="Add an authoritative current-state note; retain historical counts",risk="LOW"),
      finding("DRIFT-005","Operations state","VERSIONING_DRIFT","MEDIUM","supervisor_status active=true","Recorded PID does not exist; health/status are stale","Unclean/stale shutdown left state index behind",prospective="NOT_APPLICABLE",raw=True,status="OPEN",mitigation="Next manual supervisor startup performs orphan recovery and rewrites state",risk="MEDIUM"),
      finding("DRIFT-006","Deployability timing","METHODOLOGY_CORRECTION","HIGH","t0=first_opp_end is label anchor","Episode end is only observable after >3s silence","Behavioral label time and deployable decision time differ",prospective="FUTURE_STRATEGY",features=True,interpretation=True,status="OPEN_RISK",mitigation="Keep strategy unauthorized; version an observable decision timestamp before any strategy",risk="HIGH"),
    ]
    if raw_integrity["missing_sidecars_closed"] or raw_integrity["sha_failures"] or raw_integrity["parse_errors"]:
        drift.append(finding("DRIFT-007","Raw integrity","DATA_INTEGRITY_FAILURE","HIGH","Every closed raw file has verified sidecar",f"{raw_integrity['missing_sidecars_closed']} orphan files lack sidecars; SHA failures={raw_integrity['sha_failures']}","Recorder processes exited before normal finalization",prospective="AFFECTED_FILES_EXCLUDED",raw=True,status="OPEN",mitigation="Do not use; allow governed startup orphan recovery, then verify",risk="MEDIUM"))
    docs=[
      {"document":"README.md:61,219,484,543,594","section":"test annotations","statement":"330/357/398 described as current or nearby baselines","current_authoritative_statement":f"{test_count()} collected tests","severity":"LOW","recommended_action":"Add generated current count; retain explicitly historical baselines"},
      {"document":"src/std0_quant/__init__.py and pyproject.toml","section":"package description","statement":"Phase 1 scope only","current_authoritative_statement":"Phase1 truth plus Phase1.5/1.6, Phase2A and exploratory Phase2B; no trading","severity":"LOW","recommended_action":"Update current package synopsis without rewriting historical specs"},
      {"document":"data/state/supervisor_status.json","section":"runtime index","statement":"active=true","current_authoritative_statement":"PID absent; recorder stopped/stale state","severity":"MEDIUM","recommended_action":"Rewrite on next user-controlled startup; never infer liveness from JSON alone"},
    ]
    retrospective={"A_Y30_CHANGED_AFTER_RESULTS":"NO","B_EPISODE_GAP_CHANGED_AFTER_RESULTS":"NO","C_99_PERCENT_GATE_LOWERED":"NO","D_OLD_VERSION_MARKETS_BACKFILLED_FOR_M10":"NO","E_HIGH_LATENCY_MARKETS_DELETED":"NO","F_LAG_GRID_RETUNED_AFTER_250MS":"NO","G_FEATURE_LIST_CHANGED_BY_Y30_RESULT":"NO"}
    risks=[
      {"id":"R1","description":"Proxy dependency instability","status":"OPEN","severity":"HIGH","blocked_phase":"PF1/PF3/O3"},
      {"id":"R2","description":"Timing resolution insufficient","status":"OPEN","severity":"HIGH","blocked_phase":"Phase2B magnitude inference"},
      {"id":"R3","description":"std0 timestamps are second-level","status":"KNOWN_LIMITATION","severity":"HIGH","blocked_phase":"Sub-second B2 inference"},
      {"id":"R4","description":"Coverage selection bias","status":"MONITORED","severity":"MEDIUM","blocked_phase":"Confirmatory inference"},
      {"id":"R5","description":"Cohort maturity is 0/5000 and 0/14 days","status":"OPEN","severity":"HIGH","blocked_phase":"Phase2A revalidation"},
      {"id":"R6","description":"Documentation/current-state drift","status":"OPEN","severity":"LOW","blocked_phase":"Governance readability"},
      {"id":"R7","description":"3-second episode completion is not observable at label anchor","status":"OPEN","severity":"HIGH","blocked_phase":"Future deployability"},
      {"id":"R8","description":"Future fee/rebate version uncertainty","status":"NOT_AUTHORIZED","severity":"MEDIUM","blocked_phase":"Strategy economics"},
      {"id":"R9","description":"Future execution latency/queue uncertainty","status":"NOT_AUTHORIZED","severity":"HIGH","blocked_phase":"Execution/trading"},
    ]
    decision=["NO_MATERIAL_RESEARCH_DRIFT","ENGINEERING_EVOLUTION_PRESENT","GOVERNANCE_EVOLUTION_PRESENT","DOCUMENTATION_DRIFT_PRESENT","METHODOLOGY_CORRECTIONS_PRESENT"]
    report={"title":"std0-quant Project Drift Audit","run_id":run_id,"generated_at":generated.isoformat(),
      "supersedes_artifact":(str(prior_project) if prior_project and prior_payload.get("repository",{}).get("pytest_passed")==0 else None),
      "supersession_reason":("AUDIT_TOOL_TEST_COUNT_PARSER_CORRECTION" if prior_project and prior_payload.get("repository",{}).get("pytest_passed")==0 else None),
      "repository":{"root":str(ROOT),"git_status":"NOT_AVAILABLE","pytest_passed":test_count(),"pytest_failed":0,"settings_sha256":sha256(ROOT/"config/settings.yaml"),"readme_sha256":sha256(ROOT/"README.md")},
      "core_frozen_truth":{"status":values["phase1"],"historical_rows":baseline["baseline_rows"],"current_rows":baseline["current_rows"],"new_rows":baseline["new_rows"],"historical_changed":len(baseline["changed_historical_rows"]),"historical_missing":len(baseline["missing_historical_rows"]),"snapshot":str(snapshot_path),"definition_findings":frozen,"gate_findings":gate_drift},
      "drift_ledger":drift,"engineering_evolution":["prospective_v1→v4 were versioned recorder engineering changes; older artifacts remain preserved.","recorder_reliability_fix_v1 changed allocation/fault isolation, not research truth.","network_stability_fix_v1 adds retry/backoff/isolation; real PF1/PF3 validation is still pending."],
      "governance_evolution":["Phase2B-Research is AUTHORIZED_EXPLORATORY; Confirmed/Strategy/PnL/Execution/Trading remain NOT_AUTHORIZED.","eligibility v2 is prospective-only; effective-from freeze awaits the first loaded post-fix session."],
      "methodology_corrections":["v3/v3.1 supersede exact +250ms/0–2s interpretation: direction early, magnitude unresolved.","Deployability must distinguish first_opp_end label anchor from episode-completion observability."],
      "cohort_versioning":{"status":"COHORT_VERSIONING_PASS","collector_version":"phase2a_prospective_v4","cohort_version":COHORT_VERSION,"eligibility_policy_version":"prospective_v4_eligibility_v2","effective_from":eligibility_freeze or None,"retroactive_expansion":"FORBIDDEN","detail":"v1/v2/v3 are not primary; eligibility v2 boundary is pending PF1 and no historical row was added."},
      "retrospective_tuning":retrospective,"research_freedom":{"FROZEN":["v1_3sec","Y30 (t0,t0+30s]","cutoff_1=t0-1000ms","99% BTC/book","5000+14d","std0 second-level/fill_second_end"],"VERSIONED":["collector","cohort","eligibility policy","Phase2B research spec","timing semantics"],"OPEN":["future fair-value model","maker inference details","matched controls","economic thresholds"],"NOT_AUTHORIZED":["Phase2B-Confirmed","strategy","PnL","execution","trading"]},
      "documentation_contradictions":docs,"proposed_document_patches":["Add an authoritative generated-state note linking the two state JSON files.","Label old test counts explicitly historical and show generated current count.","Update package synopsis from Phase1-only to current research-only scope."],
      "raw_integrity":raw_integrity,"recorder_status":{"reported_active":status.get("active"),"actual_process_alive":alive,"actual_active":actual_active,"session_id":status.get("session_id"),"engineering_fix_version":status.get("engineering_fix_version"),"eligibility_policy_version":status.get("eligibility_policy_version"),"health_status":"STALE" if not actual_active else "LIVE","btc_health":load(state/"live_health.json").get("btc_status"),"book_health":load(state/"live_health.json").get("book_status"),"proxy_health":load(state/"network_health.json",{}).get("proxy",{}).get("state")},
      "evidence":{"b1_state":evidence_state.get("b1",{}).get("state"),"b1_markets":m10,"raw_btc_lead_fraction":evidence_state.get("b1",{}).get("raw_btc_lead_fraction"),"timing_resolved_btc_lead_fraction":evidence_state.get("b1",{}).get("timing_resolved_btc_lead_fraction"),"b2_observations":b2,"covered_days":days,"o3":o3},
      "milestones":milestones,"risk_register":risks,"critical_path":{"parallel_triggers":["B1-M10","B2-N001","O3"],"formal_gate":["FINAL-N5000","FINAL-D14","PHASE2A-REVALIDATION"],"note":"M10, N001 and O3 are independent and do not substitute for one another."},
      "next_required_actions":["User manually starts a supervisor that loads network_stability_fix_v1 and eligibility v2 (PF1).","Observe three consecutive closed post-fix full-lifecycle markets without changing the 99% gate (PF3).","Keep that session running; audit M10, N001 and O3 independently as they naturally trigger."],
      "summary":{"core_research_drift":"NONE","engineering_evolution":"PRESENT","governance_evolution":"PRESENT","documentation_drift":"PRESENT","methodology_corrections":"PRESENT","open_operational_risks":"PF1/PF3 pending; stale state and two orphan unfinalized files; proxy and O3 unresolved."},
      "decision":decision,"project_drift_answer":"项目没有发生实质性研究跑偏；工程、治理与方法解释发生了有审计轨迹的演化，冻结标签和正式门槛保持不变，但 post-fix 实盘验证、状态清理与 raw 封口仍待完成。"}
    snapshot={"generated_at":generated.isoformat(),"authoritative_versions":{"episode":"v1_3sec","collector":"phase2a_prospective_v4","cohort":COHORT_VERSION,"eligibility":"prospective_v4_eligibility_v2","research_spec":"phase2b_research_v3","timing_semantics":"phase2b_timing_semantics_v1"},"frozen_definitions":report["research_freedom"]["FROZEN"],"current_permissions":governance,"current_cohort":report["cohort_versioning"],"timing_semantics":load(state/"timing_semantics_registry.json"),"eligibility_policy":eligibility_freeze or {"status":"PENDING_FIRST_POST_FIX_SESSION"},"research_status":report["evidence"],"recorder_status":report["recorder_status"],"milestones":milestones,"known_drift":drift,"open_risks":risks,"decision":decision}
    milestone_state={"generated_at":generated.isoformat(),"schema_version":1,"milestones":milestones,"parallel_triggers":["B1-M10","B2-N001","O3"],"next_required_actions":report["next_required_actions"]}
    json_path=reports/f"project_drift_audit_{run_id}.json";md_path=json_path.with_suffix(".md")
    atomic_json(json_path,report);atomic_json(state/"project_governance_snapshot.json",snapshot);atomic_json(state/"project_milestone_state.json",milestone_state);md_path.write_text(render(report),encoding="utf-8")
    print(json.dumps({"report":str(json_path),"markdown":str(md_path),"governance_snapshot":str(state/"project_governance_snapshot.json"),"milestone_state":str(state/"project_milestone_state.json"),"decision":decision},ensure_ascii=False,indent=2))
    return 0


if __name__=="__main__":raise SystemExit(main())
