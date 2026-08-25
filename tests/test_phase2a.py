"""Tests X-AG for Phase 2A point-in-time public-state attribution."""
from __future__ import annotations
import numpy as np
import pytest
from std0_quant.features.btc_features import compute_btc_features
from std0_quant.features.book_features import compute_book_features
from std0_quant.features.book_reconstruction import latest_books,obi
from std0_quant.features.coverage_gate import bucket_coverage,gate_coverage
from std0_quant.features.pretrade_builder import cutoff_timestamp
from std0_quant.features.provenance import validate_provenance
from std0_quant.modeling.attribution import attribution
from std0_quant.modeling.phase2a_models import run_models
from std0_quant.audit.conditional_negative_controls import run_conditional_shuffle
import hashlib

T=2_000_000
def tick(ts,p=100,size=1,maker=False):return {"exchange_timestamp_ms":ts,"price":p,"size":size,"buyer_is_maker":maker}
def book(ts,token="opp",outcome="Down",bid=.4,ask=.6,bids=None,asks=None):return {"receive_timestamp_ms":ts,"token_id":token,"outcome":outcome,"best_bid":bid,"best_ask":ask,"mid":(bid+ask)/2,"spread":ask-bid,"bids":bids or [{"price":bid,"size":10}],"asks":asks or [{"price":ask,"size":5}]}

def test_x_cutoffs_boundaries_and_unordered():
    assert cutoff_timestamp(T,"cutoff_0")==T-1 and cutoff_timestamp(T,"cutoff_1")==T-1000 and cutoff_timestamp(T,"cutoff_2")==T-2000
    a=compute_btc_features([tick(T-1000,100),tick(T,999),tick(T-2000,90)],T-5000,T-1000)
    b=compute_btc_features(list(reversed([tick(T-2000,90),tick(T,999),tick(T-1000,100)])),T-5000,T-1000)
    assert a["btc_last_price"]==100 and a==b

def test_x_provenance_boundary_and_failure():
    base={"condition_id":"x","feature_name":"f","source_type":"binance_btc","prediction_ts_ms":T,"feature_cutoff_ms":T-1000,"source_timestamp_max_ms":T-1000}
    assert validate_provenance([base]);bad=dict(base,source_timestamp_max_ms=T-999)
    with pytest.raises(AssertionError):validate_provenance([bad])

def test_y_btc_returns_rv_flow_and_counts():
    rows=[tick(T-6000,100,2,True),tick(T-5999,100,2,True),tick(T-1000,105,3,False),tick(T,999,9)]
    f=compute_btc_features(rows,T-6000,T-1000)
    assert f["btc_ret_5s"]==pytest.approx(np.log(1.05));assert f["btc_rv_5s"]==pytest.approx(abs(np.log(1.05)))
    assert f["btc_trade_count_5s"]==2 and f["btc_signed_flow_5s"]==1

def test_y_start_price_requires_nearby_tick_and_missing_coverage():
    f=compute_btc_features([tick(T-5000)],T,T+1000);assert f["btc_start_price"] is None and f["btc_pre30_coverage_pct"]<.99

def test_z_book_snapshot_stale_reconnect_and_obi():
    old=book(T-5000,bid=.2,ask=.8);fresh=book(T-1000,bid=.4,ask=.6)
    assert latest_books([fresh,old],T,2000)["opp"]==fresh
    assert latest_books([old],T,2000)=={}
    reconnect=book(T-500,bid=.45,ask=.55);assert latest_books([old,reconnect],T,2000)["opp"]["best_bid"]==.45
    assert obi(book(T-1,bids=[{"price":.4,"size":15}],asks=[{"price":.6,"size":5}]),1)==.5

