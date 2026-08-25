"""Phase 2B exploratory microstructure primitives.

Discovery is deliberately separated from the frozen Phase 2A confirmation
protocol.  Nothing here places orders, estimates fills, or changes the cohort.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

RESEARCH_SPEC_VERSION = "phase2b_research_v1"
PRIMARY_COLLECTOR_VERSION = "phase2a_prospective_v4"
COHORT_VERSION = "prospective_v4"
GRIDS_MS = (100, 250, 500, 1000)
RESPONSE_HORIZONS_MS = (100, 250, 500, 1000, 2000, 5000)
SHOCK_BUCKETS_BP = ((0, 1, "0-1bp"), (1, 2, "1-2bp"), (2, 5, "2-5bp"),
                    (5, 10, "5-10bp"), (10, float("inf"), ">10bp"))


def v4_only(rows: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    rows = list(rows)
    kept = [row for row in rows if row.get("collector_version") == PRIMARY_COLLECTOR_VERSION]
    return kept, len(rows) - len(kept)


def valid_book_row(row: dict[str, Any]) -> bool:
    return (row.get("collector_version") == PRIMARY_COLLECTOR_VERSION
            and row.get("book_state_valid") is True
            and row.get("book_state_status") == "VALID"
            and row.get("mid") is not None
            and row.get("best_bid") is not None
            and row.get("best_ask") is not None)


def up_equivalent(side: str, outcome: str, price: float) -> tuple[int, float]:
    side, outcome, price = side.upper(), outcome.title(), float(price)
    if side not in {"BUY", "SELL"} or outcome not in {"Up", "Down"}:
        raise ValueError("side/outcome outside frozen UP-equivalent mapping")
    direction = 1 if (side, outcome) in {("BUY", "Up"), ("SELL", "Down")} else -1
    return direction, price if outcome == "Up" else 1.0 - price


def normalized_book_state(row: dict[str, Any]) -> dict[str, Any]:
    if not valid_book_row(row):
        raise ValueError("book row is not a valid bounded v4 state")
    outcome = str(row["outcome"])
    bid, ask, mid = float(row["best_bid"]), float(row["best_ask"]), float(row["mid"])
    bids, asks = row.get("bids") or [], row.get("asks") or []
    bid_depth = sum(float(level.get("size", 0)) for level in bids)
    ask_depth = sum(float(level.get("size", 0)) for level in asks)
    if outcome == "Down":
        up_bid, up_ask, up_mid = 1.0-ask, 1.0-bid, 1.0-mid
        up_bid_depth, up_ask_depth = ask_depth, bid_depth
    else:
        up_bid, up_ask, up_mid = bid, ask, mid
        up_bid_depth, up_ask_depth = bid_depth, ask_depth
    depth = up_bid_depth + up_ask_depth
    return {"pm_best_bid": up_bid, "pm_best_ask": up_ask, "pm_mid": up_mid,
            "pm_spread": up_ask-up_bid, "pm_bid_depth_top3": up_bid_depth,
            "pm_ask_depth_top3": up_ask_depth,
            "pm_obi_top3": (up_bid_depth-up_ask_depth)/depth if depth else None}


def assert_timeline_order(rows: Sequence[dict[str, Any]]) -> None:
    keys = [(int(row["event_timestamp_ms"]), int(row.get("receive_timestamp_ms") or 0),
             str(row.get("source"))) for row in rows]
    if keys != sorted(keys):
        raise ValueError("market timeline is not deterministically ordered")


def build_grid(btc: pd.DataFrame, book: pd.DataFrame, start_ms: int, end_ms: int,
               grid_ms: int, book_stale_ms: int = 5000) -> pd.DataFrame:
    grid = pd.DataFrame({"timestamp_ms": np.arange(start_ms, end_ms, grid_ms, dtype="int64")})
    btc = btc.sort_values("event_timestamp_ms")[["event_timestamp_ms", "btc_price"]]
    book_cols = ["event_timestamp_ms", "pm_mid", "pm_best_bid", "pm_best_ask", "pm_spread",
                 "pm_bid_depth_top3", "pm_ask_depth_top3", "pm_obi_top3"]
    book = book.sort_values("event_timestamp_ms")[book_cols]
    out = pd.merge_asof(grid, btc, left_on="timestamp_ms", right_on="event_timestamp_ms",
                        direction="backward").rename(columns={"event_timestamp_ms":"btc_source_timestamp_ms"})
    out = pd.merge_asof(out, book, left_on="timestamp_ms", right_on="event_timestamp_ms",
                        direction="backward").rename(columns={"event_timestamp_ms":"book_source_timestamp_ms"})
    out["book_age_ms"] = out["timestamp_ms"] - out["book_source_timestamp_ms"]
    invalid = out["book_age_ms"].isna() | (out["book_age_ms"] > book_stale_ms)
    for name in book_cols[1:]: out.loc[invalid, name] = np.nan
    out["book_valid"] = ~invalid
    out["grid_ms"] = grid_ms
    return out


def shock_bucket(abs_bp: float) -> str:
    for lo, hi, label in SHOCK_BUCKETS_BP:
        if lo <= abs_bp < hi: return label
    raise AssertionError("unreachable")


def add_market_features(grid: pd.DataFrame, start_ms: int, end_ms: int) -> pd.DataFrame:
    out = grid.copy()
    for seconds in (1, 3, 5):
        periods = max(1, int(seconds * 1000 / int(out["grid_ms"].iloc[0])))
        out[f"btc_ret_{seconds}s_bp"] = out["btc_price"].pct_change(periods, fill_method=None) * 10_000
    out["btc_shock_bucket"] = out["btc_ret_1s_bp"].abs().map(lambda x: shock_bucket(x) if pd.notna(x) else None)
    out["seconds_from_market_start"] = (out["timestamp_ms"]-start_ms)/1000
    out["seconds_to_expiry"] = (end_ms-out["timestamp_ms"])/1000
    out["lifecycle_bucket"] = pd.cut(out["seconds_from_market_start"], [-1,60,120,180,240,300],
                                      labels=["0-60s","60-120s","120-180s","180-240s","240-300s"])
    out["pm_mid_bucket"] = pd.cut(out["pm_mid"], [0,.2,.4,.6,.8,1], include_lowest=True).astype(str)
    out["btc_vol_5s_bp"] = out["btc_ret_1s_bp"].rolling(max(1, int(5000/int(out["grid_ms"].iloc[0])))).std()
    return out


def event_response_curves(shocks: pd.DataFrame, response_grid: pd.DataFrame,
                          horizons_ms: Sequence[int] = RESPONSE_HORIZONS_MS) -> list[dict[str, Any]]:
    valid = shocks.dropna(subset=["btc_ret_1s_bp","pm_mid"])
    valid = valid[valid["btc_ret_1s_bp"].abs() >= 1.0]
    times = response_grid["timestamp_ms"].to_numpy(); mids = response_grid["pm_mid"].to_numpy()
    output = []
    for bucket, group in valid.groupby("btc_shock_bucket", observed=True):
        for horizon in horizons_ms:
            values=[]
            for row in group.itertuples():
                idx=np.searchsorted(times, int(row.timestamp_ms)+horizon)
                if idx<len(times) and np.isfinite(mids[idx]) and np.isfinite(row.pm_mid):
                    values.append(np.sign(row.btc_ret_1s_bp)*(mids[idx]-row.pm_mid))
            output.append({"shock_bucket":str(bucket),"horizon_ms":int(horizon),"n":len(values),
                           "signed_mean_response":float(np.mean(values)) if values else None,
                           "signed_median_response":float(np.median(values)) if values else None})
    return output


def cross_correlations(grid: pd.DataFrame, max_lag_ms: int = 2000) -> list[dict[str, Any]]:
    step=int(grid["grid_ms"].iloc[0]);btc=grid["btc_price"].pct_change(fill_method=None);pm=grid["pm_mid"].diff()
    output=[]
    for lag in range(-max_lag_ms,max_lag_ms+1,step):
        shifted=pm.shift(-lag//step);pair=pd.concat([btc,shifted],axis=1).dropna()
        corr=float(pair.iloc[:,0].corr(pair.iloc[:,1])) if len(pair)>2 else None
        output.append({"lag_ms":lag,"n":len(pair),"correlation":corr})
    return output


def lagged_regressions(grid: pd.DataFrame) -> list[dict[str, Any]]:
    step=int(grid["grid_ms"].iloc[0]);output=[]
    for lag in (0,100,250,500,1000,2000):
        lag_steps=int(round(lag/step));x=grid["btc_price"].pct_change(max(1,lag_steps) if lag else 1,fill_method=None)
        for horizon in (250,500,1000,2000,5000):
            h_steps=max(1,int(round(horizon/step)));y=grid["pm_mid"].shift(-h_steps)-grid["pm_mid"]
            pair=pd.concat([x,y],axis=1).dropna();n=len(pair)
            if n<3 or float(pair.iloc[:,0].var())==0:alpha=beta=r2=None
            else:
                xv=pair.iloc[:,0].to_numpy();yv=pair.iloc[:,1].to_numpy();design=np.column_stack([np.ones(n),xv]);coef=np.linalg.lstsq(design,yv,rcond=None)[0];pred=design@coef;den=np.sum((yv-yv.mean())**2)
                alpha,beta=float(coef[0]),float(coef[1]);r2=float(1-np.sum((yv-pred)**2)/den) if den else None
            output.append({"lag_ms":lag,"horizon_ms":horizon,"n":n,"alpha":alpha,"beta":beta,"r2":r2})
    return output


def conservative_fill_window(timestamp_ms: int) -> dict[str, int]:
    start=(int(timestamp_ms)//1000)*1000
    return {"fill_second_start_ms":start,"fill_second_end_ms":start+999,
            "pre_context_cutoff_ms":start-1,"post_markout_anchor_ms":start+999}


def conservative_markout(side: str, outcome: str, fill_price: float,
                         future_up_mid: float | None) -> float | None:
    if future_up_mid is None:return None
    direction,up_price=up_equivalent(side,outcome,fill_price)
    return direction*(float(future_up_mid)-up_price)


def matched_controls(candidates: Sequence[dict[str, Any]], event: dict[str, Any], n: int = 5) -> list[dict[str, Any]]:
    cutoff=int(event["pre_context_cutoff_ms"]);eligible=[row for row in candidates if row.get("condition_id")==event.get("condition_id") and int(row["timestamp_ms"])<=cutoff]
    def distance(row):
        return sum(abs(float(row.get(k,0))-float(event.get(k,0))) for k in ("seconds_to_expiry","pm_mid","pm_spread","btc_vol_5s_bp"))
    return sorted(eligible,key=distance)[:n]


def _fill_key(row: dict[str, Any]) -> tuple[Any,...]:
    return tuple(row.get(k) for k in ("transactionHash","asset","side","size","price","timestamp","outcomeIndex"))


def maker_multiset_inference(all_fills: Sequence[dict[str, Any]], taker_fills: Sequence[dict[str, Any]]) -> list[str]:
    remaining=Counter(_fill_key(row) for row in taker_fills);labels=[]
    for row in all_fills:
        key=_fill_key(row)
        if remaining[key]>0:labels.append("TAKER_CONFIRMED");remaining[key]-=1
        else:labels.append("MAKER_INFERRED")
    return labels


def pair_economics(fills: Sequence[dict[str, Any]]) -> dict[str, Any]:
    def side(outcome):
        group=[r for r in fills if str(r.get("outcome")).title()==outcome and str(r.get("side")).upper()=="BUY"]
        qty=sum(float(r["size"]) for r in group);vwap=sum(float(r["size"])*float(r["price"]) for r in group)/qty if qty else None
        return qty,vwap
    up_qty,up_vwap=side("Up");down_qty,down_vwap=side("Down");pair_qty=min(up_qty,down_qty);cost=up_vwap+down_vwap if up_vwap is not None and down_vwap is not None else None
    return {"q_up":up_qty,"q_down":down_qty,"pairable_qty":pair_qty,"vwap_up":up_vwap,"vwap_down":down_vwap,"pair_cost":cost,"gross_pair_edge":1-cost if cost is not None else None,"fee_adjusted_pair_edge":None}


def inventory_proxy(fills: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    up=down=0.0;output=[]
    for row in sorted(fills,key=lambda r:int(r["timestamp_ms"])):
        signed=float(row["size"])*(1 if str(row.get("side")).upper()=="BUY" else -1)
        if str(row.get("outcome")).title()=="Up":up+=signed
        else:down+=signed
        output.append({"timestamp_ms":int(row["timestamp_ms"]),"q_up":up,"q_down":down,
                       "inventory_proxy":up-down,"inventory_abs":abs(up-down),
                       "pairable_qty":min(up,down),"directional_qty":abs(up-down)})
    return output
