"""Feature-level provenance and point-in-time assertions."""
from __future__ import annotations
from dataclasses import asdict, dataclass

PUBLIC_SOURCES={"binance_btc","polymarket_book"}

@dataclass
class FeatureProvenance:
    condition_id:str; feature_name:str; source_type:str; source_file:str|None
    source_event_type:str|None; source_timestamp_min_ms:int|None
    source_timestamp_max_ms:int|None; prediction_ts_ms:int; feature_cutoff_ms:int
    transform_version:str; missing_reason:str|None=None
    def to_dict(self): return asdict(self)

def validate_provenance(rows):
    """Public streams obey cutoff; frozen Phase-1 truth obeys prediction time.

    FirstOpposite aggregates only become known at t0, so applying the public
    t0-1s cutoff to them would be logically impossible. This distinction is
    explicit and auditable through source_type.
    """
    failures=[]
    for row in rows:
        maximum=row.get("source_timestamp_max_ms")
        if maximum is None: continue
        limit=row["feature_cutoff_ms"] if row.get("source_type") in PUBLIC_SOURCES else row["prediction_ts_ms"]
        if maximum>limit: failures.append({"condition_id":row.get("condition_id"),"feature_name":row.get("feature_name"),"source_max":maximum,"limit":limit})
    if failures: raise AssertionError(f"point-in-time provenance failure: {failures[:3]}")
    return True

