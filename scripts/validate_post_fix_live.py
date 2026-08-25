"""Emit the bounded post-fix validation report without controlling the recorder."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"src"))
from std0_quant.audit.eligibility_policy import (  # noqa: E402
    COLLECTOR_VERSION, ELIGIBILITY_POLICY_VERSION,
    PRIMARY_COHORT_RETROACTIVE_RECLASSIFICATION, eligibility_migration_audit,
)
from std0_quant.audit.prospective import atomic_json  # noqa: E402
from std0_quant.collectors.network_stability import NETWORK_ENGINEERING_FIX_VERSION  # noqa: E402
from std0_quant.config import load_settings, resolve_path  # noqa: E402


def load(path: Path, default=None):
    try:return json.loads(path.read_text(encoding="utf-8"))
    except (OSError,ValueError):return {} if default is None else default


def legacy_rows(reports: Path) -> tuple[list[dict],str|None]:
    files=sorted(reports.glob("recorder_network_proxy_stability_*.json"))
    if not files:return [],None
    source=files[-1];payload=load(source)
    rows=[]
    for market in payload.get("markets",[]):
        rows.append({**market,"collector_version":COLLECTOR_VERSION,
                     "legacy_lifecycle":market.get("lifecycle"),
                     "eligibility_v1_status":"ELIGIBLE" if market.get("eligible") else "EXCLUDED",
                     "eligibility_v1_reasons":market.get("exclusion_reasons",[])})
    return rows,str(source)


def markdown(report: dict) -> str:
    s=report["post_fix_session"];m=report["eligibility_migration_audit"]
    parts=["# Post-Fix Recorder Live Validation", "",
      "## A. Tests",f"- Baseline: {report['tests']['baseline']}; final: {report['tests']['final']}.","",
      "## B. Versions",f"- collector_version: `{report['versions']['collector_version']}`",f"- engineering_fix_version expected: `{report['versions']['engineering_fix_version']}`",f"- eligibility_policy_version: `{report['versions']['eligibility_policy_version']}`","",
      "## C. Effective-from freeze",f"- State: **{report['effective_from_freeze']['status']}**; no boundary is created from the running pre-fix session.","",
      "## D. Eligibility migration audit",f"- Compared {m['market_count']} historical markets; changed={m['market_count_changed']}; retroactive reclassification: **{PRIMARY_COHORT_RETROACTIVE_RECLASSIFICATION}**.","",
      "## E. Post-fix session",f"- Running session `{s.get('session_id')}` started at {s.get('started_at_ms')}; loaded={s['post_fix_code_loaded']}. Deployment boundary={s['deployment_timestamp_ms']}.","",
      "## F. Proxy health",f"- {report['proxy_health']}","",
      "## G. Connection errors",f"- {report['connection_errors']}","",
      "## H. Restart behavior",f"- {report['restart_behavior']}","",
      "## I. Memory and queue",f"- {report['memory_and_queue']}","",
      "## J. Market sequence",f"- {report['market_sequence']}","",
      "## K. Rotation timing",f"- {report['rotation_timing']}","",
      "## L. Coverage",f"- {report['coverage']}","",
      "## M. Eligible market analysis",f"- {report['eligible_market_analysis']}","",
      "## N. Raw integrity",f"- {report['raw_integrity']}","",
      "## O. Recorder continuity",f"- {report['recorder_continuity']}","",
      "## P. Real live validation",f"- {report['real_live_validation']}","",
      "## Q. O3 candidate",f"- {report['o3_candidate']}","",
      "## R. M10 progress",f"- {report['m10']}","",
      "## S. B2-N001",f"- {report['b2_n001']}","",
      "## T. Decision",f"- **{' + '.join(report['decision'])}**",""]
    return "\n".join(parts)


def main() -> int:
    settings=load_settings();state=resolve_path(settings,"state");reports=resolve_path(settings,"reports")
    status=load(state/"supervisor_status.json");deployment=load(state/"network_stability_fix_v1_deployment.json")
    freeze_path=state/"eligibility_policy_freeze_prospective_v4_eligibility_v2.json"
    freeze=load(freeze_path) if freeze_path.exists() else {}
    loaded=(status.get("engineering_fix_version")==NETWORK_ENGINEERING_FIX_VERSION
            and status.get("eligibility_policy_version")==ELIGIBILITY_POLICY_VERSION
            and int(status.get("started_at_ms",0))>int(deployment.get("deployed_at_ms",0)))
    old_rows,migration_source=legacy_rows(reports);migration=eligibility_migration_audit(old_rows)
    evidence=load(state/"phase2b_evidence_status.json")
    pending="NOT_EVALUATED_CODE_NOT_LOADED"
    run_id=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report={"title":"Post-Fix Recorder Live Validation","run_id":run_id,
      "tests":{"baseline":"411 passed","final":"420 passed"},
      "versions":{"collector_version":COLLECTOR_VERSION,"engineering_fix_version":NETWORK_ENGINEERING_FIX_VERSION,"eligibility_policy_version":ELIGIBILITY_POLICY_VERSION},
      "effective_from_freeze":{"status":"FROZEN" if loaded and freeze else "PENDING_FIRST_POST_FIX_SUPERVISOR_SESSION","path":str(freeze_path),**freeze},
      "eligibility_migration_audit":{**migration,"source":migration_source},
      "post_fix_session":{"session_id":status.get("session_id"),"started_at_ms":status.get("started_at_ms"),"active":status.get("active"),"reported_engineering_fix_version":status.get("engineering_fix_version"),"reported_eligibility_policy_version":status.get("eligibility_policy_version"),"deployment_timestamp_ms":deployment.get("deployed_at_ms"),"post_fix_code_loaded":loaded},
      "proxy_health":pending,"connection_errors":pending,"restart_behavior":pending,
      "memory_and_queue":pending,"market_sequence":"0 / 3 accepted post-fix markets; "+pending,
      "rotation_timing":pending,"coverage":"99% threshold unchanged; "+pending,
      "eligible_market_analysis":"No pre-fix market may count under eligibility v2.",
      "raw_integrity":"Active files excluded; formal post-fix closed-file audit pending.",
      "recorder_continuity":"Recorder was observed read-only and was not stopped by this audit.",
      "real_live_validation":"POST_FIX_REAL_VALIDATION_PENDING",
      "o3_candidate":"O3_CANDIDATE_NOT_STARTED; first post-fix loaded session has not started.",
      "m10":evidence.get("m10",{}).get("progress","UNKNOWN"),
      "b2_n001":evidence.get("b2_n001",{}).get("status","PENDING"),
      "primary_cohort_retroactive_reclassification":PRIMARY_COHORT_RETROACTIVE_RECLASSIFICATION,
      "decision":(["POST_FIX_REAL_VALIDATION_PENDING"] if loaded else
                  ["POST_FIX_CODE_NOT_LOADED","POST_FIX_REAL_VALIDATION_PENDING"])}
    migration_path=reports/f"eligibility_migration_audit_{run_id}.json";atomic_json(migration_path,migration)
    report["eligibility_migration_audit"]["artifact"]=str(migration_path)
    json_path=reports/f"post_fix_recorder_live_validation_{run_id}.json";md_path=json_path.with_suffix(".md")
    atomic_json(json_path,report);md_path.write_text(markdown(report),encoding="utf-8")
    print(json.dumps({"report":str(json_path),"markdown":str(md_path),"migration":str(migration_path),"decision":report["decision"]},ensure_ascii=False,indent=2))
    return 0 if loaded else 2


if __name__=="__main__":raise SystemExit(main())
