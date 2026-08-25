"""Build the Phase 2A point-in-time feature dataset (offline/read-only)."""
from __future__ import annotations
import argparse,hashlib,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"src"))
import pyarrow as pa
import pyarrow.parquet as pq
from std0_quant.config import load_settings,resolve_path
from std0_quant.features.pretrade_builder import build_rows
from std0_quant.storage import read_ndjson

def sha(path):
    h=hashlib.sha256();h.update(Path(path).read_bytes());return h.hexdigest()
def load_raw(directory):
    rows=[]
    if Path(directory).is_dir():
        for path in sorted(Path(directory).rglob("*.ndjson")):
            for row in read_ndjson(path):row["_source_file"]=str(path);rows.append(row)
    return rows
def write(path,rows):
    Path(path).parent.mkdir(parents=True,exist_ok=True);pq.write_table(pa.Table.from_pylist(rows if rows else [{"status":"NO_ROWS"}]),path)
def execute(ledger_path=None,output_dir=None,cutoff_mode="cutoff_1",btc_threshold=.99,book_threshold=.99,run_id="manual"):
    settings=load_settings();ledger=Path(ledger_path) if ledger_path else resolve_path(settings,"derived")/"event_ledger.parquet";features_dir=Path(output_dir) if output_dir else resolve_path(settings,"derived")/"features";before=sha(ledger);rows=pq.read_table(ledger).to_pylist();btc=load_raw(resolve_path(settings,"raw_btc_ticks"));books=load_raw(resolve_path(settings,"raw_polymarket_book"));book_index={}
    for row in books:
        if row.get("condition_id"):book_index.setdefault(row["condition_id"],[]).append(row)
    online={};regime_files=sorted((resolve_path(settings,"derived")/"audit").glob("online_regimes_*.parquet"))
    if regime_files:online={r["condition_id"]:int(r["online_regime_id"]) for r in pq.read_table(regime_files[-1],columns=["condition_id","online_regime_id"]).to_pylist()}
    suffix=run_id if cutoff_mode=="cutoff_1" else f"{run_id}_{cutoff_mode}";paths={"features":features_dir/f"pretrade_features_{suffix}.parquet","provenance":features_dir/f"feature_provenance_{suffix}.parquet","coverage":features_dir/f"coverage_audit_{suffix}.parquet"};features_dir.mkdir(parents=True,exist_ok=True);feature_rows=[];coverage=[];provenance_count=0;writer=None
    for start in range(0,len(rows),500):
        batch_features,batch_provenance,batch_coverage=build_rows(rows[start:start+500],btc,book_index,cutoff_mode,btc_threshold,book_threshold,online);feature_rows.extend(batch_features);coverage.extend(batch_coverage);provenance_count+=len(batch_provenance)
        for item in batch_provenance:
            if item["source_type"]=="phase1_truth":item["source_file"]=str(ledger)
        if batch_provenance:
            table=pa.Table.from_pylist(batch_provenance)
            if writer is None:writer=pq.ParquetWriter(paths["provenance"],table.schema)
            writer.write_table(table)
    if writer:writer.close()
    else:write(paths["provenance"],[])
    if before!=sha(ledger):raise AssertionError("Phase 1 ledger changed")
    write(paths["features"],feature_rows);write(paths["coverage"],coverage);return feature_rows,provenance_count,coverage,paths
def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--ledger");p.add_argument("--output");p.add_argument("--cutoff",choices=("cutoff_0","cutoff_1","cutoff_2"),default="cutoff_1");p.add_argument("--btc-coverage-threshold",type=float,default=.99);p.add_argument("--book-coverage-threshold",type=float,default=.99);p.add_argument("--run-id",default="manual");a=p.parse_args(argv);_,_,c,paths=execute(a.ledger,a.output,a.cutoff,a.btc_coverage_threshold,a.book_coverage_threshold,a.run_id);print(paths);print(f"observations={len(c)} eligible={sum(r['model_eligible'] for r in c)}")
if __name__=="__main__":main()
