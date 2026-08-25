"""Assemble Phase 2A point-in-time rows with explicit missingness."""
from __future__ import annotations
import hashlib
from datetime import datetime,timezone
from .btc_features import BTC_TRANSFORM_VERSION,compute_btc_features
from .book_features import BOOK_TRANSFORM_VERSION,compute_book_features
from .coverage_gate import gate_coverage
from .provenance import FeatureProvenance,validate_provenance
from .std0_state_features import build_std0_state

def cutoff_timestamp(prediction_ts_ms,mode):
    if mode=="cutoff_0":return prediction_ts_ms-1
    if mode=="cutoff_1":return prediction_ts_ms-1000
    if mode=="cutoff_2":return prediction_ts_ms-2000
    raise ValueError(f"unknown cutoff mode: {mode}")

def iso_week(ms):
    x=datetime.fromtimestamp(ms/1000,timezone.utc).isocalendar();return f"{x.year:04d}-W{x.week:02d}"

def build_rows(ledger_rows,btc_rows,book_by_condition,cutoff_mode="cutoff_1",btc_threshold=.99,book_threshold=.99,online_regimes=None):
    features=[];provenance=[];coverage=[];online_regimes=online_regimes or {}
    public_names=None
    for row in ledger_rows:
        if not row.get("clean_flag") or row.get("first_opp_end_ms") is None or not row.get("y30_horizon_eligible"):continue
        cid=row["condition_id"];prediction=int(row["first_opp_end_ms"]);cutoff=cutoff_timestamp(prediction,cutoff_mode);base=build_std0_state(row)
        btc=compute_btc_features(btc_rows,int(row["market_start_ms"]),cutoff);book=compute_book_features(book_by_condition.get(cid,[]),cutoff,row.get("first_opp_direction"),row.get("initial_direction"));direction=base["direction_sign"]
        btc_files=sorted(set(str(r.get("_source_file")) for r in btc_rows if r.get("_source_file") and r.get("exchange_timestamp_ms") is not None and btc.get("_source_min_ms") is not None and int(btc["_source_min_ms"])<=int(r["exchange_timestamp_ms"])<=cutoff)); book_rows=book_by_condition.get(cid,[]);book_files=sorted(set(str(r.get("_source_file")) for r in book_rows if r.get("_source_file") and r.get("receive_timestamp_ms") is not None and int(r["receive_timestamp_ms"])<=cutoff))
        for seconds in (1,3,5,10,30):
            name=f"btc_ret_{seconds}s"
            if name in btc:btc[f"btc_move_toward_opp_{seconds}s"]=direction*btc[name] if btc[name] is not None else None
        base.update({k:v for k,v in btc.items() if not k.startswith("_")});base.update({k:v for k,v in book.items() if not k.startswith("_")})
        feature_row_id=hashlib.sha256(f"{cid}|{prediction}|{cutoff_mode}".encode()).hexdigest()
        base.update({"feature_row_id":feature_row_id,"condition_id":cid,"prediction_ts_ms":prediction,"feature_cutoff_ms":cutoff,"cutoff_mode":cutoff_mode,"market_start_ms":row["market_start_ms"],"market_end_ms":row["market_end_ms"],"y30":int(row["y30"]),"iso_week":iso_week(int(row["market_start_ms"])),"online_regime_id":online_regimes.get(cid,0),"first_opp_fill_price":row.get("first_opp_vwap")})
        for ref in ("mid","bid","ask"):
            val=book.get(f"opp_best_{ref}") if ref in ("bid","ask") else book.get("opp_mid");base[f"fill_minus_prev_{ref}"]=float(row["first_opp_vwap"])-val if val is not None else None
        eligible,reasons=gate_coverage(btc.get("btc_pre30_coverage_pct"),book.get("book_pre10_coverage_pct"),btc_threshold,book_threshold);base["model_eligible"]=eligible;base["model_ineligible_reason"]=reasons
        coverage.append({"condition_id":cid,"cutoff_mode":cutoff_mode,"btc_pre30_coverage_pct":btc.get("btc_pre30_coverage_pct"),"btc_pre10_coverage_pct":btc.get("btc_pre10_coverage_pct"),"book_pre30_coverage_pct":book.get("book_pre30_coverage_pct"),"book_pre10_coverage_pct":book.get("book_pre10_coverage_pct"),"book_pre5_coverage_pct":book.get("book_pre5_coverage_pct"),"model_eligible":eligible,"missing_reason":base["model_ineligible_reason"]})
        source_phase_min=int(row["initial_first_timestamp_ms"])
        metadata={"feature_row_id","condition_id","prediction_ts_ms","feature_cutoff_ms","cutoff_mode","market_start_ms","market_end_ms","y30","iso_week","online_regime_id","model_eligible","model_ineligible_reason"}
        for name,value in base.items():
            if name in metadata:continue
            if name.startswith("btc_"):stype="binance_btc";smin=btc.get("_source_min_ms");smax=btc.get("_source_max_ms");version=BTC_TRANSFORM_VERSION;reason=None if value is not None else "NO_BTC_DATA_BEFORE_CUTOFF";files=btc_files;event="trade"
            elif name.startswith(("opp_best","opp_bid_depth","opp_ask_depth","opp_mid","opp_spread","opp_obi","initial_best","initial_mid","initial_spread","initial_bid_depth","initial_ask_depth","initial_obi","pm_","book_","fill_minus_prev")):stype="polymarket_book";smin=book.get("_source_min_ms");smax=book.get("_source_max_ms");version=BOOK_TRANSFORM_VERSION;reason=None if value is not None else "NO_VALID_BOOK_BEFORE_CUTOFF";files=book_files;event="book/price_change"
            else:stype="phase1_truth";smin=source_phase_min;smax=prediction;version="phase1_safe_v1";reason=None if value is not None else "PHASE1_VALUE_UNDEFINED";files=[];event="event_ledger"
            provenance.append(FeatureProvenance(cid,name,stype,";".join(files) or None,event,smin,smax,prediction,cutoff,version,reason).to_dict())
        features.append(base)
    validate_provenance(provenance)
    return features,provenance,coverage
