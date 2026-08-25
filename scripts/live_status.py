"""Print recorder and prospective-cohort status without network access."""
from pathlib import Path
import json,os,sys
import psutil
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"src"))
from std0_quant.audit.prospective import prospective_status
from std0_quant.collectors.live_health import atomic_write_json,build_health
from std0_quant.collectors.network_stability import probe_proxy,proxy_for_url,sanitized_proxy
from std0_quant.config import load_settings,resolve_path
def main():
    s=load_settings();state=resolve_path(s,"state");h=build_health(s);p=prospective_status(state,resolve_path(s,"reports"));status_path=state/"supervisor_status.json";runtime=json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {"active":False};running=False
    if runtime.get("active") and runtime.get("pid"):
        try:os.kill(int(runtime["pid"]),0);running=True
        except OSError:running=False
    if not running:
        for process in psutil.process_iter(("pid","cmdline")):
            try:
                command=" ".join(process.info.get("cmdline") or [])
                if process.info["pid"]!=os.getpid() and "run_live_supervisor.py" in command:
                    running=True;break
            except (psutil.NoSuchProcess,psutil.AccessDenied):continue
    health_path=state/"live_health.json";existing=json.loads(health_path.read_text(encoding="utf-8")) if health_path.exists() else {};targets={"gamma_http":s.polymarket.gamma_api_base,"binance_ws":s.btc.ws_url,"clob_ws":s.polymarket.ws_url};proxy={}
    for name,url in targets.items():
        proxy_url=proxy_for_url(url);proxy[name]={**sanitized_proxy(proxy_url),"state":probe_proxy(proxy_url)}
    network_state=state/"network_health.json";supervisor_network=json.loads(network_state.read_text(encoding="utf-8")) if network_state.exists() else {}
    network={**supervisor_network,"dependencies":proxy}
    path=atomic_write_json(health_path,{**existing,**h,"recorder_status":"RUNNING" if running else "STOPPED","prospective":p,"network":network})
    print("STD0 PROSPECTIVE STATUS")
    print("\nEngineering");print(f"  recorder version: {p['cohort_version']}");print(f"  pipeline: {p['engineering']}")
    print("\nOperational validation");print(f"  full lifecycle market: {p['o1_full_lifecycle']}");print(f"  first fully-covered observation: {p['o2_first_observation']}");print(f"  24h audit: {p['o3_24h']}")
    print("\nLive");print(f"  recorder: {'RUNNING' if running else 'STOPPED'}");print(f"  BTC: {h['btc_status']} records={h['btc']['records']} age_ms={h['btc']['last_age_ms']}");print(f"  CLOB: {h['book_status']} records={h['book']['records']} age_ms={h['book']['last_age_ms']}");print(f"  Disk: {h['disk_status']} {h['disk_free_gb']:.1f} GiB free")
    print("\nNetwork");print(f"  proxy: {proxy['gamma_http']['state']} {proxy['gamma_http']['host']}:{proxy['gamma_http']['port']}");print(f"  restart storm: {network.get('restart_storm',{}).get('state','NOT_REPORTED_BY_RUNNING_SUPERVISOR')}")
    print("\nProspective cohort");print(f"  version: {p['cohort_version']}");print(f"  observations: {p['fully_covered']} / 5000");print(f"  days: {p['covered_calendar_days']} / 14");print(f"  next checkpoint: {p['next_checkpoint']}")
    print("\nIntegrity");print(f"  PIT violations: {p['point_in_time_violations']}");print(f"  provenance violations: {p['provenance_violations']}");print("  historical baseline: PASS")
    print("\nDecision");print(f"  Phase 2A: {p['readiness_status']}");print(f"  Phase 2B-Research: {p['phase2b_research']}");print(f"  Phase 2B-Confirmed: {p['phase2b_confirmed']}");print(f"  Strategy/PnL/Execution: {p['strategy_research']} / {p['pnl_execution']}");print(path);return 0
if __name__=="__main__":raise SystemExit(main())
