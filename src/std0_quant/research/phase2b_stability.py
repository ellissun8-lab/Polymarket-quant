"""Phase 2B per-market lead-lag stability primitives (research spec v2).

Extension of the exploratory bootstrap in ``research/phase2b.py``.  Every
estimator here runs PER MARKET (never pooled-only), records both OVERLAPPING
and NON_OVERLAPPING_1S shock-anchor sets, compares EXCHANGE_TIME against
RECEIVE_TIME clock bases, and never touches the recorder, the cohort, any
raw file, or any frozen Phase 2A definition.  No trading, no causal claims.

Frozen in this spec version (do not retune after seeing results): direction
tolerance 100ms, refractory 1000ms, lag grid +/-2000ms at 250ms, METHOD_C lag
grid, response horizons, economic horizons, shock/lifecycle buckets, bootstrap
settings, dependence-sensitivity thresholds, B1/B2 milestone grids.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

from std0_quant.audit.prospective import (
    COHORT_VERSION,
    PRIMARY_COLLECTOR_VERSION,
    atomic_json,
    fully_covered_observation,
)
from std0_quant.research.phase2b import (
    RESPONSE_HORIZONS_MS,
    conservative_fill_window,
    cross_correlations,
    lagged_regressions,
)

RESEARCH_SPEC_VERSION_V2 = "phase2b_research_v2"
DIRECTION_TOLERANCE_MS = 100
REFRACTORY_MS = 1000
MAX_LAG_MS = 2000
METHOD_C_LAGS_MS = (0, 100, 250, 500, 1000, 2000)
METHOD_C_HORIZONS_MS = (250, 500, 1000, 2000, 5000)
ECONOMIC_HORIZONS_MS = (100, 250, 500, 1000)
SHOCK_MIN_ABS_BP = 1.0
DEPENDENCE_MIN_N = 10
DEPENDENCE_REL_DIFF = 0.5
B1_MILESTONES = (3, 10, 20, 50, 100)
B2_MILESTONES = (1, 10, 50, 100, 250, 500)
BOOTSTRAP_MIN_MARKETS = 10
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 20260824
TEST_BASELINE_COUNT = 330
ALLOWED_OVERALL_DECISIONS = ("EXPLORATORY_PIPELINE_READY", "EXPLORATORY_EVIDENCE_ACCUMULATING",
                             "MICROSTRUCTURE_DATA_QUALITY_FAILURE")
FORBIDDEN_DECISION_TOKENS = ("ALPHA_PROVEN", "STRATEGY_PROVEN", "READY_TO_TRADE")
ALLOWED_B1_STATES = ("TINY_SAMPLE", "EARLY_REPLICATION", "EXPLORATORY_REPLICATION",
                     "MULTI_MARKET_EVIDENCE", "INTERMEDIATE_STABILITY",
                     "BROAD_EXPLORATORY_EVIDENCE", "MICROSTRUCTURE_DATA_QUALITY_FAILURE")
ALLOWED_B2_STATES = ("INSUFFICIENT_STD0_EVENTS", "DESCRIPTIVE_ONLY_TINY_N",
                     "EXPLORATORY_EVIDENCE_ACCUMULATING", "MECHANISM_CANDIDATE_IDENTIFIED")


def b1_maturity_state(n_markets: int) -> str:
    if n_markets < 3:
        return "TINY_SAMPLE"
    if n_markets < 10:
        return "EARLY_REPLICATION"
    if n_markets < 20:
        return "EXPLORATORY_REPLICATION"
    if n_markets < 50:
        return "MULTI_MARKET_EVIDENCE"
    if n_markets < 100:
        return "INTERMEDIATE_STABILITY"
    return "BROAD_EXPLORATORY_EVIDENCE"


def b2_observation_state(n_observations: int) -> str:
    """MECHANISM_CANDIDATE_IDENTIFIED is never assigned by count alone."""
    if n_observations <= 0:
        return "INSUFFICIENT_STD0_EVENTS"
    if n_observations < 10:
        return "DESCRIPTIVE_ONLY_TINY_N"
    return "EXPLORATORY_EVIDENCE_ACCUMULATING"


def classify_direction(lag_ms: int | float | None,
                       tolerance_ms: int = DIRECTION_TOLERANCE_MS) -> str | None:
    if lag_ms is None:
        return None
    lag = float(lag_ms)
    if abs(lag) <= tolerance_ms:
        return "SYNCHRONOUS"
    return "BTC_LEAD" if lag > 0 else "PM_LEAD"


def next_b1_milestone(n_markets: int) -> int | None:
    return next((m for m in B1_MILESTONES if n_markets < m), None)


def next_b2_milestone(n_observations: int) -> int | None:
    return next((m for m in B2_MILESTONES if n_observations < m), None)


def non_overlapping_anchors(timestamps_ms: Iterable[int],
                            refractory_ms: int = REFRACTORY_MS) -> list[int]:
    """Accept an anchor, then reject every anchor within the refractory window."""
    accepted: list[int] = []
    for ts in sorted(int(t) for t in timestamps_ms):
        if not accepted or ts - accepted[-1] >= refractory_ms:
            accepted.append(ts)
    return accepted


def latency_summary(values_ms: Sequence[int | float]) -> dict[str, Any]:
    arr = np.asarray([float(v) for v in values_ms if v is not None], dtype=float)
    if arr.size == 0:
        return {"n": 0, "p50_ms": None, "p90_ms": None, "p95_ms": None,
                "p99_ms": None, "max_ms": None}
    return {"n": int(arr.size),
            "p50_ms": float(np.percentile(arr, 50)),
            "p90_ms": float(np.percentile(arr, 90)),
            "p95_ms": float(np.percentile(arr, 95)),
            "p99_ms": float(np.percentile(arr, 99)),
            "max_ms": float(arr.max())}


def is_full_lifecycle_v4_market(row: dict[str, Any]) -> bool:
    return (row.get("collector_version") == PRIMARY_COLLECTOR_VERSION
            and row.get("lifecycle") == "FULL_LIFECYCLE_MARKET"
            and float(row.get("btc_coverage_pct") or 0) >= 0.99
            and float(row.get("book_coverage_pct") or 0) >= 0.99)


def b2_eligible_observations(rows: Iterable[dict[str, Any]],
                             cohort_version: str = COHORT_VERSION) -> list[dict[str, Any]]:
    """O2 -> N001 activation gate; observation_id stays the primary key."""
    return [row for row in rows
            if row.get("cohort_version", cohort_version) == cohort_version
            and row.get("collector_version") == PRIMARY_COLLECTOR_VERSION
            and fully_covered_observation(row)
            and row.get("pit_pass") is not False]


def markout_horizon_timestamp(fill_timestamp_ms: int, horizon_s: int) -> int:
    """Post-fill anchor is fill_second_end; never inside the fill second."""
    return conservative_fill_window(int(fill_timestamp_ms))["post_markout_anchor_ms"] + int(horizon_s) * 1000


# ---------------------------------------------------------------- methods

def method_a_peak(grid: pd.DataFrame, max_lag_ms: int = MAX_LAG_MS) -> dict[str, Any]:
    rows = [r for r in cross_correlations(grid, max_lag_ms) if r["correlation"] is not None]
    if not rows:
        return {"lag_ms": None, "correlation": None, "n": 0}
    peak = min(rows, key=lambda r: (-abs(r["correlation"]), abs(r["lag_ms"]), r["lag_ms"]))
    return {"lag_ms": int(peak["lag_ms"]), "correlation": float(peak["correlation"]),
            "n": int(peak["n"])}


def shock_anchor_rows(grid: pd.DataFrame, min_abs_bp: float = SHOCK_MIN_ABS_BP) -> pd.DataFrame:
    valid = grid.dropna(subset=["btc_ret_1s_bp", "pm_mid"])
    return valid[valid["btc_ret_1s_bp"].abs() >= min_abs_bp]


def collect_shock_response_values(anchors: pd.DataFrame, fine: pd.DataFrame,
                                  horizons_ms: Sequence[int] = RESPONSE_HORIZONS_MS
                                  ) -> dict[int, list[float]]:
    times = fine["timestamp_ms"].to_numpy()
    mids = fine["pm_mid"].to_numpy(dtype=float)
    values: dict[int, list[float]] = {int(h): [] for h in horizons_ms}
    for row in anchors.itertuples():
        base = float(row.pm_mid)
        sign = 1.0 if float(row.btc_ret_1s_bp) > 0 else -1.0
        for horizon in horizons_ms:
            idx = int(np.searchsorted(times, int(row.timestamp_ms) + int(horizon)))
            if idx < len(times) and np.isfinite(mids[idx]) and np.isfinite(base):
                values[int(horizon)].append(sign * (float(mids[idx]) - base))
    return values


def response_stats_from_values(values: Sequence[float]) -> dict[str, Any]:
    arr = np.asarray([float(v) for v in values], dtype=float)
    if arr.size == 0:
        return {"n": 0, "signed_mean": None, "signed_median": None, "abs_median": None,
                "p25": None, "p75": None, "p90": None}
    return {"n": int(arr.size),
            "signed_mean": float(arr.mean()),
            "signed_median": float(np.median(arr)),
            "abs_median": float(np.median(np.abs(arr))),
            "p25": float(np.percentile(arr, 25)),
            "p75": float(np.percentile(arr, 75)),
            "p90": float(np.percentile(arr, 90))}


def combine_value_maps(maps: Sequence[dict[int, list[float]]]) -> dict[int, list[float]]:
    combined: dict[int, list[float]] = {}
    for mapping in maps:
        for horizon, values in mapping.items():
            combined.setdefault(int(horizon), []).extend(values)
    return combined


def method_b_peak(response_values: dict[int, list[float]]) -> dict[str, Any]:
    stats = {int(h): response_stats_from_values(v) for h, v in response_values.items()}
    usable = [(h, s) for h, s in stats.items() if s["n"] > 0 and s["signed_mean"] is not None]
    if not usable:
        return {"lag_ms": None, "signed_mean": None, "n": 0, "horizon_stats": stats}
    horizon, best = min(usable, key=lambda item: (-abs(item[1]["signed_mean"]), item[0]))
    return {"lag_ms": int(horizon), "signed_mean": float(best["signed_mean"]),
            "n": int(best["n"]), "horizon_stats": stats}


def method_c_peak(grid: pd.DataFrame) -> dict[str, Any]:
    rows = [r for r in lagged_regressions(grid) if r["r2"] is not None]
    if not rows:
        return {"lag_ms": None, "horizon_ms": None, "beta": None, "r2": None, "n": 0}
    best = min(rows, key=lambda r: (-r["r2"], r["lag_ms"], r["horizon_ms"]))
    return {"lag_ms": int(best["lag_ms"]), "horizon_ms": int(best["horizon_ms"]),
            "beta": float(best["beta"]), "r2": float(best["r2"]), "n": int(best["n"])}


def method_agreement(directions: Sequence[str | None]) -> dict[str, Any]:
    votes = Counter(d for d in directions if d is not None)
    if not votes:
        return {"agreement": "METHOD_INCONSISTENT", "direction": None, "votes": {}}
    direction, count = max(votes.items(), key=lambda kv: (kv[1], kv[0]))
    consistent = count >= 2
    return {"agreement": "METHOD_CONSISTENT_MARKET" if consistent else "METHOD_INCONSISTENT",
            "direction": direction if consistent else None, "votes": dict(votes)}


def dependence_sensitivity(overlap_stats: dict[int, dict[str, Any]],
                           nonoverlap_stats: dict[int, dict[str, Any]],
                           min_n: int = DEPENDENCE_MIN_N,
                           rel_diff: float = DEPENDENCE_REL_DIFF) -> dict[str, Any]:
    """Compare OVERLAPPING vs NON_OVERLAPPING_1S shock responses; differences
    must surface as DEPENDENCE_SENSITIVITY_WARNING, never be hidden."""
    sign_flip = False
    rel_diff_max: float | None = None
    compared = 0
    for horizon, ov in overlap_stats.items():
        no = nonoverlap_stats.get(int(horizon))
        if not no:
            continue
        a, b = ov.get("signed_mean"), no.get("signed_mean")
        if a is None or b is None or ov.get("n", 0) < min_n or no.get("n", 0) < min_n:
            continue
        compared += 1
        if (a > 0) != (b > 0):
            sign_flip = True
        diff = abs(a - b) / max(abs(a), abs(b), 1e-12)
        rel_diff_max = diff if rel_diff_max is None else max(rel_diff_max, diff)
    warning = sign_flip or (rel_diff_max is not None and rel_diff_max > rel_diff)
    return {"warning": bool(warning), "sign_flip": bool(sign_flip),
            "max_rel_diff": float(rel_diff_max) if rel_diff_max is not None else None,
            "horizons_compared": compared}


def clock_basis_assessment(exchange_lag_ms: int | None, receive_lag_ms: int | None,
                           btc_latency: dict[str, Any], clob_latency: dict[str, Any],
                           tolerance_ms: int = DIRECTION_TOLERANCE_MS) -> dict[str, Any]:
    d_exchange = classify_direction(exchange_lag_ms, tolerance_ms)
    d_receive = classify_direction(receive_lag_ms, tolerance_ms)
    if d_exchange is None or d_receive is None:
        status = "CLOCK_BASIS_UNKNOWN"
    elif d_exchange == d_receive:
        status = "CLOCK_BASIS_CONSISTENT"
    else:
        status = "CLOCK_BASIS_INSTABILITY"
    drift = max((btc_latency.get("p99_ms") or 0) - (btc_latency.get("p50_ms") or 0),
                (clob_latency.get("p99_ms") or 0) - (clob_latency.get("p50_ms") or 0))
    scale = max(abs(exchange_lag_ms) if exchange_lag_ms is not None else 0, tolerance_ms)
    return {"status": status, "exchange_direction": d_exchange, "receive_direction": d_receive,
            "latency_drift_ms": float(drift), "timing_resolution_warning": bool(drift >= scale)}


# ------------------------------------------------------- per-market table

def per_market_lead_lag_row(condition_id: str, slug: str, market_start_ms: int,
                            market_end_ms: int, grid_primary: pd.DataFrame,
                            grid_fine: pd.DataFrame, grid_primary_receive: pd.DataFrame,
                            btc_latency: dict[str, Any], clob_latency: dict[str, Any]
                            ) -> dict[str, Any]:
    """One row of data/derived/phase2b/per_market_lead_lag_<run_id>.parquet."""
    anchors = shock_anchor_rows(grid_primary)
    anchor_times = [int(t) for t in anchors["timestamp_ms"].tolist()]
    keep = set(non_overlapping_anchors(anchor_times))
    anchors_nonoverlap = anchors[anchors["timestamp_ms"].map(lambda t: int(t) in keep)]

    values_overlap = collect_shock_response_values(anchors, grid_fine)
    values_nonoverlap = collect_shock_response_values(anchors_nonoverlap, grid_fine)
    peak_overlap = method_b_peak(values_overlap)
    peak_nonoverlap = method_b_peak(values_nonoverlap)

    a = method_a_peak(grid_primary)
    c = method_c_peak(grid_primary)
    receive = method_a_peak(grid_primary_receive) if grid_primary_receive is not None and len(grid_primary_receive) else {"lag_ms": None, "correlation": None, "n": 0}

    directions = [classify_direction(a["lag_ms"]), classify_direction(peak_overlap["lag_ms"]),
                  classify_direction(c["lag_ms"])]
    agreement = method_agreement(directions)
    clock = clock_basis_assessment(a["lag_ms"], receive["lag_ms"], btc_latency, clob_latency)
    dependence = dependence_sensitivity(peak_overlap["horizon_stats"],
                                        peak_nonoverlap["horizon_stats"])

    calendar_date = datetime.fromtimestamp(int(market_start_ms) / 1000, timezone.utc).date().isoformat()
    row: dict[str, Any] = {
        "research_spec_version": RESEARCH_SPEC_VERSION_V2,
        "condition_id": condition_id, "slug": slug, "calendar_date": calendar_date,
        "market_start_ms": int(market_start_ms), "market_end_ms": int(market_end_ms),
        "n_shocks_overlapping": int(len(anchors)),
        "n_shocks_non_overlapping_1s": int(len(anchors_nonoverlap)),
        "method_a_lag_ms": a["lag_ms"], "method_a_correlation": a["correlation"],
        "method_a_n": a["n"],
        "method_b_lag_ms": peak_overlap["lag_ms"],
        "method_b_signed_mean": peak_overlap["signed_mean"], "method_b_n": peak_overlap["n"],
        "method_b_lag_ms_non_overlapping_1s": peak_nonoverlap["lag_ms"],
        "method_b_signed_mean_non_overlapping_1s": peak_nonoverlap["signed_mean"],
        "method_b_n_non_overlapping_1s": peak_nonoverlap["n"],
        "method_c_lag_ms": c["lag_ms"], "method_c_horizon_ms": c["horizon_ms"],
        "method_c_beta": c["beta"], "method_c_r2": c["r2"], "method_c_n": c["n"],
        "direction_method_a": directions[0], "direction_method_b": directions[1],
        "direction_method_c": directions[2], "direction": directions[0],
        "method_agreement": agreement["agreement"],
        "method_agreement_direction": agreement["direction"],
        "receive_method_a_lag_ms": receive["lag_ms"],
        "receive_method_a_correlation": receive["correlation"],
        "receive_direction": clock["receive_direction"],
        "clock_basis_status": clock["status"],
        "latency_drift_ms": clock["latency_drift_ms"],
        "timing_resolution_warning": clock["timing_resolution_warning"],
        "dependence_sensitivity_warning": dependence["warning"],
        "dependence_sign_flip": dependence["sign_flip"],
        "dependence_max_rel_diff": dependence["max_rel_diff"],
    }
    for name, latency in (("btc", btc_latency), ("clob", clob_latency)):
        for stat in ("p50_ms", "p90_ms", "p95_ms", "p99_ms", "max_ms"):
            row[f"{name}_latency_{stat}"] = latency.get(stat)
    stats = peak_overlap["horizon_stats"]
    for horizon in ECONOMIC_HORIZONS_MS:
        s = stats.get(int(horizon), {})
        row[f"response_{horizon}ms_signed_mean_cents"] = (
            s["signed_mean"] * 100.0 if s.get("signed_mean") is not None else None)
        row[f"response_{horizon}ms_abs_median_cents"] = (
            s["abs_median"] * 100.0 if s.get("abs_median") is not None else None)
    base_5s = stats.get(5000, {}).get("signed_mean")
    for horizon in ECONOMIC_HORIZONS_MS:
        value = stats.get(int(horizon), {}).get("signed_mean")
        row[f"latency_decay_fraction_{horizon}ms"] = (
            value / base_5s if value is not None and base_5s not in (None, 0) else None)
    return row


# ------------------------------------------------------------ pooling

def equal_market_summary(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    lags = [r["method_a_lag_ms"] for r in rows if r.get("method_a_lag_ms") is not None]
    corrs = [abs(r["method_a_correlation"]) for r in rows
             if r.get("method_a_correlation") is not None]
    directions = Counter(r["direction"] for r in rows if r.get("direction"))
    dominant, count = (max(directions.items(), key=lambda kv: (kv[1], kv[0]))
                       if directions else (None, 0))
    return {
        "n_markets": len(rows), "n_with_estimate": len(lags),
        "mean_lag_ms": float(np.mean(lags)) if lags else None,
        "median_lag_ms": float(np.median(lags)) if lags else None,
        "min_lag_ms": float(np.min(lags)) if lags else None,
        "max_lag_ms": float(np.max(lags)) if lags else None,
        "mean_abs_correlation": float(np.mean(corrs)) if corrs else None,
        "direction_counts": dict(directions),
        "dominant_direction": dominant if dominant is not None and count > 1 else None,
        "universal_direction_claim": bool(len(rows) >= 3 and len(directions) == 1),
        "universal_direction": (next(iter(directions)) if len(rows) >= 3 and len(directions) == 1 else None),
    }


def market_bootstrap(lags_ms: Sequence[int | float], seed: int = BOOTSTRAP_SEED,
                     resamples: int = BOOTSTRAP_RESAMPLES) -> dict[str, Any]:
    """Market-level (not shock-level) exploratory bootstrap; N>=10 markets only."""
    arr = np.asarray([float(v) for v in lags_ms if v is not None], dtype=float)
    if arr.size < BOOTSTRAP_MIN_MARKETS:
        return {"status": "NOT_COMPUTED_N_BELOW_10", "n_markets": int(arr.size)}
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, arr.size, size=(int(resamples), arr.size))
    means = arr[idx].mean(axis=1)
    point = float(arr.mean())
    point_direction = classify_direction(point)
    stability = float(np.mean([classify_direction(m) == point_direction for m in means]))
    return {"status": "COMPUTED_EXPLORATORY", "n_markets": int(arr.size),
            "resamples": int(resamples), "seed": int(seed),
            "mean_lag_ms": point,
            "ci_lo_ms": float(np.percentile(means, 2.5)),
            "ci_hi_ms": float(np.percentile(means, 97.5)),
            "direction_stability_fraction": stability}


def pooled_peak_lag(concat_primary_grid: pd.DataFrame) -> dict[str, Any]:
    """Shock-weighted (grid-row-weighted) pooled estimate - diagnostic only."""
    if concat_primary_grid is None or len(concat_primary_grid) == 0:
        return {"lag_ms": None, "correlation": None, "n": 0}
    return method_a_peak(concat_primary_grid)


# ------------------------------------------------- bucket stability

def _grid_response_at(grid: pd.DataFrame, horizon_ms: int) -> dict[str, Any]:
    times = grid["timestamp_ms"].to_numpy()
    mids = grid["pm_mid"].to_numpy(dtype=float)
    rets = grid["btc_ret_1s_bp"].to_numpy(dtype=float)
    values: list[float] = []
    for i in range(len(grid)):
        if not (np.isfinite(rets[i]) and abs(rets[i]) >= SHOCK_MIN_ABS_BP and np.isfinite(mids[i])):
            continue
        j = int(np.searchsorted(times, times[i] + horizon_ms))
        if j < len(times) and np.isfinite(mids[j]):
            values.append((1.0 if rets[i] > 0 else -1.0) * (float(mids[j]) - float(mids[i])))
    return response_stats_from_values(values)


def shock_bucket_stability(concat_primary_grid: pd.DataFrame,
                           horizon_ms: int = 1000) -> list[dict[str, Any]]:
    """Per shock bucket: pooled counts, per-market presence and sign consistency."""
    rows: list[dict[str, Any]] = []
    valid = concat_primary_grid.dropna(subset=["btc_shock_bucket", "btc_ret_1s_bp", "pm_mid"])
    valid = valid[valid["btc_ret_1s_bp"].abs() >= SHOCK_MIN_ABS_BP]
    times = concat_primary_grid["timestamp_ms"].to_numpy()
    mids = concat_primary_grid["pm_mid"].to_numpy(dtype=float)
    for bucket, group in valid.groupby("btc_shock_bucket", observed=True):
        per_market: dict[str, list[float]] = {}
        for row in group.itertuples():
            j = int(np.searchsorted(times, int(row.timestamp_ms) + horizon_ms))
            if j < len(times) and np.isfinite(mids[j]) and np.isfinite(float(row.pm_mid)):
                per_market.setdefault(str(row.condition_id), []).append(
                    (1.0 if row.btc_ret_1s_bp > 0 else -1.0) * (float(mids[j]) - float(row.pm_mid)))
        means = {cid: float(np.mean(v)) for cid, v in per_market.items() if v}
        positive = sum(1 for m in means.values() if m > 0)
        negative = sum(1 for m in means.values() if m < 0)
        pooled = response_stats_from_values([x for v in per_market.values() for x in v])
        rows.append({"bucket": str(bucket), "horizon_ms": int(horizon_ms),
                     "n_shocks": int(len(group)), "n_markets": len(means),
                     "signed_mean_cents": pooled["signed_mean"] * 100.0 if pooled["signed_mean"] is not None else None,
                     "n_markets_positive": positive, "n_markets_negative": negative,
                     "sign_consistent": bool(means and (positive == 0 or negative == 0))})
    return rows


def lifecycle_stability(concat_primary_grid: pd.DataFrame,
                        horizon_ms: int = 1000) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    shocks = concat_primary_grid.dropna(subset=["btc_ret_1s_bp"])
    shocks = shocks[shocks["btc_ret_1s_bp"].abs() >= SHOCK_MIN_ABS_BP]
    for bucket, group in concat_primary_grid.groupby("lifecycle_bucket", observed=True):
        group_shocks = shocks[shocks["lifecycle_bucket"] == bucket]
        stats = _grid_response_at(group.reset_index(drop=True), horizon_ms)
        rows.append({"bucket": str(bucket), "n_rows": int(len(group)),
                     "n_shocks": int(len(group_shocks)),
                     "n_markets": int(group["condition_id"].nunique()),
                     "signed_mean_cents": stats["signed_mean"] * 100.0 if stats["signed_mean"] is not None else None})
    return rows


def cluster_awareness(rows: Sequence[dict[str, Any]],
                      concat_primary_grid: pd.DataFrame) -> dict[str, Any]:
    """Shocks within one market are not independent; report all three counts."""
    shocks = 0
    valid = concat_primary_grid.dropna(subset=["btc_ret_1s_bp"]) if len(concat_primary_grid) else concat_primary_grid
    if len(valid):
        shocks = int((valid["btc_ret_1s_bp"].abs() >= SHOCK_MIN_ABS_BP).sum())
    return {"shock_count": shocks,
            "market_count": int(len(rows)),
            "day_count": int(len({r.get("calendar_date") for r in rows if r.get("calendar_date")}))}


# ------------------------------------------------------- manifest / cache

def file_sha256(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def market_cache_key(file_shas: Sequence[tuple[str, str]]) -> str:
    payload = "\n".join(f"{path}|{sha}" for path, sha in sorted(file_shas))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def cache_reusable(entry: dict[str, Any] | None, current_file_shas: dict[str, str],
                   spec_version: str = RESEARCH_SPEC_VERSION_V2,
                   timeline_path: Path | str | None = None) -> bool:
    if not entry or entry.get("research_spec_version") != spec_version:
        return False
    cached = dict(entry.get("file_shas") or {})
    if set(cached) != set(current_file_shas) or any(cached[k] != v for k, v in current_file_shas.items()):
        return False
    return timeline_path is None or Path(timeline_path).exists()


def load_state(path: Path | str) -> dict[str, Any]:
    path = Path(path)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"schema_version": 1, "research_spec_version": RESEARCH_SPEC_VERSION_V2,
            "processed_files": {}, "market_cache": {},
            "attained_b1_milestones": [], "attained_b2_milestones": [],
            "last_run_id": None}


def save_state(path: Path | str, state: dict[str, Any]) -> Path:
    return atomic_json(path, state)


# ------------------------------------------------------- milestones

def emit_milestone_once(reports_dir: Path | str, prefix: str, run_id: str,
                        payload: dict[str, Any], markdown_text: str) -> tuple[str, Path | None]:
    """First attainment writes an immutable report; later runs never rewrite it."""
    reports_dir = Path(reports_dir)
    existing = sorted(reports_dir.glob(f"{prefix}_*.json"))
    if existing:
        return "already_present", existing[0]
    path = reports_dir / f"{prefix}_{run_id}.json"
    atomic_json(path, payload)
    path.with_suffix(".md").write_text(markdown_text, encoding="utf-8")
    return "written", path


def b1_milestone_payload(milestone: int, n_markets: int, run_id: str,
                         per_market_rows: Sequence[dict[str, Any]],
                         equal_market: dict[str, Any], bootstrap: dict[str, Any],
                         artifact_path: str) -> dict[str, Any]:
    return {"research_spec_version": RESEARCH_SPEC_VERSION_V2, "milestone": f"B1-M{milestone}",
            "run_id": run_id, "n_markets": int(n_markets),
            "evidence_state": b1_maturity_state(n_markets),
            "equal_market_weighted": equal_market, "bootstrap": bootstrap,
            "cluster_awareness": {"market_count": int(n_markets),
                                  "note": "shocks within a market are not independent"},
            "per_market_lead_lag_artifact": artifact_path,
            "replication_goal": "estimate the lead-lag distribution; +250ms is NOT required to replicate",
            "no_real_trading": True, "causal_claim": False, "immutable": True}


def b2_milestone_payload(milestone: int, observations: Sequence[dict[str, Any]],
                         run_id: str, context_artifact: str | None) -> dict[str, Any]:
    first = observations[0] if observations else {}
    return {"research_spec_version": RESEARCH_SPEC_VERSION_V2,
            "milestone": f"B2-N{milestone:03d}", "run_id": run_id,
            "n_observations": len(observations), "state": b2_observation_state(len(observations)),
            "first_observation_id": first.get("observation_id"),
            "primary_key": "observation_id (Phase 2A prospective cohort)",
            "timestamp_semantics": {"std0_fill": "second precision",
                                    "same_second_ordering": "FORBIDDEN",
                                    "post_fill_anchor": "fill_second_end"},
            "context_artifact": context_artifact, "no_real_trading": True,
            "causal_claim": False, "immutable": True}


def frozen_invariant_check(before: dict[str, str], after: dict[str, str]) -> dict[str, Any]:
    changed = sorted(k for k in before if before.get(k) != after.get(k))
    return {"before": before, "after": after, "changed": changed,
            "status": "FAIL" if changed else "PASS"}


def assert_allowed_decision(decisions: Sequence[str]) -> None:
    for token in decisions:
        if token in FORBIDDEN_DECISION_TOKENS:
            raise ValueError(f"forbidden decision token: {token}")
        if token not in ALLOWED_OVERALL_DECISIONS and token not in ALLOWED_B1_STATES \
                and token not in ALLOWED_B2_STATES:
            raise ValueError(f"decision token outside pre-registered vocabulary: {token}")
