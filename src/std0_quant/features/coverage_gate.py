"""Explicit live-data coverage gate; never imputes absent streams."""
from __future__ import annotations
import math

DEFAULT_BTC_THRESHOLD=.99; DEFAULT_BOOK_THRESHOLD=.99

def gate_coverage(btc_pre30,book_pre10,btc_threshold=DEFAULT_BTC_THRESHOLD,book_threshold=DEFAULT_BOOK_THRESHOLD):
    reasons=[]
    if btc_pre30 is None: reasons.append("BTC_PRE30_MISSING")
    elif not math.isfinite(btc_pre30): reasons.append("BTC_PRE30_INVALID")
    elif btc_pre30<btc_threshold: reasons.append("BTC_PRE30_BELOW_THRESHOLD")
    if book_pre10 is None: reasons.append("BOOK_PRE10_MISSING")
    elif not math.isfinite(book_pre10): reasons.append("BOOK_PRE10_INVALID")
    elif book_pre10<book_threshold: reasons.append("BOOK_PRE10_BELOW_THRESHOLD")
    return len(reasons)==0,";".join(reasons) if reasons else None

def bucket_coverage(timestamps,start_ms,end_ms,bucket_ms=1000):
    if end_ms<=start_ms:return None
    n=math.ceil((end_ms-start_ms)/bucket_ms)
    occupied={min((int(t)-start_ms)//bucket_ms,n-1) for t in timestamps if start_ms<=int(t)<=end_ms}
    if not occupied:return None
    return len(occupied)/n

def bounded_book_coverage(rows,start_ms,end_ms,stale_after_ms=5000,bucket_ms=1000):
    """Weakest-token valid reconstructed-state coverage, with no unbounded fill."""
    if end_ms<=start_ms:return None
    valid=[r for r in rows if r.get("book_state_valid") is True and r.get("token_id") and r.get("receive_timestamp_ms") is not None]
    if not valid:
        # Backwards compatibility for pre-live fixtures/raw without validity fields.
        if rows and not any("book_state_valid" in r for r in rows):
            return bucket_coverage([r.get("receive_timestamp_ms") for r in rows if r.get("receive_timestamp_ms") is not None],start_ms,end_ms,bucket_ms)
        return None
    n=math.ceil((end_ms-start_ms)/bucket_ms);by_token={}
    for r in valid:by_token.setdefault(str(r["token_id"]),[]).append(int(r["receive_timestamp_ms"]))
    coverages=[]
    for timestamps in by_token.values():
        intervals=[]
        for t in sorted(timestamps):
            lo=max(t,start_ms);hi=min(t+stale_after_ms,end_ms)
            if hi<=lo:continue
            if intervals and lo<=intervals[-1][1]:intervals[-1]=(intervals[-1][0],max(intervals[-1][1],hi))
            else:intervals.append((lo,hi))
        occupied={i for i in range(n) if any(lo<=start_ms+i*bucket_ms and hi>=min(start_ms+(i+1)*bucket_ms,end_ms) for lo,hi in intervals)}
        coverages.append(len(occupied)/n)
    return min(coverages) if coverages else None
