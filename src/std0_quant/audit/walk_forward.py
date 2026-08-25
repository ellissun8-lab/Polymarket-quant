"""Expanding-window OOS evaluation for transparent Phase 1.6 models."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Mapping, Sequence
import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .conditional_metrics import probability_metrics
from .regime_baselines import historical_probabilities, predict_from_train

FEATURE_NAMES=("initial_qty","first_opp_qty","first_opp_fill_count","initial_to_opp_seconds","up_qty_before_first_opp","down_qty_before_first_opp","old_direction_qty","initial_direction_up")


def week_key(ms:int)->str:
    x=datetime.fromtimestamp(ms/1000,timezone.utc).isocalendar(); return f"{x.year:04d}-W{x.week:02d}"


def build_predictive_rows(rows:Sequence[Mapping])->tuple[np.ndarray,np.ndarray,np.ndarray,list[str],list[str]]:
    X=[]; y=[]; ts=[]; weeks=[]; ids=[]
    for r in rows:
        if not r.get("clean_flag") or r.get("first_opp_end_ms") is None or not r.get("y30_horizon_eligible"): continue
        t0=int(r["first_opp_end_ms"]); first=r.get("initial_first_timestamp_ms")
        values=[r.get("initial_qty"),r.get("first_opp_qty"),r.get("first_opp_fill_count"),
                (t0-int(first))/1000 if first is not None else None,r.get("up_qty_before_first_opp"),
                r.get("down_qty_before_first_opp"),r.get("old_direction_qty"),1 if r.get("initial_direction")=="Up" else 0]
        X.append([float(v) if v is not None else np.nan for v in values]); y.append(int(r["y30"])); ts.append(t0); weeks.append(week_key(t0)); ids.append(str(r.get("condition_id")))
    order=np.argsort(ts)
    return np.asarray(X)[order],np.asarray(y)[order],np.asarray(ts,dtype=np.int64)[order],[weeks[i] for i in order],[ids[i] for i in order]


def _fit_logistic(X,y):
    if len(np.unique(y))<2: return None
    model=make_pipeline(SimpleImputer(strategy="median"),StandardScaler(),LogisticRegression(max_iter=1000,random_state=0)); model.fit(X,y); return model


def run_walk_forward(X,y,timestamps,weeks,min_train_weeks=4,min_test_n=30)->dict:
    unique=sorted(set(weeks)); folds=[]; aggregate={m:{"y":[],"p":[],"weeks":[]} for m in ("global_constant","previous_week","rolling_7d","rolling_14d","ewma","M0","M1","M2","M3")}
    for k in range(min_train_weeks,len(unique)):
        test_week=unique[k]; train_idx=np.asarray([i for i,w in enumerate(weeks) if w<test_week]); test_idx=np.asarray([i for i,w in enumerate(weeks) if w==test_week])
        if len(test_idx)<min_test_n or not len(train_idx): continue
        if timestamps[train_idx].max()>=timestamps[test_idx].min(): raise AssertionError("train must strictly precede test")
        if len(np.unique(y[train_idx]))<2: continue
        baselines=predict_from_train(timestamps[train_idx],y[train_idx],timestamps[test_idx]); base_scores={name:probability_metrics(y[test_idx],p,[test_week]*len(test_idx)) for name,p in baselines.items()}
        train_candidates=historical_probabilities(timestamps[train_idx],y[train_idx],float(y[train_idx].mean()))
        train_scores={name:probability_metrics(y[train_idx],p,[weeks[i] for i in train_idx]) for name,p in train_candidates.items()}
        best=min(train_scores,key=lambda name:(train_scores[name]["brier"],train_scores[name]["logloss"])); m1=baselines[best]
        episode=_fit_logistic(X[train_idx],y[train_idx]); m2=episode.predict_proba(X[test_idx])[:,1]
        # M3 adds a strictly historical regime probability as a state feature.
        train_state=train_candidates[best]
        combined=_fit_logistic(np.c_[X[train_idx],train_state],y[train_idx]); m3=combined.predict_proba(np.c_[X[test_idx],m1])[:,1]
        preds={**baselines,"M0":baselines["global_constant"],"M1":m1,"M2":m2,"M3":m3}; metrics={m:probability_metrics(y[test_idx],p,[test_week]*len(test_idx)) for m,p in preds.items()}
        fold={"fold_id":len(folds)+1,"train_start":int(timestamps[train_idx].min()),"train_end":int(timestamps[train_idx].max()),"test_start":int(timestamps[test_idx].min()),"test_end":int(timestamps[test_idx].max()),"test_week":test_week,"train_n":len(train_idx),"test_n":len(test_idx),"train_positive_rate":float(y[train_idx].mean()),"test_positive_rate":float(y[test_idx].mean()),"n_train_weeks":k,"best_regime_baseline":best,"metrics":metrics,"delta_brier":metrics["M1"]["brier"]-metrics["M3"]["brier"],"delta_logloss":metrics["M1"]["logloss"]-metrics["M3"]["logloss"]}
        folds.append(fold)
        for m,p in preds.items(): aggregate[m]["y"].extend(y[test_idx].tolist());aggregate[m]["p"].extend(p.tolist());aggregate[m]["weeks"].extend([test_week]*len(test_idx))
    agg={m:probability_metrics(v["y"],v["p"],v["weeks"]) for m,v in aggregate.items() if v["y"]}
    valid=len(folds); improved_b=sum(f["delta_brier"]>0 for f in folds); improved_l=sum(f["delta_logloss"]>0 for f in folds)
    return {"folds":folds,"aggregate":agg,"n_valid_folds":valid,"pct_folds_brier_improved":improved_b/valid if valid else None,"pct_folds_logloss_improved":improved_l/valid if valid else None,
            "delta_brier_vs_regime":agg["M1"]["brier"]-agg["M3"]["brier"] if valid else None,"delta_logloss_vs_regime":agg["M1"]["logloss"]-agg["M3"]["logloss"] if valid else None,"delta_macro_auc_vs_regime":(agg["M3"]["macro_auc"]-agg["M1"]["macro_auc"]) if valid and agg["M3"]["macro_auc"] is not None and agg["M1"]["macro_auc"] is not None else None}
