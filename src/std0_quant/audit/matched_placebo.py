"""Calendar-matched universe placebo comparison."""
from __future__ import annotations
from collections import defaultdict
from typing import Mapping,Sequence
from .walk_forward import week_key


def calendar_matched(reference:Sequence[Mapping],other:Sequence[Mapping],name:str,min_week_n:int=10)->dict:
    def rates(rows):
        d=defaultdict(list)
        for r in rows:
            if r.get("clean_flag") and r.get("y30_horizon_eligible") and r.get("market_start_ms") is not None:d[week_key(int(r["market_start_ms"]))].append(int(r["y30"]))
        return d
    a,b=rates(reference),rates(other); common=sorted(w for w in set(a)&set(b) if len(a[w])>=min_week_n and len(b[w])>=min_week_n)
    raw_a=[v for z in a.values() for v in z];raw_b=[v for z in b.values() for v in z]
    raw=(sum(raw_a)/len(raw_a)-sum(raw_b)/len(raw_b))*100 if raw_a and raw_b else None
    if not common:return {"universe":name,"status":"NOT_COMPARABLE","reason":"no common ISO weeks with sufficient observable samples","raw_pooled_delta_pp":raw,"calendar_matched_delta_pp":None,"weighted_matched_delta_pp":None,"n_common_weeks":0}
    deltas=[sum(a[w])/len(a[w])-sum(b[w])/len(b[w]) for w in common];weights=[min(len(a[w]),len(b[w])) for w in common]
    return {"universe":name,"status":"COMPARABLE","reason":None,"raw_pooled_delta_pp":raw,"calendar_matched_delta_pp":sum(deltas)/len(deltas)*100,"weighted_matched_delta_pp":sum(d*w for d,w in zip(deltas,weights))/sum(weights)*100,"n_common_weeks":len(common)}

