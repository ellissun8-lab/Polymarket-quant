"""Past-only online regime assignment."""
from __future__ import annotations
from collections import deque
from typing import Mapping, Sequence


def assign_online_regimes(rows: Sequence[Mapping], min_history: int = 50, threshold: float = .15, window: int = 200) -> list[dict]:
    ordered = sorted(rows, key=lambda r: (int(r["first_opp_end_ms"]), str(r.get("condition_id"))))
    history: deque[int] = deque(maxlen=window)
    regime, prior_rate = 0, None
    out = []
    last_ts = None
    pos = 0
    while pos < len(ordered):
        prediction_time = int(ordered[pos]["first_opp_end_ms"])
        end = pos
        while end < len(ordered) and int(ordered[end]["first_opp_end_ms"]) == prediction_time: end += 1
        source_end = last_ts
        rate = sum(history) / len(history) if history else None
        if len(history) >= min_history and prior_rate is not None and abs(rate - prior_rate) >= threshold:
            regime += 1; prior_rate = rate
        elif len(history) >= min_history and prior_rate is None: prior_rate = rate
        for row in ordered[pos:end]:
            out.append({"condition_id": row.get("condition_id"), "online_regime_id": regime,
                        "online_regime_source_end_ms": source_end, "prediction_time_ms": prediction_time,
                        "historical_rate": rate})
        for row in ordered[pos:end]:
            if row.get("y30_horizon_eligible") and row.get("y30") is not None: history.append(int(row["y30"]))
        last_ts = prediction_time
        pos = end
    return out


def assert_point_in_time(assignments: Sequence[Mapping]) -> None:
    for a in assignments:
        source = a.get("online_regime_source_end_ms")
        if source is not None and int(source) >= int(a["prediction_time_ms"]):
            raise AssertionError("online regime used current/future observation")
