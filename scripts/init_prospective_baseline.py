"""Create once, then verify, the immutable historical truth snapshot."""
from __future__ import annotations
import json, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"src"))
import pyarrow.parquet as pq
from std0_quant.audit.prospective import create_baseline_snapshot,verify_baseline_snapshot
from std0_quant.config import load_settings,resolve_path

def main()->int:
    settings=load_settings();ledger=resolve_path(settings,"derived")/"event_ledger.parquet"
    path=resolve_path(settings,"state")/"baseline_truth_snapshot.json"
    rows=pq.read_table(ledger).to_pylist()
    if not path.exists():snapshot=create_baseline_snapshot(rows,path);created=True
    else:snapshot=json.loads(path.read_text(encoding="utf-8"));created=False
    result=verify_baseline_snapshot(snapshot,rows)
    print(json.dumps({"created":created,"path":str(path),**result},indent=2))
    return 0 if result["status"]=="PASS" else 3
if __name__=="__main__":raise SystemExit(main())
