"""Build and execute the reader-facing Phase 2B data-quality companion notebook."""
from __future__ import annotations
import json,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
import nbformat as nbf
from nbclient import NotebookClient


def main()->int:
    report_path=sorted((ROOT/"data/reports").glob("phase2b_research_*.json"))[-1]
    report=json.loads(report_path.read_text(encoding="utf-8"));d=report["data_maturity"];a=report["artifacts"]
    nb=nbf.v4.new_notebook();nb.metadata.kernelspec={"display_name":"Python 3","language":"python","name":"python3"}
    nb.cells=[
        nbf.v4.new_markdown_cell(f"# Phase 2B Exploratory Data-Quality Audit\n\n## tl;dr\n\nThe closed-file v4-only audit contains **{d['n_complete_markets']} complete market**, **{d['n_btc_ticks']:,} BTC ticks**, **{d['n_valid_pm_states']:,} valid PM states**, and **{d['n_shocks']} overlapping ≥1bp shock anchors**. Evidence maturity is **{d['maturity']}**; B2 is **{report['std0_sample_availability']['status']}**. These outputs do not support causal, alpha, profitability, execution, or trading claims."),
        nbf.v4.new_markdown_cell("## Context & Methods\n\nThis notebook is a read-only companion to the versioned JSON/Parquet artifacts. It checks grain, collector version, deterministic ordering, valid-book selection, raw lineage population, and bounded result tables.\n\n### Key Assumptions\n\n- Only formal full-lifecycle `phase2a_prospective_v4` markets are eligible.\n- Exchange timestamp is the primary research clock; receive time remains available for clock sensitivity.\n- Shock anchors overlap and are descriptive, not independent statistical observations."),
        nbf.v4.new_code_cell(f"from pathlib import Path\nimport json\nimport pandas as pd\nfrom IPython.display import display\nreport_path = Path(r'''{report_path}''')\nreport = json.loads(report_path.read_text(encoding='utf-8'))\ntimeline_path = Path(r'''{a['market_timeline']}''')\ngrid_path = Path(r'''{a['market_grids']}''')\ncorr_path = Path(r'''{a['cross_correlation']}''')\nresponse_path = Path(r'''{a['event_response']}''')"),
        nbf.v4.new_markdown_cell("## Data\n\n### 1. Load bounded columns and confirm grain"),
        nbf.v4.new_code_cell("timeline = pd.read_parquet(timeline_path, columns=['condition_id','source','event_timestamp_ms','receive_timestamp_ms','collector_version','book_valid','raw_file','raw_line'])\ngrids = pd.read_parquet(grid_path)\nquality = pd.DataFrame({\n    'metric':['timeline_rows','markets','btc_rows','pm_rows','v4_share','missing_raw_reference','grid_rows','grid_versions'],\n    'value':[len(timeline),timeline.condition_id.nunique(),(timeline.source=='BTC').sum(),(timeline.source=='PM').sum(),(timeline.collector_version=='phase2a_prospective_v4').mean(),timeline[['raw_file','raw_line']].isna().any(axis=1).sum(),len(grids),sorted(grids.grid_ms.unique().tolist())]\n})\ndisplay(quality)"),
        nbf.v4.new_markdown_cell("### 2. Ordering and valid-state checks"),
        nbf.v4.new_code_cell("checks = {\n 'timeline_sorted': timeline[['event_timestamp_ms','receive_timestamp_ms','source']].reset_index(drop=True).equals(timeline.sort_values(['event_timestamp_ms','receive_timestamp_ms','source'])[['event_timestamp_ms','receive_timestamp_ms','source']].reset_index(drop=True)),\n 'v4_only': bool((timeline.collector_version=='phase2a_prospective_v4').all()),\n 'pm_rows_valid': bool(timeline.loc[timeline.source=='PM','book_valid'].fillna(False).all()),\n 'raw_lineage_complete': int(timeline[['raw_file','raw_line']].isna().any(axis=1).sum()) == 0,\n 'phase2a_frozen': report['phase2a_frozen_invariants']['status'] == 'PASS',\n 'sha_failures': len(report['recorder_cohort_state']['raw_integrity']['sha256_failures'])\n}\nchecks"),
        nbf.v4.new_markdown_cell("## Results\n\n### 3. Lead-lag summaries"),
        nbf.v4.new_code_cell("corr = pd.read_parquet(corr_path).sort_values('lag_ms')\nresponse = pd.read_parquet(response_path).sort_values(['shock_bucket','horizon_ms'])\ndisplay(corr.nlargest(7, 'correlation')[['lag_ms','n','correlation']])\ndisplay(response.head(18))"),
        nbf.v4.new_markdown_cell("### 4. Coverage and missingness by grid"),
        nbf.v4.new_code_cell("grid_quality = grids.groupby('grid_ms').agg(rows=('timestamp_ms','size'), book_valid_rate=('book_valid','mean'), btc_missing_rate=('btc_price',lambda s:s.isna().mean()), pm_missing_rate=('pm_mid',lambda s:s.isna().mean())).reset_index()\ndisplay(grid_quality)"),
        nbf.v4.new_markdown_cell("## Takeaways\n\n- The artifact is v4-only, ordered, raw-referenced, and built from closed SHA-verified files.\n- Row-level invalid PM states are excluded before grids and response estimation; the report retains their exclusion count.\n- A one-market peak correlation lag is a mechanism hint only. It is not general evidence and must be revisited as markets accumulate.\n- B2 remains disabled until the primary cohort naturally contains a fully-covered lineage/PIT-passing std0 observation.\n- Phase 2A thresholds and hashes remain unchanged."),
    ]
    out=ROOT/"notebooks"/"phase2b_exploratory_data_quality.ipynb";out.parent.mkdir(parents=True,exist_ok=True)
    client=NotebookClient(nb,timeout=120,kernel_name="python3",resources={"metadata":{"path":str(ROOT)}});client.execute();nbf.write(nb,out);print(out);return 0

if __name__=="__main__":raise SystemExit(main())
