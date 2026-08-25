"""Selection audit for fully-covered versus uncovered observations."""
from __future__ import annotations
from std0_quant.audit.selection_bias import compute_smd

VARIABLES=("y30","initial_qty","first_opp_qty","initial_to_opp_seconds","seconds_to_expiry","online_regime_id")
def audit_coverage_selection(rows,warning_pp=5):
    covered=[r for r in rows if r.get("model_eligible")];uncovered=[r for r in rows if not r.get("model_eligible")];comparisons=[]
    for name in VARIABLES:
        a=[r.get(name) for r in covered];b=[r.get(name) for r in uncovered];smd,note=compute_smd(a,b);comparisons.append({"variable":name,"covered_n":sum(v is not None for v in a),"uncovered_n":sum(v is not None for v in b),"smd":smd,"note":note})
    cr=sum(r["y30"] for r in covered)/len(covered) if covered else None;ur=sum(r["y30"] for r in uncovered)/len(uncovered) if uncovered else None;delta=(cr-ur)*100 if cr is not None and ur is not None else None
    status="NOT_COMPARABLE_NO_COVERED" if not covered else "NOT_COMPARABLE_NO_UNCOVERED" if not uncovered else "COVERAGE_SELECTION_WARNING" if delta is not None and abs(delta)>=warning_pp else "REPORTED"
    return {"n_covered":len(covered),"n_uncovered":len(uncovered),"covered_y30_rate":cr,"uncovered_y30_rate":ur,"y30_delta_pp":delta,"status":status,"comparisons":comparisons}