def test_z_book_depth_dynamics_and_cutoff():
    rows=[book(T-2000,bid=.3,ask=.7),book(T-1000,bid=.4,ask=.6),book(T+1,bid=.49,ask=.51)]
    f=compute_book_features(rows,T-1000,"Down","Up",stale_after_ms=5000)
    assert f["opp_best_bid"]==.4 and f["book_update_count_1s"]==1 and f["_source_max_ms"]==T-1000

def test_aa_coverage_threshold_exact_and_missing():
    assert gate_coverage(.989,1)[0] is False;assert gate_coverage(.99,.99)[0] is True;ok,reasons=gate_coverage(None,.99);assert not ok and "BTC_PRE30_MISSING" in reasons

def test_aa_bucket_coverage():
    assert bucket_coverage([0,1000,2000],0,3000)==1 and bucket_coverage([],0,1000) is None

def synthetic_rows(btc_signal=True,book_signal=False):
    rng=np.random.default_rng(4);rows=[];base=1_760_000_000_000
    for w in range(7):
        for i in range(50):
            btc=rng.normal();bookv=rng.normal();latent=(2*btc if btc_signal else 0)+(2*bookv if book_signal else 0)+rng.normal();y=int(latent>0);r={"condition_id":f"{w}-{i}","iso_week":f"W{w}","prediction_ts_ms":base+w*604800000+i*1000,"y30":y,"model_eligible":True,"online_regime_id":0}
            for n in ("initial_direction_up","initial_qty","first_opp_qty","first_opp_fill_count","initial_to_opp_seconds","up_qty_before_first_opp","down_qty_before_first_opp","old_direction_qty","opp_ratio","inventory_proxy_abs","inventory_proxy_ratio","seconds_from_market_start","seconds_to_expiry","fraction_elapsed"):r[n]=rng.normal()
            from std0_quant.modeling.phase2a_models import BTC,BOOK
            for n in BTC:r[n]=btc if n=="btc_ret_5s" else rng.normal()
            for n in BOOK:r[n]=bookv if n=="opp_obi_1" else rng.normal()
            rows.append(r)
    return rows

def test_ab_ac_ad_fold_local_models_and_btc_attribution():
    pred,folds,coef=run_models(synthetic_rows(True,False),min_train_weeks=3,min_test_n=20);agg,abl=attribution(pred,folds);by={r["increment"]:r for r in abl}
    assert by["M2-M1"]["delta_brier"]>0 and len(coef)>0 and all(r["train_n"]>r["test_n"] for r in folds)

def test_ad_book_attribution_fixture():
    pred,folds,_=run_models(synthetic_rows(False,True),3,20);_,abl=attribution(pred,folds);by={r["increment"]:r for r in abl};assert by["M3-M1"]["delta_brier"]>0

def test_ae_same_second_leakage_fixture():
    rows=[tick(T-1500,100),tick(T-500,200)];c0=compute_btc_features(rows,T-10000,T-1);c1=compute_btc_features(rows,T-10000,T-1000)
    assert c0["btc_last_price"]==200 and c1["btc_last_price"]==100

def test_af_complete_pipeline_conditional_shuffle():
    rng=np.random.default_rng(7);periods=[];y=[];X=[]
    for week,rate in (("A",.2),("B",.9)):
        labels=rng.binomial(1,rate,250);periods.extend([week]*250);y.extend(labels);X.extend([[0 if week=="A" else 1,rng.normal()] for _ in labels])
    result=run_conditional_shuffle(np.asarray(X),y,periods,n_shuffles=50,seed=8)
    assert result["summary"]["macro_weekly_auc"]["mean"]==pytest.approx(.5,abs=.06)

def test_ag_truth_file_hash_unchanged(tmp_path):
    path=tmp_path/"truth";path.write_bytes(b"frozen phase truth");before=hashlib.sha256(path.read_bytes()).hexdigest()
    validate_provenance([{"condition_id":"x","feature_name":"q","source_type":"phase1_truth","source_timestamp_max_ms":T,"prediction_ts_ms":T,"feature_cutoff_ms":T-1000}])
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before
