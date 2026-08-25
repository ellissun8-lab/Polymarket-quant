"""Low-frequency isolated refresh: episodes -> ledger -> Phase 2A features."""
from __future__ import annotations
import subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main():
    commands=[[sys.executable,str(ROOT/"scripts/build_episodes.py")],[sys.executable,str(ROOT/"scripts/build_event_ledger.py")],[sys.executable,str(ROOT/"scripts/init_prospective_baseline.py")],[sys.executable,str(ROOT/"scripts/build_pretrade_features.py"),"--run-id",f"live-{int(time.time())}"],[sys.executable,str(ROOT/"scripts/run_prospective_checkpoint.py")]]
    for command in commands:
        result=subprocess.run(command,cwd=ROOT)
        if result.returncode:return result.returncode
    return 0
if __name__=="__main__":raise SystemExit(main())
