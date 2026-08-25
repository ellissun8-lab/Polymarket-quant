"""Fold-local LogisticRegression ablations M0-M4."""
from __future__ import annotations
import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from std0_quant.audit.conditional_metrics import probability_metrics

STD0=["initial_direction_up","initial_qty","first_opp_qty","first_opp_fill_count","initial_to_opp_seconds","up_qty_before_first_opp","down_qty_before_first_opp","old_direction_qty","opp_ratio","inventory_proxy_abs","inventory_proxy_ratio","seconds_from_market_start","seconds_to_expiry","fraction_elapsed"]
BTC=["btc_distance_bps","btc_ret_1s","btc_ret_3s","btc_ret_5s","btc_ret_10s","btc_ret_30s","btc_rv_5s","btc_rv_10s","btc_rv_30s","btc_trade_count_1s","btc_trade_count_5s","btc_trade_count_30s","btc_volume_1s","btc_volume_5s","btc_volume_30s","btc_signed_flow_1s","btc_signed_flow_5s","btc_signed_flow_30s"]
BOOK=["opp_best_bid","opp_best_ask","opp_mid","opp_spread","initial_best_bid","initial_best_ask","initial_mid","initial_spread","opp_bid_depth_1","opp_ask_depth_1","opp_obi_1","opp_bid_depth_3","opp_ask_depth_3","opp_obi_3","pm_mid_change_1s","pm_mid_change_3s","pm_mid_change_5s","pm_mid_change_10s","pm_spread_change_5s","pm_obi_change_1s","pm_obi_change_5s","book_update_count_1s","book_update_count_5s"]
GROUPS={"M0":["online_regime_id"],"M1":["online_regime_id"]+STD0,"M2":["online_regime_id"]+STD0+BTC,"M3":["online_regime_id"]+STD0+BOOK,"M4":["online_regime_id"]+STD0+BTC+BOOK}

def run_models(rows,min_train_weeks=4,min_test_n=30):
    eligible=[r for r in rows if r.get("model_eligible")];weeks=sorted(set(r["iso_week"] for r in eligible));predictions=[];fold_metrics=[];coefficients=[]
    for k in range(min_train_weeks,len(weeks)):
        test_week=weeks[k];train=[r for r in eligible if r["iso_week"]<test_week];test=[r for r in eligible if r["iso_week"]==test_week]
        if len(test)<min_test_n or len(set(r["y30"] for r in train))<2:continue
        if max(r["prediction_ts_ms"] for r in train)>=min(r["prediction_ts_ms"] for r in test):raise AssertionError("walk-forward time violation")
        for model_name,names in GROUPS.items():
            Xtr=np.asarray([[r.get(n,np.nan) if r.get(n) is not None else np.nan for n in names] for r in train]);Xte=np.asarray([[r.get(n,np.nan) if r.get(n) is not None else np.nan for n in names] for r in test]);ytr=np.asarray([r["y30"] for r in train]);yte=np.asarray([r["y30"] for r in test])
            pipe=make_pipeline(SimpleImputer(strategy="median",add_indicator=True),StandardScaler(),LogisticRegression(max_iter=1000,random_state=0));pipe.fit(Xtr,ytr);prob=pipe.predict_proba(Xte)[:,1];metrics=probability_metrics(yte,prob,[test_week]*len(test));fold_metrics.append({"fold_id":k,"test_week":test_week,"model":model_name,"train_n":len(train),"test_n":len(test),**{x:metrics[x] for x in ("brier","logloss","ece","pooled_auc","macro_weekly_auc","weighted_weekly_auc")}})
            for row,p in zip(test,prob):predictions.append({"condition_id":row["condition_id"],"fold_id":k,"test_week":test_week,"model":model_name,"y30":row["y30"],"probability":float(p)})
            coef=pipe.named_steps["logisticregression"].coef_[0]
            for i,name in enumerate(names):coefficients.append({"fold_id":k,"model":model_name,"feature_name":name,"coefficient":float(coef[i])})
    return predictions,fold_metrics,coefficients

