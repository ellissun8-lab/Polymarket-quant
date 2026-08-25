"""Weekly feature summaries, SMD and PSI drift diagnostics."""
from __future__ import annotations
import numpy as np
from .walk_forward import FEATURE_NAMES


def _summary(v):
    a=np.asarray(v,float); a=a[np.isfinite(a)]
    return {"count":len(a),"mean":float(a.mean()) if len(a) else None,"median":float(np.median(a)) if len(a) else None,"std":float(a.std(ddof=1)) if len(a)>1 else 0.0 if len(a) else None,"p25":float(np.percentile(a,25)) if len(a) else None,"p75":float(np.percentile(a,75)) if len(a) else None}


def _smd(a,b):
    a=a[np.isfinite(a)];b=b[np.isfinite(b)]
    if not len(a) or not len(b): return None
    den=np.sqrt((a.var()+b.var())/2)
    return float((b.mean()-a.mean())/den) if den else (0.0 if a.mean()==b.mean() else None)


def _psi(expected,actual,bins=10):
    e=expected[np.isfinite(expected)];a=actual[np.isfinite(actual)]
    if not len(e) or not len(a): return None
    edges=np.unique(np.quantile(e,np.linspace(0,1,bins+1)))
    if len(edges)<2:return 0.0 if np.all(a==e[0]) else None
    edges[0]=-np.inf;edges[-1]=np.inf; ep=np.histogram(e,edges)[0]/len(e);ap=np.histogram(a,edges)[0]/len(a);eps=1e-6
    ep=np.clip(ep,eps,None);ap=np.clip(ap,eps,None);return float(np.sum((ap-ep)*np.log(ap/ep)))


def run_feature_drift(X,weeks):
    out=[]; unique=sorted(set(weeks))
    if not unique:return out
    ref=np.asarray([i for i,w in enumerate(weeks) if w==unique[0]])
    for j,name in enumerate(FEATURE_NAMES):
        for week in unique:
            idx=np.asarray([i for i,w in enumerate(weeks) if w==week]);psi=_psi(X[ref,j],X[idx,j]);smd=_smd(X[ref,j],X[idx,j])
            label="UNDEFINED" if psi is None else "LOW" if psi<.1 else "NOTICEABLE" if psi<=.25 else "MATERIAL"
            out.append({"feature":name,"week":week,**_summary(X[idx,j]),"smd_vs_first_week":smd,"psi_vs_first_week":psi,"psi_label":label})
    return out

