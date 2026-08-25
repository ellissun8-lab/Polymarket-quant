"""Prospective Phase 2A gate check; never launches modeling or Phase 2B."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"src"))
from std0_quant.audit.prospective import prospective_status
from std0_quant.config import load_settings,resolve_path
def main():
    s=load_settings();p=prospective_status(resolve_path(s,"state"),resolve_path(s,"reports"));ready=p["readiness_status"]=="READY_FOR_PHASE2A_REVALIDATION";print(p["readiness_status"]);print(f"covered={p['fully_covered']}/5000 days={p['covered_calendar_days']}/14 provenance_violations={p['provenance_violations']}");return 0 if ready else 2
if __name__=="__main__":raise SystemExit(main())
