"""Strictly cutoff-bounded Binance trade-tick features."""
from __future__ import annotations
import math
import numpy as np
from .coverage_gate import bucket_coverage

BTC_TRANSFORM_VERSION="btc_ticks_v1_last_tick"

def _ts(row): return int(row.get("exchange_timestamp_ms"))
def _last_at(rows,target):
    eligible=[r for r in rows if _ts(r)<=target]
    return max(eligible,key=_ts) if eligible else None

def compute_btc_features(rows,market_start_ms,cutoff_ms):
    ordered=sorted((r for r in rows if r.get("exchange_timestamp_ms") is not None and r.get("price") is not None),key=_ts)
    current=_last_at(ordered,cutoff_ms); start=_last_at(ordered,market_start_ms)
    if start is not None and market_start_ms-_ts(start)>1000:start=None
    out={"btc_last_price":float(current["price"]) if current else None,"btc_start_price":float(start["price"]) if start else None}
    out["btc_cutoff_price"]=out["btc_last_price"]
    out["btc_distance_bps"]=10000*math.log(out["btc_last_price"]/out["btc_start_price"]) if current and start and float(start["price"])>0 else None
    for seconds in (1,3,5,10,30):
        old=_last_at(ordered,cutoff_ms-seconds*1000)
        out[f"btc_ret_{seconds}s"]=math.log(float(current["price"])/float(old["price"])) if current and old and float(old["price"])>0 else None
    for seconds in (5,10,30):
        window=[r for r in ordered if cutoff_ms-seconds*1000<_ts(r)<=cutoff_ms]
        returns=np.diff(np.log([float(r["price"]) for r in window])) if len(window)>1 else np.array([])
        out[f"btc_rv_{seconds}s"]=float(np.sqrt(np.sum(returns**2))) if len(returns) else None
    for seconds in (1,5,30):
        window=[r for r in ordered if cutoff_ms-seconds*1000<_ts(r)<=cutoff_ms]
        out[f"btc_trade_count_{seconds}s"]=len(window) if ordered else None
        out[f"btc_volume_{seconds}s"]=sum(float(r.get("size") or 0) for r in window) if ordered else None
        signed=[(-1 if r.get("buyer_is_maker") else 1)*float(r.get("size") or 0) for r in window if r.get("buyer_is_maker") is not None]
        out[f"btc_signed_flow_{seconds}s"]=sum(signed) if len(signed)==len(window) and window else None
    for seconds in (10,30): out[f"btc_pre{seconds}_coverage_pct"]=bucket_coverage([_ts(r) for r in ordered],cutoff_ms-seconds*1000,cutoff_ms)
    source_floor=min(market_start_ms-1000,cutoff_ms-30000)
    used=[_ts(r) for r in ordered if source_floor<=_ts(r)<=cutoff_ms]
    out["_source_min_ms"]=min(used,default=None);out["_source_max_ms"]=max(used,default=None)
    return out
