"""PB1-PB14 exploratory Phase 2B governance and mechanics tests."""
from __future__ import annotations
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from std0_quant.research.phase2b import (
    assert_timeline_order,conservative_fill_window,conservative_markout,
    cross_correlations,inventory_proxy,maker_multiset_inference,matched_controls,
    normalized_book_state,pair_economics,up_equivalent,v4_only,valid_book_row,
)


def test_pb1_prospective_v4_only_gating():
    kept,excluded=v4_only([{"collector_version":"phase2a_prospective_v4"},{"collector_version":"phase2a_prospective_v3"}]);assert len(kept)==1 and excluded==1


def test_pb2_market_timeline_ordering():
    assert_timeline_order([{"event_timestamp_ms":1,"receive_timestamp_ms":2,"source":"BTC"},{"event_timestamp_ms":2,"receive_timestamp_ms":2,"source":"PM"}])
    with pytest.raises(ValueError):assert_timeline_order([{"event_timestamp_ms":2,"receive_timestamp_ms":2,"source":"PM"},{"event_timestamp_ms":1,"receive_timestamp_ms":2,"source":"BTC"}])


def book(valid=True,status="VALID"):
    return {"collector_version":"phase2a_prospective_v4","book_state_valid":valid,"book_state_status":status,"mid":.4,"best_bid":.39,"best_ask":.41,"outcome":"Down","bids":[{"size":2}],"asks":[{"size":1}]}


def test_pb3_valid_book_only():
    assert valid_book_row(book()) and not valid_book_row(book(False,"STALE"));state=normalized_book_state(book());assert state["pm_mid"]==pytest.approx(.6) and state["pm_best_bid"]==pytest.approx(.59)


def lag_grid(lag_steps):
    rng=np.random.default_rng(4);ret=rng.normal(0,1e-4,200);btc=100*np.cumprod(1+ret);pm_ret=np.r_[np.zeros(lag_steps),ret[:-lag_steps]] if lag_steps else ret;pm=.5+np.cumsum(pm_ret);return pd.DataFrame({"grid_ms":250,"btc_price":btc,"pm_mid":pm})


def test_pb4_btc_to_pm_synthetic_known_lag():
    rows=cross_correlations(lag_grid(2),1000);peak=max(rows,key=lambda r:abs(r["correlation"] or 0));assert peak["lag_ms"]==500


def test_pb5_no_lag_synthetic():
    rows=cross_correlations(lag_grid(0),1000);peak=max(rows,key=lambda r:abs(r["correlation"] or 0));assert peak["lag_ms"]==0


def test_pb6_std0_same_second_ambiguity():
    window=conservative_fill_window(12_345);assert window=={"fill_second_start_ms":12000,"fill_second_end_ms":12999,"pre_context_cutoff_ms":11999,"post_markout_anchor_ms":12999}


def test_pb7_conservative_markout():
    assert conservative_markout("BUY","Down",.4,.55)==pytest.approx(.05);assert conservative_markout("BUY","Up",.4,None) is None


def test_pb8_up_equivalent_normalization():
    assert up_equivalent("BUY","Up",.3)==(1,.3);assert up_equivalent("SELL","Up",.3)==(-1,.3);assert up_equivalent("BUY","Down",.3)==(-1,.7);assert up_equivalent("SELL","Down",.3)==(1,.7)


def test_pb9_matched_controls_no_future_leakage():
    event={"condition_id":"c","pre_context_cutoff_ms":100,"seconds_to_expiry":1,"pm_mid":.5,"pm_spread":.01,"btc_vol_5s_bp":1};rows=[{"condition_id":"c","timestamp_ms":99,**{k:event[k] for k in ("seconds_to_expiry","pm_mid","pm_spread","btc_vol_5s_bp")}},{"condition_id":"c","timestamp_ms":101,**{k:event[k] for k in ("seconds_to_expiry","pm_mid","pm_spread","btc_vol_5s_bp")}}];assert [r["timestamp_ms"] for r in matched_controls(rows,event)]==[99]


def test_pb10_maker_multiset_duplicate_handling():
    row={"transactionHash":"x","asset":"a","side":"BUY","size":"1","price":".5","timestamp":1,"outcomeIndex":0};assert maker_multiset_inference([row,row],[row])==["TAKER_CONFIRMED","MAKER_INFERRED"]


def test_pb11_pair_economics():
    rows=[{"side":"BUY","outcome":"Up","size":2,"price":.4},{"side":"BUY","outcome":"Down","size":1,"price":.5}];out=pair_economics(rows);assert out["pairable_qty"]==1 and out["pair_cost"]==pytest.approx(.9) and out["gross_pair_edge"]==pytest.approx(.1) and out["fee_adjusted_pair_edge"] is None


def test_pb12_inventory_proxy():
    rows=[{"timestamp_ms":1,"side":"BUY","outcome":"Up","size":3},{"timestamp_ms":2,"side":"BUY","outcome":"Down","size":1}];out=inventory_proxy(rows);assert out[-1]["inventory_proxy"]==2 and out[-1]["pairable_qty"]==1


def test_pb13_analysis_failure_does_not_mutate_raw(tmp_path):
    raw=tmp_path/"raw.ndjson";raw.write_text("immutable\n");before=hashlib.sha256(raw.read_bytes()).hexdigest()
    with pytest.raises(ValueError):normalized_book_state(book(False,"DESYNCED"))
    assert hashlib.sha256(raw.read_bytes()).hexdigest()==before


def test_pb14_phase2a_frozen_spec_unchanged():
    path=Path(__file__).parents[1]/"config/settings.yaml";before=hashlib.sha256(path.read_bytes()).hexdigest();up_equivalent("BUY","Up",.5);assert hashlib.sha256(path.read_bytes()).hexdigest()==before
