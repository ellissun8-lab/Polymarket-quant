"""Cross-platform long-running supervisor for public research recorders."""
from __future__ import annotations
import argparse,json,os,signal,subprocess,sys,time
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"src"))
from std0_quant.collectors.live_health import atomic_write_json,build_health,process_rss_mb
from std0_quant.collectors.live_storage import finalize_orphan_sidecars,streaming_sha256
from std0_quant.collectors.recorder_reliability import ENGINEERING_FIX_VERSION,health_step_isolated
from std0_quant.collectors.network_stability import (
    NETWORK_ENGINEERING_FIX_VERSION, ProxyHealthMonitor,
    RestartStormDetector,
)
from std0_quant.collectors.gamma_discovery import (
    GAMMA_DISCOVERY_ISOLATION_FIX_VERSION,
)
from std0_quant.collectors.ws_runner import compute_backoff_seconds
from std0_quant.collectors.ws_runner import SessionJournal
from std0_quant.config import load_settings,resolve_path
from std0_quant.storage import RUN_ID_UNIQUENESS_FIX_VERSION,new_run_id
from std0_quant.audit.eligibility_policy import (
    ELIGIBILITY_POLICY_VERSION, RECORDER_RELIABILITY_FIX_VERSION,
    freeze_eligibility_policy,
)
from std0_quant.audit.coverage_evidence import (
    COVERAGE_EVIDENCE_VERSION, COVERAGE_SELECTION_FIX_VERSION,
)
def sha(p):return streaming_sha256(Path(p))
def latest(pattern):x=sorted((ROOT/"data/reports").glob(pattern));return x[-1] if x else None
def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--duration-minutes",type=float);p.add_argument("--no-sync",action="store_true");p.add_argument("--sync-interval",type=float);a=p.parse_args(argv);s=load_settings();session_id=new_run_id("supervisor");journal=SessionJournal(resolve_path(s,"sessions"),session_id,"live_supervisor");stop=False
    started=time.time()
    policy_freeze=freeze_eligibility_policy(
        resolve_path(s,"state")/"eligibility_policy_freeze_prospective_v4_eligibility_v2.json",
        session_id,int(started*1000),NETWORK_ENGINEERING_FIX_VERSION)
    journal.emit("eligibility_policy_frozen",**policy_freeze)
    baseline_check=subprocess.run([sys.executable,str(ROOT/"scripts/init_prospective_baseline.py")],cwd=ROOT,capture_output=True,text=True)
    journal.emit("historical_baseline_check",status="PASS" if baseline_check.returncode==0 else "FAIL",detail_text=baseline_check.stdout[-2000:])
    if baseline_check.returncode:
        journal.close(exit_reason="RECORDER_INTEGRITY_FAILURE")
        return 3
    # Startup recovery for raw files orphaned by a PREVIOUS unclean exit.
    # The grace window skips files still plausibly owned by a surviving
    # writer (fresh mtime); those are picked up by a later sweep instead.
    startup_repaired=[]
    for root,src in ((resolve_path(s,"raw_btc_ticks"),"binance_btc"),(resolve_path(s,"raw_polymarket_book"),"polymarket_book")):
        startup_repaired += [str(p) for p in finalize_orphan_sidecars(root,src,skip_newer_than_seconds=900)]
    if startup_repaired:journal.emit("orphan_sidecars_recovered",files=startup_repaired,phase="startup")
    def halt(*_):
        nonlocal stop;stop=True;journal.emit("shutdown_requested")
    signal.signal(signal.SIGINT,halt)
    if hasattr(signal,"SIGTERM"):signal.signal(signal.SIGTERM,halt)
    truth=[ROOT/"data/derived/event_ledger.parquet",ROOT/"config/settings.yaml",latest("bias_audit_*.json"),latest("regime_audit_*.json"),latest("phase2a_*.json")];truth=[x for x in truth if x];baseline={str(x):sha(x) for x in truth};deadline=started+a.duration_minutes*60 if a.duration_minutes else None;child=None;next_sync=started;next_rebuild=started+s.live.ledger_rebuild_interval_seconds;rebuild_child=None;sync_child=None;restarts=0;exit_reason="SHUTDOWN_REQUESTED"
    restart_detector=RestartStormDetector(s.live.restart_storm_threshold,s.live.restart_storm_window_seconds);proxy_monitor=ProxyHealthMonitor(s.polymarket.gamma_api_base,s.live.proxy_probe_interval_seconds);next_child_start=started;child_started_at=None;consecutive_short_failures=0;last_storm_state="NORMAL";last_proxy_state=None
    raw_roots=(resolve_path(s,"raw_btc_ticks"),resolve_path(s,"raw_polymarket_book"));files_before={str(x) for root in raw_roots for x in Path(root).rglob("*.ndjson")}
    version_meta={"collector_version":"phase2a_prospective_v4","cohort_version":"prospective_v4","engineering_fix_version":NETWORK_ENGINEERING_FIX_VERSION,"recorder_reliability_fix_version":RECORDER_RELIABILITY_FIX_VERSION,"gamma_discovery_isolation_fix_version":GAMMA_DISCOVERY_ISOLATION_FIX_VERSION,"run_id_uniqueness_fix_version":RUN_ID_UNIQUENESS_FIX_VERSION,"coverage_evidence_version":COVERAGE_EVIDENCE_VERSION,"coverage_selection_fix_version":COVERAGE_SELECTION_FIX_VERSION,"eligibility_policy_version":ELIGIBILITY_POLICY_VERSION,"eligibility_policy_effective_from_session_id":policy_freeze["effective_from_session_id"],"eligibility_policy_effective_from_timestamp_ms":policy_freeze["effective_from_timestamp_ms"],"engineering_fixes":[RECORDER_RELIABILITY_FIX_VERSION,NETWORK_ENGINEERING_FIX_VERSION,COVERAGE_SELECTION_FIX_VERSION,GAMMA_DISCOVERY_ISOLATION_FIX_VERSION,RUN_ID_UNIQUENESS_FIX_VERSION]}
    status_path=resolve_path(s,"state")/"supervisor_status.json";atomic_write_json(status_path,{"active":True,"pid":os.getpid(),"session_id":session_id,"started_at_ms":int(started*1000),**version_meta})

    def publish_health(payload):
        # best-effort telemetry of the supervised collector process; a
        # missing psutil (None) is honest, never fatal
        payload["collector_rss_mb"]=process_rss_mb(child.pid) if child is not None and child.poll() is None else None
        payload.update(version_meta)
        storm=restart_detector.snapshot();proxy=proxy_monitor.snapshot()
        payload["network"]={"proxy":proxy,"restart_storm":{"state":storm.state,"restart_count_in_window":storm.restart_count_in_window,"threshold":storm.threshold,"window_seconds":storm.window_seconds},"consecutive_short_failures":consecutive_short_failures,"next_child_start_epoch_seconds":next_child_start if child is None else None}
        atomic_write_json(resolve_path(s,"state")/"network_health.json",payload["network"])
        atomic_write_json(resolve_path(s,"state")/"live_health.json",payload)

    try:
        while not stop and (deadline is None or time.time()<deadline):
            now=time.time()
            if child is not None and child.poll() is not None:
                restarts+=1;runtime=max(0.0,now-(child_started_at or now));reason="UNEXPECTED_NORMAL_EXIT" if child.returncode==0 else "CHILD_EXIT_NONZERO";storm=restart_detector.record(now);journal.emit("child_restart",component="collect_live",exit_code=child.returncode,restart_count=restarts,restart_reason=reason,child_runtime_seconds=runtime,restart_storm_state=storm.state,restarts_in_window=storm.restart_count_in_window)
                if runtime>=s.live.restart_storm_window_seconds:consecutive_short_failures=0
                else:consecutive_short_failures+=1
                delay=compute_backoff_seconds(consecutive_short_failures,1.0,s.live.restart_max_backoff_seconds);next_child_start=now+delay;journal.emit("child_restart_backoff",component="collect_live",attempt=consecutive_short_failures,delay_seconds=delay)
                if storm.state!=last_storm_state:
                    journal.emit("restart_storm_warning",state=storm.state,restarts_in_window=storm.restart_count_in_window,threshold=storm.threshold,window_seconds=storm.window_seconds)
                    last_storm_state=storm.state
                child=None;child_started_at=None
            if child is None and now>=next_child_start:
                cmd=[sys.executable,str(ROOT/"scripts/collect_live.py")]
                if deadline is not None:
                    cmd += ["--duration-minutes",str(max(.01,(deadline-time.time())/60))]
                else:
                    cmd += ["--continuous"]
                flags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name=="nt" else 0
                child=subprocess.Popen(cmd,cwd=ROOT,creationflags=flags);child_started_at=time.time();journal.emit("child_started",component="collect_live",pid=child.pid,engineering_fix_version=NETWORK_ENGINEERING_FIX_VERSION,gamma_discovery_isolation_fix_version=GAMMA_DISCOVERY_ISOLATION_FIX_VERSION,coverage_evidence_version=COVERAGE_EVIDENCE_VERSION,coverage_selection_fix_version=COVERAGE_SELECTION_FIX_VERSION,eligibility_policy_version=ELIGIBILITY_POLICY_VERSION)
            # health reporting is fully isolated: a health failure is
            # classified HEALTH_REPORT_FAILURE and journaled, but NEVER
            # kills the raw collectors or the supervisor loop
            health_result=health_step_isolated(lambda:build_health(s),publish_health,journal)
            proxy_state=proxy_monitor.snapshot().get("state")
            if proxy_state!=last_proxy_state:
                journal.emit("proxy_state_changed",state=proxy_state)
                last_proxy_state=proxy_state
            if health_result["status"]=="OK":
                health=health_result["payload"]
                if health["disk_status"]=="CRITICAL":journal.emit("disk_critical_stop",free_gb=health["disk_free_gb"]);exit_reason="DISK_CRITICAL_STOP";stop=True;break
            if not a.no_sync and time.time()>=next_sync:
                if sync_child is not None and sync_child.poll() is not None:
                    journal.emit("analysis_child_exit",component="sync_std0_trades",exit_code=sync_child.returncode,recorder_action="NONE")
                    sync_child=None
                if sync_child is None:
                    sync_child=subprocess.Popen([sys.executable,str(ROOT/"scripts/sync_std0_trades.py")],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);journal.emit("sync_started",pid=sync_child.pid);next_sync=time.time()+(a.sync_interval or s.live.sync_interval_seconds)
            if time.time()>=next_rebuild:
                if rebuild_child is not None and rebuild_child.poll() is not None:
                    journal.emit("analysis_child_exit",component="refresh_live_derived",exit_code=rebuild_child.returncode,recorder_action="NONE")
                    rebuild_child=None
                if rebuild_child is None:
                    rebuild_child=subprocess.Popen([sys.executable,str(ROOT/"scripts/refresh_live_derived.py")],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);journal.emit("derived_refresh_started",pid=rebuild_child.pid);next_rebuild=time.time()+s.live.ledger_rebuild_interval_seconds
            time.sleep(min(s.live.health_interval_seconds,1 if deadline else s.live.health_interval_seconds))
    finally:
        if deadline is not None and time.time()>=deadline and not stop:exit_reason="DURATION_COMPLETE"
        if child is not None and child.poll() is None:
            if deadline is not None:
                try:child.wait(timeout=15)
                except subprocess.TimeoutExpired:pass
        if child is not None and child.poll() is None:
            child.send_signal(signal.CTRL_BREAK_EVENT if os.name=="nt" and hasattr(signal,"CTRL_BREAK_EVENT") else signal.SIGINT)
            try:child.wait(timeout=30)
            except subprocess.TimeoutExpired:child.terminate();child.wait(timeout=10)
        if rebuild_child is not None and rebuild_child.poll() is None:
            rebuild_child.terminate()
        if sync_child is not None and sync_child.poll() is None:
            sync_child.terminate()
        repaired=[]
        repaired += [str(p) for p in finalize_orphan_sidecars(resolve_path(s,"raw_btc_ticks"),"binance_btc")]
        repaired += [str(p) for p in finalize_orphan_sidecars(resolve_path(s,"raw_polymarket_book"),"polymarket_book")]
        if repaired:journal.emit("orphan_sidecars_recovered",files=repaired)
        files=[str(x) for root in raw_roots for x in Path(root).rglob("*.ndjson") if str(x) not in files_before or x.stat().st_mtime>=started]
        manifest={"session_id":session_id,"start_utc":datetime.fromtimestamp(started,timezone.utc).isoformat(),"end_utc":datetime.now(timezone.utc).isoformat(),"version":"phase2a_prospective_v4",**version_meta,"universe":"btc-updown-5m-*","wallet":s.trader.wallet,"sources":[s.btc.ws_url,s.polymarket.ws_url],"files":files,"recovered_sidecars":repaired,"restarts":restarts,"exit_reason":exit_reason,"historical_frozen_baseline_hashes":baseline,"current_hashes":{str(x):sha(x) for x in truth}}
        atomic_write_json(resolve_path(s,"state")/f"manifest_{session_id}.json",manifest);journal.close(exit_reason=exit_reason)
        atomic_write_json(status_path,{"active":False,"pid":os.getpid(),"session_id":session_id,"started_at_ms":int(started*1000),"ended_at_ms":int(time.time()*1000),"exit_reason":exit_reason,**version_meta})
    return 0
if __name__=="__main__":raise SystemExit(main())
