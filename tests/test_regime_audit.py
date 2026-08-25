"""Tests P-W: Phase 1.6 regime and conditional predictability audit."""
from __future__ import annotations
import numpy as np
import pytest

from std0_quant.audit.changepoints import detect_change_points
from std0_quant.audit.conditional_metrics import conditional_auc
from std0_quant.audit.conditional_negative_controls import shuffle_within_period
from std0_quant.audit.feature_drift import run_feature_drift
from std0_quant.audit.matched_placebo import calendar_matched
from std0_quant.audit.online_regime import assign_online_regimes, assert_point_in_time
from std0_quant.audit.regime_surface import build_regime_surface, wilson_interval
from std0_quant.audit.walk_forward import run_walk_forward

MONDAY=1767571200000; WEEK=7*86400000
def row(i,week=0,y=1,eligible=True):
    start=MONDAY+week*WEEK+i*1000
    return {"condition_id":f"c{week}-{i}","clean_flag":True,"market_start_ms":start,"market_end_ms":start+300000,"initial_first_timestamp_ms":start+10000,"initial_qty":10.,"initial_direction":"Up","first_opp_start_ms":start+20000,"first_opp_end_ms":start+20000,"first_opp_qty":5.,"first_opp_fill_count":1,"first_opp_vwap":.5,"up_qty_before_first_opp":10.,"down_qty_before_first_opp":5.,"old_direction_qty":10.,"y30":y if eligible else None,"y30_horizon_eligible":eligible,"n_buy_fills":3,"n_sell_fills":1}

def test_p_surface_censoring_wilson_and_low_n():
    s=build_regime_surface([row(0,y=1),row(1,y=0),row(2,eligible=False)],"weekly",min_n=10)[0]
    assert (s["y30_observable_count"],s["y30_positive"],s["y30_negative"],s["y30_censored"])==(2,1,1,1)
    assert s["y30_rate"]==.5 and s["low_n"]
    lo,hi=wilson_interval(1,2);assert lo<.5<hi
    assert wilson_interval(0,0)==(None,None)

def test_q_known_break_supported_and_stable_not_supported():
    surface=[]
    for i,v in enumerate([.2]*6+[.8]*6): surface.append({"period_key":f"W{i:02d}","y30_rate":v,"y30_observable_count":100})
    found=detect_change_points(surface,min_segment=3)
    assert any(x["status"]=="SUPPORTED_BREAK" and x["break_timestamp"] in {"W05","W06","W07"} for x in found)
    stable=[dict(x,y30_rate=.5) for x in surface]
    assert not any(x["status"]=="SUPPORTED_BREAK" for x in detect_change_points(stable,min_segment=3))

def test_r_regime_confounding_regression():
    rng=np.random.default_rng(9);periods=["A"]*1000+["B"]*1000;y=np.r_[rng.binomial(1,.2,1000),rng.binomial(1,.9,1000)];score=np.r_[np.zeros(1000),np.ones(1000)]
    m=conditional_auc(y,score,periods)
    assert m["pooled_auc"]>.8
    assert m["macro_auc"]==pytest.approx(.5) and m["weighted_auc"]==pytest.approx(.5)

def test_r_single_class_period_is_kept():
    m=conditional_auc([0,0,0,1],[.1,.2,.3,.9],["A","A","A","B"])
    assert len(m["period_details"])==2 and all(x["status"]=="NOT_EVALUABLE" for x in m["period_details"])

def test_s_shuffle_preserves_period_counts():
    y=np.array([0,1,1,0,0,1]);p=["A"]*3+["B"]*3;out=shuffle_within_period(y,p,np.random.default_rng(1))
    assert out[:3].sum()==y[:3].sum() and out[3:].sum()==y[3:].sum()

def test_u_online_regime_is_past_only_and_future_invariant():
    base=[row(i,y=i%2) for i in range(20)];a=assign_online_regimes(base,min_history=5)
    extended=assign_online_regimes(base+[row(i+100,week=1,y=1) for i in range(50)],min_history=5)
    assert_point_in_time(a);assert [(x["condition_id"],x["online_regime_id"]) for x in a]==[(x["condition_id"],x["online_regime_id"]) for x in extended[:len(a)]]

def test_v_calendar_match_common_only_and_empty():
    ref=[row(i,0,y=i%2) for i in range(10)]+[row(i,1,y=1) for i in range(10)]
    other=[row(i,1,y=0) for i in range(10)]
    res=calendar_matched(ref,other,"X",min_week_n=5);assert res["n_common_weeks"]==1 and res["calendar_matched_delta_pp"]==100
    assert calendar_matched(ref,[],"X",5)["status"]=="NOT_COMPARABLE"

def test_t_walk_forward_strict_and_reports_stability():
    rng=np.random.default_rng(2);weeks=[];ts=[];y=[];X=[]
    for w in range(6):
        for i in range(40):
            weeks.append(f"W{w}");ts.append(MONDAY+w*WEEK+i*1000);val=rng.normal();X.append([val]*8);y.append(int(val+rng.normal()>.2))
    result=run_walk_forward(np.asarray(X),np.asarray(y),np.asarray(ts),weeks,min_train_weeks=3,min_test_n=30)
    assert result["n_valid_folds"]==3
    assert all(f["train_end"]<f["test_start"] for f in result["folds"])

def test_w_feature_drift_no_inf_nan_silence():
    X=np.ones((6,8));X[4,0]=np.nan;d=run_feature_drift(X,["A"]*3+["B"]*3)
    assert all(x["psi_vs_first_week"] is None or np.isfinite(x["psi_vs_first_week"]) for x in d)
