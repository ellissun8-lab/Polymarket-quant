"""Strictly historical regime-only probability baselines."""
from __future__ import annotations
from collections import deque
from datetime import datetime, timezone
from typing import Sequence
import numpy as np


def _week(ms: int) -> str:
    x = datetime.fromtimestamp(ms / 1000, timezone.utc).isocalendar(); return f"{x.year:04d}-W{x.week:02d}"


def historical_probabilities(timestamps: Sequence[int], y: Sequence[int], train_rate: float, alpha: float = .1) -> dict[str, np.ndarray]:
    order = np.argsort(timestamps); ts=np.asarray(timestamps,dtype=np.int64)[order]; yy=np.asarray(y,int)[order]
    out={k:np.empty(len(yy)) for k in ("global_constant","previous_week","rolling_7d","rolling_14d","ewma")}
    week_values: dict[str,list[int]]={}; hist: deque[tuple[int,int]]=deque(); ewma=train_rate
    for i,(t,label) in enumerate(zip(ts,yy)):
        out["global_constant"][i]=train_rate
        current=_week(int(t)); prior=[k for k in sorted(week_values) if k<current]
        out["previous_week"][i]=np.mean(week_values[prior[-1]]) if prior else train_rate
        for days in (7,14):
            vals=[v for ht,v in hist if ht < t and ht >= t-days*86400000]
            out[f"rolling_{days}d"][i]=np.mean(vals) if vals else train_rate
        out["ewma"][i]=ewma
        hist.append((int(t),int(label))); week_values.setdefault(current,[]).append(int(label)); ewma=alpha*label+(1-alpha)*ewma
    inverse=np.argsort(order)
    return {k:np.clip(v[inverse],1e-6,1-1e-6) for k,v in out.items()}


def predict_from_train(train_ts, train_y, test_ts, alpha=.1) -> dict[str,np.ndarray]:
    train_ts=np.asarray(train_ts,dtype=np.int64); train_y=np.asarray(train_y,int); test_ts=np.asarray(test_ts,dtype=np.int64)
    rate=float(train_y.mean()); joined_ts=np.r_[train_ts,test_ts]; joined_y=np.r_[train_y,np.zeros(len(test_ts),int)]
    # Sequential helper would learn fake test labels; calculate each test from train only.
    base={k:[] for k in ("global_constant","previous_week","rolling_7d","rolling_14d","ewma")}
    train_weeks={}
    for t,v in zip(train_ts,train_y): train_weeks.setdefault(_week(int(t)),[]).append(int(v))
    ewma=rate
    for v in train_y[np.argsort(train_ts)]: ewma=alpha*v+(1-alpha)*ewma
    for t in test_ts:
        base["global_constant"].append(rate); prior=[k for k in sorted(train_weeks) if k<_week(int(t))]
        base["previous_week"].append(float(np.mean(train_weeks[prior[-1]])) if prior else rate)
        for d in (7,14):
            mask=(train_ts<t)&(train_ts>=t-d*86400000); base[f"rolling_{d}d"].append(float(train_y[mask].mean()) if mask.any() else rate)
        base["ewma"].append(float(ewma))
    return {k:np.clip(np.asarray(v),1e-6,1-1e-6) for k,v in base.items()}

