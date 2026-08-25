"""CLI: Phase 1.5 bias & robustness audit (read-only over Phase 1 truth).

Runs the six audits against the frozen Phase 1 derived data:

1. selection bias (G1 vs G0 pre-FirstOpposite SMD)
2. BUY-only label sensitivity (audit-only directional label)
3. within-page identity collision sensitivity (A vs B datasets)
4. temporal stability (daily / ISO week)
5. universe placebo (BTC-15m / ETH-5m / SOL-5m / XRP-5m)
6. negative controls (within-week label shuffle + future-window placebo)

Outputs ``data/reports/bias_audit_<stamp>.{json,md}`` and an optional
per-market audit CSV under ``data/derived/audit/``.

Guarantees: the ledger file, its semantic content, and config/settings.yaml
are hashed before and after; any change FAILS the run. No raw page is
modified; no label, episode rule, or frozen definition is touched; nothing
is sent anywhere.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(_PROJECT_SRC))

import pyarrow.parquet as pq  # noqa: E402

from std0_quant.audit.bias_report import (  # noqa: E402
    INTERPRETATION_GUARDRAILS,
    KNOWN_LIMITATIONS,
    overall_status,
    phase2_recommendation,
    render_markdown,
)
from std0_quant.audit.buy_only_sensitivity import (  # noqa: E402
    MarketFillWindows,
    run_buy_only_sensitivity,
)
from std0_quant.audit.collision_sensitivity import (  # noqa: E402
    collision_concentration,
    compare_datasets,
    scan_pages,
)
from std0_quant.audit.negative_controls import (  # noqa: E402
    DEFAULT_N_SHUFFLES,
    DEFAULT_RANDOM_SEED,
    build_t0_features,
    run_future_window_placebo,
    run_global_shuffle_diagnostic,
    run_shuffle_control,
)
from std0_quant.audit.selection_bias import run_selection_bias  # noqa: E402
from std0_quant.audit.temporal_stability import (  # noqa: E402
    DEFAULT_MIN_WEEKLY_N,
    run_temporal_stability,
)
from std0_quant.audit.universe_placebo import (  # noqa: E402
    PLACEBO_UNIVERSES,
    UniverseSpec,
    summarize_universe,
)
from std0_quant.config import load_settings, resolve_path  # noqa: E402
from std0_quant.events.event_ledger import (  # noqa: E402
    SlugWindowMetadataProvider,
    build_ledger_rows,
)
from std0_quant.events.fills import Fill  # noqa: E402
from std0_quant.logging_setup import setup_logging  # noqa: E402

BTC5M = UniverseSpec("BTC-5m", "btc-updown-5m-", 300)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def semantic_ledger_hash(rows: list[dict]) -> str:
    """Hash of the label-bearing identity of every ledger row."""
    parts = sorted(
        f"{r.get('condition_id')}|{r.get('clean_flag')}|"
        f"{r.get('exclude_reason')}|{r.get('y30')}|"
        f"{r.get('y30_horizon_eligible')}"
        for r in rows
    )
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def load_ledger_rows(path: Path) -> list[dict]:
    table = pq.read_table(path)
    return table.to_pylist()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", default=None,
                        help="path to event_ledger.parquet "
                             "(default: data/derived/event_ledger.parquet)")
    parser.add_argument("--output", default=None,
                        help="report directory (default: data/reports)")
    parser.add_argument("--n-shuffles", type=int,
                        default=DEFAULT_N_SHUFFLES,
                        help="label shuffles for the negative control")
    parser.add_argument("--seed", type=int, default=DEFAULT_RANDOM_SEED,
                        help="random seed for the shuffle control")
    parser.add_argument("--min-weekly-n", type=int,
                        default=DEFAULT_MIN_WEEKLY_N,
                        help="weeks below this many clean markets are "
                             "flagged low-N and excluded from the rule")
    return parser


def _fills_dataframe(fills_path: Path):
    import pandas as pd  # noqa: F401  (pandas ships with pyarrow use here)

    return pq.read_table(fills_path).to_pandas()


def _market_windows(fills_df, target_cids: set[str],
                    rows_by_cid: dict[str, dict]) -> dict[str, MarketFillWindows]:
    """BUY/SELL timestamp lists per outcome for the target markets."""
    sub = fills_df[fills_df["condition_id"].isin(target_cids)]
    buy_ts: dict[tuple[str, str, str], list[int]] = {}
    for (cid, outcome), group in sub[sub["side"] == "BUY"].groupby(
        ["condition_id", "outcome"]
    ):
        buy_ts[(cid, "buy", outcome)] = sorted(
            int(t) for t in group["timestamp_ms"] if t is not None
        )
    for (cid, outcome), group in sub[sub["side"] == "SELL"].groupby(
        ["condition_id", "outcome"]
    ):
        buy_ts[(cid, "sell", outcome)] = sorted(
            int(t) for t in group["timestamp_ms"] if t is not None
        )
    windows: dict[str, MarketFillWindows] = {}
    for cid in target_cids:
        row = rows_by_cid[cid]
        windows[row["condition_id"]] = MarketFillWindows(
            t0_ms=row["first_opp_end_ms"],
            initial_direction=row["initial_direction"],
            buy_ts_by_outcome={
                outcome: buy_ts.get((cid, "buy", outcome), [])
                for outcome in ("Up", "Down")
            },
            sell_ts_by_outcome={
                outcome: buy_ts.get((cid, "sell", outcome), [])
                for outcome in ("Up", "Down")
            },
            market_end_ms=row["market_end_ms"],
            y30=row["y30"],
        )
    return windows


def _g0_initial_qty(episodes_path: Path) -> dict[str, float]:
    """Earliest initial-direction episode size per market (condition_id)."""
    ep = pq.read_table(
        episodes_path,
        columns=["market_id", "direction", "episode_start_ms", "total_shares"],
    ).to_pandas()
    ep = ep.sort_values(["market_id", "direction", "episode_start_ms"])
    first = ep.groupby(["market_id", "direction"], as_index=False).first()
    out: dict[str, float] = {}
    for rec in first.itertuples(index=False):
        out.setdefault(f"{rec.market_id}|{rec.direction}", rec.total_shares)
    return out


def _scan_api_pages(api_pages_dir: Path):
    pages = []
    n_skipped = 0
    for run_dir in sorted(api_pages_dir.iterdir()):
        if not run_dir.is_dir():
            continue
        for page_path in sorted(run_dir.glob("page_*.json")):
            with open(page_path, encoding="utf-8") as fh:
                envelope = json.load(fh)
            if envelope.get("status_code") != 200:
                n_skipped += 1
                continue
            body = envelope.get("body")
            if isinstance(body, str):
                try:
                    body = json.loads(body)
                except json.JSONDecodeError:
                    n_skipped += 1
                    continue
            if not isinstance(body, list):
                n_skipped += 1
                continue
            pages.append((f"{run_dir.name}/{page_path.name}", body))
    return pages, n_skipped


def _row_to_fill(rec: dict) -> Fill:
    """Parquet row (normalized schema) -> Fill; NaN -> None."""
    def clean(value):
        if value is None:
            return None
        if isinstance(value, float) and value != value:  # NaN
            return None
        return value

    return Fill(
        fill_id=rec["fill_id"],
        proxy_wallet=clean(rec.get("proxy_wallet")),
        side=clean(rec.get("side")),
        asset=clean(rec.get("asset")),
        condition_id=clean(rec.get("condition_id")),
        size=clean(rec.get("size")),
        price=clean(rec.get("price")),
        timestamp_ms=clean(rec.get("timestamp_ms")),
        timestamp_raw=rec.get("timestamp_raw"),
        title=clean(rec.get("title")),
        slug=clean(rec.get("slug")),
        outcome=clean(rec.get("outcome")),
        outcome_index=clean(rec.get("outcome_index")),
        transaction_hash=clean(rec.get("transaction_hash")),
        source=rec.get("source") or "fills.parquet",
        fetched_at_ms=rec.get("fetched_at_ms") or 0,
        raw_json={},
    )


def _universe_rows(fills_df, spec: UniverseSpec) -> tuple[list[dict], int]:
    """Rebuild a placebo ledger with the frozen construction."""
    sub = fills_df[fills_df["slug"].str.startswith(spec.slug_prefix,
                                                    na=False)]
    fills = [_row_to_fill(rec) for rec in sub.to_dict("records")]
    provider = SlugWindowMetadataProvider.from_fills(
        fills, slug_prefix=spec.slug_prefix,
        window_seconds=spec.window_seconds,
    )
    rows = build_ledger_rows(
        fills, provider, scope_slug_prefix=spec.slug_prefix
    )
    in_scope = [
        r for r in rows
        if r.get("slug") and r["slug"].startswith(spec.slug_prefix)
    ]
    return in_scope, len(sub)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    settings = load_settings()
    log_path = setup_logging(resolve_path(settings, "logs"), "run_bias_audit")

    derived_dir = resolve_path(settings, "derived")
    ledger_path = Path(args.ledger) if args.ledger else (
        derived_dir / "event_ledger.parquet"
    )
    fills_path = resolve_path(settings, "normalized") / "fills.parquet"
    episodes_path = derived_dir / "episodes.parquet"
    settings_path = Path(__file__).resolve().parents[1] / "config" / "settings.yaml"
    api_pages_dir = resolve_path(settings, "raw_api_pages")
    report_dir = Path(args.output) if args.output else resolve_path(
        settings, "reports"
    )

    print(f"ledger:   {ledger_path}")
    print(f"fills:    {fills_path}")
    print(f"log:      {log_path}")

    # -- invariants BEFORE ---------------------------------------------------
    ledger_sha_before = sha256_file(ledger_path)
    settings_sha_before = sha256_file(settings_path)
    rows = load_ledger_rows(ledger_path)
    semantic_before = semantic_ledger_hash(rows)

    clean_rows = [r for r in rows if r.get("clean_flag")]
    excluded_rows = [r for r in rows if not r.get("clean_flag")]
    rows_by_cid = {r["condition_id"]: r for r in rows}
    n_fills_total = pq.read_metadata(fills_path).num_rows

    print(f"markets:  {len(rows):,} (clean {len(clean_rows):,} / "
          f"excluded {len(excluded_rows):,})")

    fills_df = _fills_dataframe(fills_path)

    # =====================================================================
    # Audit 1 - selection bias
    # =====================================================================
    print("[1/6] selection bias ...")
    g0_qty = _g0_initial_qty(episodes_path)
    totals: dict[str, dict[str, float]] = {}
    sub_all = fills_df[fills_df["condition_id"].isin(
        {r["condition_id"] for r in clean_rows})]
    for (cid, side), group in sub_all.groupby(["condition_id", "side"]):
        totals.setdefault(cid, {})[side] = float(group["size"].sum())
    fill_counts = sub_all.groupby("condition_id").size().to_dict()
    extras: dict[str, dict[str, float]] = {}
    for row in clean_rows:
        cid = row["condition_id"]
        e: dict[str, float] = {}
        if row.get("initial_qty") is None and row.get("initial_direction"):
            key = f"{cid}|{row['initial_direction']}"
            if key in g0_qty:
                e["initial_qty"] = g0_qty[key]
        t = totals.get(cid, {})
        e["total_buy_qty"] = t.get("BUY")
        e["total_sell_qty"] = t.get("SELL")
        e["total_fill_count"] = float(fill_counts.get(cid, 0))
        extras[cid] = e
    sb = run_selection_bias(clean_rows, extras)
    sb_max_smd = sb.max_abs_smd()
    sb_median_smd = sb.median_abs_smd()

    # =====================================================================
    # Audit 2 - BUY-only sensitivity
    # =====================================================================
    print("[2/6] buy-only sensitivity ...")
    fo_rows = [r for r in clean_rows if r.get("first_opp_end_ms") is not None]
    windows = _market_windows(
        fills_df, {r["condition_id"] for r in fo_rows}, rows_by_cid
    )
    bos = run_buy_only_sensitivity(windows)
    if bos.n_consistency_errors:
        print(f"WARNING: {bos.n_consistency_errors} y30/fills consistency "
              "errors (fills parquet disagrees with the frozen ledger)")

    # =====================================================================
    # Audit 3 - collision sensitivity
    # =====================================================================
    print("[3/6] collision sensitivity (scanning api_pages) ...")
    pages, n_skipped_pages = _scan_api_pages(api_pages_dir)
    scan = scan_pages(pages)
    print(f"        pages={scan.n_pages:,} records={scan.n_records:,} "
          f"collisions={scan.n_collisions:,} "
          f"excess={scan.excess_records:,} "
          f"(skipped non-200/unparseable pages: {n_skipped_pages})")
    cs = compare_datasets(
        clean_rows, excluded_rows, scan.affected_condition_ids,
        scan.collision_fill_count_by_condition(),
    )
    conc = collision_concentration(scan.collisions, clean_rows)

    # =====================================================================
    # Audit 4 - temporal stability
    # =====================================================================
    print("[4/6] temporal stability ...")
    ts = run_temporal_stability(clean_rows, min_weekly_n=args.min_weekly_n)

    # =====================================================================
    # Audit 5 - universe placebo
    # =====================================================================
    print("[5/6] universe placebo ...")
    main_scope_rows = [
        r for r in rows
        if r.get("slug") and r["slug"].startswith(BTC5M.slug_prefix)
    ]
    n_main_fills = int(
        (fills_df["slug"].str.startswith(BTC5M.slug_prefix, na=False)).sum()
    )
    reference = summarize_universe(BTC5M, main_scope_rows, n_main_fills)
    placebo_summaries = []
    for spec in PLACEBO_UNIVERSES:
        universe_rows, n_universe_fills = _universe_rows(fills_df, spec)
        placebo_summaries.append(
            summarize_universe(spec, universe_rows, n_universe_fills)
        )
        print(f"        {spec.name}: fills={n_universe_fills:,} "
              f"clean={placebo_summaries[-1].n_clean:,} "
              f"comparable={placebo_summaries[-1].comparable}")

    # =====================================================================
    # Audit 6 - negative controls
    # =====================================================================
    print("[6/6] negative controls ...")
    X, y, weeks, n_dropped = build_t0_features(clean_rows)
    print(f"        features: {X.shape[0]:,} samples "
          f"({n_dropped:,} dropped: censored/incomplete)")
    shuffle = run_shuffle_control(
        X, y, weeks, n_shuffles=args.n_shuffles, seed=args.seed
    )
    print(f"        shuffle AUC: mean={shuffle.auc_mean} "
          f"p95={shuffle.auc_p95} -> {shuffle.status}")
    # Diagnostic only (NOT part of the PASS/FAIL rule): a global shuffle
    # destroys weekly base rates as well. Its AUC ~ 0.5 together with a
    # high within-week-shuffle AUC localizes the signal to between-week
    # rate dispersion x drifting features, not per-sample leakage.
    global_diag = run_global_shuffle_diagnostic(
        X, y, n_shuffles=min(args.n_shuffles, 5), seed=args.seed
    )
    print(f"        global-shuffle diagnostic AUC: "
          f"mean={global_diag.auc_mean} (diagnostic only)")
    future = run_future_window_placebo(windows)

    # -- invariants AFTER ----------------------------------------------------
    ledger_sha_after = sha256_file(ledger_path)
    settings_sha_after = sha256_file(settings_path)
    rows_after = load_ledger_rows(ledger_path)
    semantic_after = semantic_ledger_hash(rows_after)
    invariants_ok = (
        ledger_sha_before == ledger_sha_after
        and settings_sha_before == settings_sha_after
        and semantic_before == semantic_after
    )

    # =====================================================================
    # Report assembly
    # =====================================================================
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = report_dir / f"bias_audit_{stamp}.json"
    md_path = report_dir / f"bias_audit_{stamp}.md"

    def group_stats_dict(g):
        return {"n": g.n, "missing": g.missing, "mean": g.mean,
                "median": g.median, "std": g.std, "p25": g.p25, "p75": g.p75}

    audit_statuses = {
        "selection_bias": sb.status,
        "buy_only_sensitivity": bos.status,
        "collision_sensitivity": cs.status,
        "temporal_stability": ts.status,
        "universe_placebo": "REPORTED",
        "negative_controls_shuffle": shuffle.status,
    }
    overall = overall_status(audit_statuses, invariants_ok)
    if bos.n_consistency_errors:
        overall = "FAIL"

    placebo_cells = []
    for p in placebo_summaries:
        rate = p.y30_positive_rate_observable
        rate_str = "n/a" if rate is None else f"{rate * 100:.1f}%"
        placebo_cells.append(f"{p.name}: {rate_str}")
    def rnd(value, digits=2):
        return None if value is None else round(float(value), digits)

    summary_table = [
        {"audit_id": "1", "name": "Selection bias (SMD)",
         "key_question": "Are G1/G0 different on pre-FirstOpposite variables?",
         "key_number": f"max |SMD|={rnd(sb_max_smd)}, material pre-vars="
                       f"{len(sb.material_pre_variables)}",
         "status": sb.status},
        {"audit_id": "2", "name": "BUY-only label sensitivity",
         "key_question": "Does the BUY-only y30 definition matter?",
         "key_number": f"delta={rnd(bos.delta_pp)}pp, sell-only upgrades="
                       f"{bos.n_sell_only_upgrades}",
         "status": bos.status},
        {"audit_id": "3", "name": "Collision sensitivity",
         "key_question": "Do within-page identity collisions move rates?",
         "key_number": f"delta y30={rnd(cs.delta_y30_pp)}pp, affected="
                       f"{cs.n_clean_affected}",
         "status": cs.status},
        {"audit_id": "4", "name": "Temporal stability",
         "key_question": "Are the rates stable across ISO weeks?",
         "key_number": f"spread={rnd(ts.spread_pp)}pp, max WoW="
                       f"{rnd(ts.max_abs_week_over_week_pp)}pp",
         "status": ts.status},
        {"audit_id": "5", "name": "Universe placebo",
         "key_question": "Do the patterns replicate in other universes?",
         "key_number": "; ".join(placebo_cells),
         "status": "REPORTED"},
        {"audit_id": "6", "name": "Negative controls",
         "key_question": "Any leakage signal under randomized labels?",
         "key_number": f"shuffle AUC p95={rnd(shuffle.auc_p95)}",
         "status": shuffle.status},
    ]

    report = {
        "generated_at_utc": datetime.now(tz=timezone.utc).isoformat(),
        "phase": "1.5",
        "overall_status": overall,
        "invariants": {
            "ledger_file_sha256_before": ledger_sha_before,
            "ledger_file_sha256_after": ledger_sha_after,
            "ledger_file_sha256_changed": ledger_sha_before != ledger_sha_after,
            "settings_sha256_before": settings_sha_before,
            "settings_sha256_after": settings_sha_after,
            "settings_sha256_changed": settings_sha_before != settings_sha_after,
            "ledger_semantic_hash_before": semantic_before,
            "ledger_semantic_hash_after": semantic_after,
            "ledger_semantic_hash_changed": semantic_before != semantic_after,
            "invariants_ok": invariants_ok,
        },
        "inputs": {
            "ledger_path": str(ledger_path),
            "n_fills": n_fills_total,
            "n_markets": len(rows),
            "n_clean": len(clean_rows),
            "n_excluded": len(excluded_rows),
        },
        "audits": {
            "selection_bias": {
                "g1_count": sb.g1_count, "g0_count": sb.g0_count,
                "max_abs_smd": sb_max_smd,
                "median_abs_smd": sb_median_smd,
                "material_pre_variables": sb.material_pre_variables,
                "status": sb.status,
                "comparisons": [
                    {
                        "variable": c.variable,
                        "pre_first_opposite": c.pre_first_opposite,
                        "smd": c.smd, "smd_note": c.smd_note,
                        "magnitude": c.magnitude,
                        "g1": group_stats_dict(c.g1),
                        "g0": group_stats_dict(c.g0),
                    } for c in sb.comparisons
                ],
            },
            "buy_only_sensitivity": {
                "n_markets": bos.n_markets, "n_eligible": bos.n_eligible,
                "original_positive": bos.original_positive,
                "sensitivity_positive": bos.sensitivity_positive,
                "original_rate": bos.original_rate,
                "sensitivity_rate": bos.sensitivity_rate,
                "delta_pp": bos.delta_pp,
                "agreement_rate": bos.agreement_rate,
                "n_sell_only_upgrades": bos.n_sell_only_upgrades,
                "n_both_event_types": bos.n_both_event_types,
                "n_consistency_errors": bos.n_consistency_errors,
                "sell_only_share": bos.sell_only_share_of_eligible,
                "status": bos.status,
            },
            "collision_sensitivity": {
                "n_pages": scan.n_pages, "n_records": scan.n_records,
                "n_pages_with_collisions": scan.n_pages_with_collisions,
                "n_collisions": scan.n_collisions,
                "excess_records": scan.excess_records,
                "n_skipped_pages": n_skipped_pages,
                "n_clean_affected": cs.n_clean_affected,
                "n_excluded_affected": cs.n_excluded_affected,
                "collision_fill_count": cs.collision_fill_count,
                "share_clean_affected": cs.share_clean_affected,
                "delta_y30_pp": cs.delta_y30_pp,
                "delta_first_opp_pp": cs.delta_first_opp_pp,
                "median_initial_qty_a": cs.stats_a.median_initial_qty,
                "median_initial_qty_b": cs.stats_b.median_initial_qty,
                "status": cs.status,
                "concentration": {
                    "by_utc_date": dict(sorted(conc.by_utc_date.items())),
                    "by_utc_hour": dict(sorted(conc.by_utc_hour.items())),
                    "by_side": dict(sorted(conc.by_side.items())),
                    "by_outcome": dict(sorted(conc.by_outcome.items())),
                    "by_market_top": conc.by_market_top[:10],
                    "affected_clean_labels": conc.affected_clean_labels,
                },
            },
            "temporal_stability": {
                "min_weekly_n": ts.min_weekly_n,
                "n_low_n_weeks": ts.n_low_n_weeks,
                "rate_min": ts.rate_min, "rate_max": ts.rate_max,
                "rate_mean": ts.rate_mean, "rate_std": ts.rate_std,
                "spread_pp": ts.spread_pp,
                "max_abs_week_over_week_pp": ts.max_abs_week_over_week_pp,
                "extreme_weeks": ts.extreme_weeks,
                "status": ts.status,
                "weekly": [
                    {
                        "key": w.key,
                        "clean_market_count": w.clean_market_count,
                        "first_opposite_count": w.first_opposite_count,
                        "y30_observable": w.y30_observable,
                        "y30_positive": w.y30_positive,
                        "y30_negative": w.y30_negative,
                        "y30_censored": w.y30_censored,
                        "positive_rate_observable": w.positive_rate_observable,
                        "median_initial_qty": w.median_initial_qty,
                        "median_seconds_to_initial": w.median_seconds_to_initial,
                        "low_n": w.low_n,
                    } for w in ts.weekly
                ],
                "daily": [
                    {"key": d.key,
                     "clean_market_count": d.clean_market_count,
                     "first_opposite_count": d.first_opposite_count,
                     "y30_observable": d.y30_observable,
                     "y30_positive": d.y30_positive}
                    for d in ts.daily
                ],
            },
            "universe_placebo": {
                "reference": {
                    "name": reference.name, "n_fills": reference.n_fills,
                    "n_clean": reference.n_clean,
                    "n_excluded": reference.n_excluded,
                    "n_first_opp": reference.n_first_opp,
                    "n_eligible": reference.n_eligible,
                    "y30_positive": reference.y30_positive,
                    "first_opp_rate": reference.first_opp_rate,
                    "y30_positive_rate_observable":
                        reference.y30_positive_rate_observable,
                    "comparable": reference.comparable,
                    "note": reference.not_comparable_reason,
                },
                "placebos": [
                    {
                        "name": p.name, "n_fills": p.n_fills,
                        "n_clean": p.n_clean, "n_excluded": p.n_excluded,
                        "exclusion_reasons": p.exclusion_reasons,
                        "n_first_opp": p.n_first_opp,
                        "n_eligible": p.n_eligible,
                        "y30_positive": p.y30_positive,
                        "y30_censored": p.y30_censored,
                        "first_opp_rate": p.first_opp_rate,
                        "y30_positive_rate_observable":
                            p.y30_positive_rate_observable,
                        "delta_first_opp_pp": _delta_pp(
                            p.first_opp_rate, reference.first_opp_rate),
                        "delta_y30_pp": _delta_pp(
                            p.y30_positive_rate_observable,
                            reference.y30_positive_rate_observable),
                        "comparable": p.comparable,
                        "note": p.not_comparable_reason,
                    } for p in placebo_summaries
                ],
                "construction_note": (
                    "placebo ledgers use the same frozen construction "
                    "(episode rule, Y30, exclusions) with no coverage "
                    "provider: book/BTC collection targeted the BTC-5m "
                    "universe only, and coverage-based exclusions are "
                    "vacuous on the main ledger"
                ),
            },
            "negative_controls": {
                "shuffle": {
                    "n_samples": shuffle.n_samples,
                    "n_dropped": n_dropped,
                    "n_shuffles": shuffle.n_shuffles, "seed": shuffle.seed,
                    "auc_mean": shuffle.auc_mean, "auc_std": shuffle.auc_std,
                    "auc_p95": shuffle.auc_p95, "auc_max": shuffle.auc_max,
                    "auc_values": shuffle.auc_values,
                    "error": shuffle.error, "status": shuffle.status,
                },
                "global_shuffle_diagnostic": {
                    "note": (
                        "diagnostic only, not part of the PASS/FAIL rule: "
                        "global permutation also destroys weekly base "
                        "rates; AUC ~ 0.5 here + high within-week AUC "
                        "localizes the signal to between-week rate "
                        "dispersion x drifting features"
                    ),
                    "n_shuffles": global_diag.n_shuffles,
                    "auc_mean": global_diag.auc_mean,
                    "auc_std": global_diag.auc_std,
                    "auc_p95": global_diag.auc_p95,
                    "auc_max": global_diag.auc_max,
                },
                "future_window": {
                    "windows": [
                        {"start_offset_s": w.start_offset_s,
                         "end_offset_s": w.end_offset_s,
                         "n_markets": w.n_markets,
                         "n_eligible": w.n_eligible,
                         "n_positive": w.n_positive,
                         "positive_rate": w.positive_rate}
                        for w in future.windows
                    ],
                },
            },
        },
        "summary_table": summary_table,
        "interpretation_guardrails": list(INTERPRETATION_GUARDRAILS),
        "known_limitations": list(KNOWN_LIMITATIONS),
        "provenance": {
            "command": "scripts/run_bias_audit.py "
                       f"--n-shuffles {args.n_shuffles} --seed {args.seed} "
                       f"--min-weekly-n {args.min_weekly_n}",
            "n_shuffles": args.n_shuffles, "seed": args.seed,
            "min_weekly_n": args.min_weekly_n,
            "json_path": str(json_path),
        },
    }

    report_dir.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    md_path.write_text(render_markdown(report), encoding="utf-8")

    # -- per-market audit CSV (traceability for Phase 2) ----------------------
    audit_dir = derived_dir / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    csv_path = audit_dir / f"buy_only_sensitivity_{stamp}.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "condition_id", "t0_ms", "initial_direction",
            "opposite_direction", "horizon_eligible", "y30",
            "y30_directional_sensitivity", "buy_opposite_event_ms",
            "sell_initial_event_ms", "sell_only_upgrade",
        ])
        for m in sorted(bos.per_market, key=lambda m: m.market_id):
            writer.writerow([
                m.market_id, m.t0_ms, m.initial_direction,
                m.opposite_direction, m.horizon_eligible, m.y30,
                m.y30_directional_sensitivity, m.buy_opposite_event_ms,
                m.sell_initial_event_ms, m.sell_only_upgrade,
            ])

    print()
    print(f"overall:  {overall}")
    print(f"          {phase2_recommendation(overall)}")
    print(f"json:     {json_path}")
    print(f"md:       {md_path}")
    print(f"csv:      {csv_path}")
    return 0 if overall != "FAIL" else 2


def _delta_pp(rate: float | None, reference: float | None) -> float | None:
    if rate is None or reference is None:
        return None
    return (rate - reference) * 100


if __name__ == "__main__":
    raise SystemExit(main())
