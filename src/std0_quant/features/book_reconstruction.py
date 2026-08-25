"""Cutoff-bounded reconstruction of persisted Polymarket book rows."""
from __future__ import annotations

def row_timestamp(row):
    # Receive time is the conservative observable-time clock.
    return int(row["receive_timestamp_ms"])

def latest_books(rows,cutoff_ms,stale_after_ms=2000):
    latest={}
    for row in sorted(rows,key=row_timestamp):
        ts=row_timestamp(row)
        if ts>cutoff_ms: break
        token=row.get("token_id")
        if token: latest[token]=row
    return {token:row for token,row in latest.items() if cutoff_ms-row_timestamp(row)<=stale_after_ms}

def depth(levels,levels_count):
    if not isinstance(levels,list):return None
    return sum(float(x.get("size") or 0) for x in levels[:levels_count])

def obi(row,levels_count):
    if not row:return None
    bid=depth(row.get("bids"),levels_count);ask=depth(row.get("asks"),levels_count)
    if bid is None or ask is None or bid+ask==0:return None
    return (bid-ask)/(bid+ask)
