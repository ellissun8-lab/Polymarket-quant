"""Phase-1-safe std0 and contract-state features."""
from __future__ import annotations

STD0_FEATURES=("initial_direction_up","initial_qty","first_opp_qty","first_opp_fill_count","initial_to_opp_seconds","up_qty_before_first_opp","down_qty_before_first_opp","old_direction_qty","opp_ratio","inventory_proxy_abs","inventory_proxy_ratio")

def build_std0_state(row):
    initial=float(row["initial_qty"]);opp=float(row["first_opp_qty"]);up=float(row.get("up_qty_before_first_opp") or 0);down=float(row.get("down_qty_before_first_opp") or 0);net=up-down
    t0=int(row["first_opp_end_ms"]);start=int(row["market_start_ms"]);end=int(row["market_end_ms"])
    return {"initial_direction_up":int(row.get("initial_direction")=="Up"),"initial_qty":initial,"first_opp_qty":opp,"first_opp_fill_count":int(row["first_opp_fill_count"]),"initial_to_opp_seconds":(t0-int(row["initial_first_timestamp_ms"]))/1000,"up_qty_before_first_opp":up,"down_qty_before_first_opp":down,"old_direction_qty":float(row["old_direction_qty"]),"opp_ratio":opp/initial if initial else None,"inventory_proxy_abs":net,"inventory_proxy_ratio":net/(up+down) if up+down else None,"seconds_from_market_start":(t0-start)/1000,"seconds_to_expiry":(end-t0)/1000,"fraction_elapsed":(t0-start)/(end-start),"direction_sign":1 if row.get("first_opp_direction")=="Up" else -1}

