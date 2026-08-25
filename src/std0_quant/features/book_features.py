"""FirstOpposite-frame Polymarket book state and dynamics."""
from __future__ import annotations
from .book_reconstruction import depth,latest_books,obi,row_timestamp
from .coverage_gate import bounded_book_coverage

BOOK_TRANSFORM_VERSION="clob_receive_time_v2_valid_state_5s"

def compute_book_features(rows,cutoff_ms,opp_outcome,initial_outcome,stale_after_ms=5000):
    has_validity=any("book_state_valid" in r for r in rows)
    eligible=[r for r in rows if r.get("receive_timestamp_ms") is not None and int(r["receive_timestamp_ms"])<=cutoff_ms and (not has_validity or r.get("book_state_valid") is True)]
    latest=latest_books(eligible,cutoff_ms,stale_after_ms); by_outcome={r.get("outcome"):r for r in latest.values() if r}
    out={}
    for frame,outcome in (("opp",opp_outcome),("initial",initial_outcome)):
        row=by_outcome.get(outcome)
        for name in ("best_bid","best_ask","mid","spread"):out[f"{frame}_{name}"]=row.get(name) if row else None
        for count in (1,3):
            out[f"{frame}_bid_depth_{count}"]=depth(row.get("bids"),count) if row else None;out[f"{frame}_ask_depth_{count}"]=depth(row.get("asks"),count) if row else None;out[f"{frame}_obi_{count}"]=obi(row,count)
    for seconds in (1,3,5,10):
        previous_rows=latest_books(eligible,cutoff_ms-seconds*1000,stale_after_ms);previous={r.get("outcome"):r for r in previous_rows.values() if r}.get(opp_outcome)
        previous_mid=previous.get("mid") if previous else None; previous_obi=obi(previous,1)
        out[f"pm_mid_change_{seconds}s"]=(out.get("opp_mid")-previous_mid) if out.get("opp_mid") is not None and previous_mid is not None else None
        if seconds in (1,5): out[f"pm_obi_change_{seconds}s"]=(out.get("opp_obi_1")-previous_obi) if out.get("opp_obi_1") is not None and previous_obi is not None else None
        if seconds in (1,5): out[f"book_update_count_{seconds}s"]=sum(cutoff_ms-seconds*1000<row_timestamp(r)<=cutoff_ms for r in eligible) if eligible else None
    previous5=latest_books(eligible,cutoff_ms-5000,stale_after_ms);p5={r.get("outcome"):r for r in previous5.values() if r}.get(opp_outcome);out["pm_spread_change_5s"]=(out["opp_spread"]-p5.get("spread")) if out.get("opp_spread") is not None and p5 and p5.get("spread") is not None else None
    stamps=[row_timestamp(r) for r in eligible]
    for seconds in (5,10,30):out[f"book_pre{seconds}_coverage_pct"]=bounded_book_coverage(eligible,cutoff_ms-seconds*1000,cutoff_ms,stale_after_ms)
    out["_source_min_ms"]=min(stamps,default=None);out["_source_max_ms"]=max(stamps,default=None)
    return out
