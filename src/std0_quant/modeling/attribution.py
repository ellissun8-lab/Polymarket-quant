"""Aggregate feature-group increments and coefficient stability."""
from __future__ import annotations
from collections import defaultdict
import numpy as np
from std0_quant.audit.conditional_metrics import probability_metrics

def attribution(predictions,fold_metrics):
    grouped=defaultdict(list)
    for r in predictions:grouped[r["model"]].append(r)
    aggregate={m:probability_metrics([r["y30"] for r in v],[r["probability"] for r in v],[r["test_week"] for r in v]) for m,v in grouped.items()};out=[]
    for model in ("M2","M3","M4"):
        if "M1" not in aggregate or model not in aggregate:continue
        folds=sorted(set(r["fold_id"] for r in fold_metrics if r["model"]==model));improved=[]
        for f in folds:
            a=next(r for r in fold_metrics if r["fold_id"]==f and r["model"]=="M1");b=next(r for r in fold_metrics if r["fold_id"]==f and r["model"]==model);improved.append(b["brier"]<a["brier"])
        out.append({"increment":f"{model}-M1","delta_brier":aggregate["M1"]["brier"]-aggregate[model]["brier"],"delta_logloss":aggregate["M1"]["logloss"]-aggregate[model]["logloss"],"pct_folds_brier_improved":sum(improved)/len(improved) if improved else None})
    return aggregate,out

def coefficient_stability(rows):
    groups=defaultdict(list)
    for r in rows:groups[(r["model"],r["feature_name"])].append(r["coefficient"])
    return [{"model":m,"feature_name":f,"n_folds":len(v),"mean":float(np.mean(v)),"median":float(np.median(v)),"std":float(np.std(v)),"sign_consistency":max(sum(x>0 for x in v),sum(x<0 for x in v))/len(v)} for (m,f),v in sorted(groups.items())]
