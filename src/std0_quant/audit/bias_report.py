"""Phase 1.5 bias-audit report assembly and Markdown rendering.

Read-only: consumes the six audit results (already converted to plain
dicts by the runner) and produces the JSON structure + human-readable
Markdown. The report never modifies Phase 1 artifacts.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

INTERPRETATION_GUARDRAILS: tuple[str, ...] = (
    "Statistical association is NOT behavioral interpretation and NOT "
    "causal interpretation. Every number in this report is descriptive.",
    "A material SMD only says the G1/G0 distributions differ on that "
    "variable; it does not prove selection bias invalidates any Phase 1 "
    "conclusion.",
    "y30_directional_sensitivity is an AUDIT-ONLY label. It never replaces "
    "the frozen y30; its delta only measures how much the BUY-only event "
    "definition matters.",
    "Temporal WARN does not prove a mechanism changed; temporal PASS does "
    "not prove future stability.",
    "Universe placebo similarities/differences cannot prove BTC-5m is "
    "special or generic - the universes differ on many dimensions at once.",
    "A passing negative control only says the t0-observable feature set "
    "shows no leakage signal under randomized labels. It does NOT say any "
    "model is valid or that any edge exists.",
    "Behavior must not be relabeled as arbitrage, market making, or "
    "inventory management without independent evidence.",
    "Censored markets (window extending past market end) are never counted "
    "as negatives.",
)

KNOWN_LIMITATIONS: tuple[str, ...] = (
    "Fill timestamps are second-granular: the relative order of same-second "
    "events is not recoverable from public data (same-second initial "
    "direction ambiguity is excluded as SAME_SECOND, never guessed).",
    "trade_identity collapses byte-identical fills inside one transaction; "
    "Audit 3 quantifies the effect of this on headline rates.",
    "The data API caps offset at 10000; history deeper than 10000x500 "
    "records per time window is unreachable (the backfill sliced windows "
    "to cover the study period).",
    "Book/BTC coverage percentages depend on live-collection sessions "
    "overlapping each market; absent sessions mean absent coverage, and "
    "coverage-based exclusions only apply where a session promised data.",
    "Phase 1.5 stays read-only: no orders, no signing, no private keys, no "
    "executor. All results are computed offline from stored raw data.",
)

# Status severity for the overall rollup. FAIL is reserved for hard
# invariants (hash changes, negative-control leakage) - never for a
# descriptive difference.
_SEVERITY = {"PASS": 0, "LOW_SENSITIVITY": 0, "REPORTED": 0,
             "NOT_COMPUTABLE": 0, "WARN": 1, "HIGH_SENSITIVITY": 1,
             "FAIL": 2}


def severity(status: str) -> int:
    return _SEVERITY.get(status, 1)


def overall_status(audit_statuses: Mapping[str, str],
                   invariants_ok: bool) -> str:
    if not invariants_ok:
        return "FAIL"
    if any(s == "FAIL" for s in audit_statuses.values()):
        return "FAIL"
    if any(severity(s) >= 1 for s in audit_statuses.values()):
        return "WARN"
    return "PASS"


def phase2_recommendation(overall: str) -> str:
    if overall == "FAIL":
        return (
            "NO-GO for Phase 2 modeling until the failing invariant or "
            "negative control is explained and fixed; the current feature "
            "set must not be used for prediction."
        )
    if overall == "WARN":
        return (
            "CONDITIONAL GO for Phase 2: proceed only with the affected "
            "caveats carried into every downstream interpretation (see the "
            "WARN rows above); no label or definition may be changed to "
            "silence a WARN."
        )
    return (
        "GO for Phase 2 descriptive modeling on the frozen labels, with the "
        "interpretation guardrails of this report attached."
    )


def _fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.2f}%"


def render_markdown(report: Mapping[str, Any]) -> str:
    lines: list[str] = []
    overall = report["overall_status"]
    lines.append("# std0-quant Phase 1.5 - Bias & Robustness Audit")
    lines.append("")
    lines.append(f"*Generated (UTC): {report['generated_at_utc']}*")
    lines.append("")
    lines.append(f"**Overall: {overall}** - "
                 f"{phase2_recommendation(overall)}")
    lines.append("")

    # -- invariants ----------------------------------------------------------
    inv = report["invariants"]
    lines.append("## Frozen-truth invariants (must be unchanged)")
    lines.append("")
    lines.append("| invariant | before | after | changed |")
    lines.append("|---|---|---|---|")
    for key, label in (
        ("ledger_file_sha256", "event_ledger.parquet sha256"),
        ("settings_sha256", "config/settings.yaml sha256"),
        ("ledger_semantic_hash", "semantic hash (condition_id, clean_flag, "
                                 "exclude_reason, y30, y30_horizon_eligible)"),
    ):
        lines.append(
            f"| {label} | `{inv[key + '_before'][:16]}...` | "
            f"`{inv[key + '_after'][:16]}...` | "
            f"{'**YES**' if inv[key + '_changed'] else 'no'} |"
        )
    lines.append("")

    # -- inputs --------------------------------------------------------------
    inp = report["inputs"]
    lines.append("## Inputs")
    lines.append("")
    lines.append(f"- ledger: `{inp['ledger_path']}`")
    lines.append(f"- fills scanned: {inp['n_fills']:,}")
    lines.append(f"- markets in ledger: {inp['n_markets']:,} "
                 f"(clean {inp['n_clean']:,} / excluded {inp['n_excluded']:,})")
    lines.append("")

    # -- summary table -------------------------------------------------------
    lines.append("## Summary")
    lines.append("")
    lines.append("| # | audit | key question | key number | status |")
    lines.append("|---|---|---|---|---|")
    for row in report["summary_table"]:
        lines.append(
            f"| {row['audit_id']} | {row['name']} | {row['key_question']} | "
            f"{row['key_number']} | **{row['status']}** |"
        )
    lines.append("")

    # -- audit details -------------------------------------------------------
    audits = report["audits"]
    lines.append("## 1. Selection bias (G1 vs G0, pre-FirstOpposite SMD)")
    lines.append("")
    a1 = audits["selection_bias"]
    lines.append(f"- G1 (FirstOpposite) markets: {a1['g1_count']:,}; "
                 f"G0: {a1['g0_count']:,}")
    lines.append(f"- material pre-variables (|SMD| > 0.20): "
                 f"{', '.join(a1['material_pre_variables']) or 'none'}")
    lines.append(f"- max |SMD|: {_fmt(a1['max_abs_smd'])}; "
                 f"median |SMD|: {_fmt(a1['median_abs_smd'])}")
    lines.append(f"- status: **{a1['status']}** "
                 f"(WARN is informational, never FAIL)")
    lines.append("")
    for c in a1["comparisons"]:
        flag = " (post-inclusive, not in WARN rule)" \
            if not c["pre_first_opposite"] else ""
        lines.append(
            f"  - {c['variable']}: SMD {_fmt(c['smd'])} "
            f"[{c['magnitude']}]{flag}; "
            f"G1 mean {_fmt(c['g1']['mean'])} (n={c['g1']['n']}, "
            f"missing={c['g1']['missing']}) vs "
            f"G0 mean {_fmt(c['g0']['mean'])} (n={c['g0']['n']}, "
            f"missing={c['g0']['missing']})"
        )
    lines.append("")

    lines.append("## 2. BUY-only label sensitivity "
                 "(audit-only directional label)")
    lines.append("")
    a2 = audits["buy_only_sensitivity"]
    lines.append(f"- eligible markets: {a2['n_eligible']:,}")
    lines.append(f"- frozen y30 positive rate: {_pct(a2['original_rate'])}")
    lines.append(f"- directional sensitivity rate: "
                 f"{_pct(a2['sensitivity_rate'])}")
    lines.append(f"- |delta|: {_fmt(a2['delta_pp'])} pp -> "
                 f"**{a2['status']}**")
    lines.append(f"- sell-only upgrades (y30=0 but SELL of initial in "
                 f"window): {a2['n_sell_only_upgrades']:,} "
                 f"({_pct(a2.get('sell_only_share'))} of eligible)")
    lines.append(f"- y30 vs fills consistency errors: "
                 f"{a2['n_consistency_errors']:,} (must be 0)")
    lines.append("")

    lines.append("## 3. Within-page identity collision sensitivity")
    lines.append("")
    a3 = audits["collision_sensitivity"]
    lines.append(f"- pages scanned: {a3['n_pages']:,} "
                 f"({a3['n_records']:,} records, "
                 f"{a3['n_pages_with_collisions']:,} pages with collisions)")
    lines.append(f"- collision identities: {a3['n_collisions']:,}; "
                 f"excess records (dropped as in-page duplicates): "
                 f"{a3['excess_records']:,} (independently re-derived from "
                 f"raw api_pages by this audit)")
    lines.append(f"- clean markets affected: {a3['n_clean_affected']:,} "
                 f"({_pct(a3['share_clean_affected'])}); "
                 f"excluded markets affected: {a3['n_excluded_affected']:,}")
    lines.append(f"- delta y30 rate A vs B: {_fmt(a3['delta_y30_pp'])} pp; "
                 f"delta FirstOpposite rate: "
                 f"{_fmt(a3['delta_first_opp_pp'])} pp -> "
                 f"**{a3['status']}**")
    lines.append(f"- median initial_qty A/B: "
                 f"{_fmt(a3['median_initial_qty_a'])} / "
                 f"{_fmt(a3['median_initial_qty_b'])}")
    if a3.get("concentration"):
        conc = a3["concentration"]
        top_dates = sorted(conc.get("by_utc_date", {}).items(),
                           key=lambda kv: -kv[1])[:5]
        lines.append(
            f"- concentration: side={conc.get('by_side')}, "
            f"outcome={conc.get('by_outcome')}; top UTC dates: "
            + ", ".join(f"{d} ({n})" for d, n in top_dates)
        )
        lines.append(
            f"- labels of affected clean markets: "
            f"{conc.get('affected_clean_labels')}"
        )
    lines.append("")

    lines.append("## 4. Temporal stability (daily / ISO week)")
    lines.append("")
    a4 = audits["temporal_stability"]
    lines.append(f"- weeks: {len(a4['weekly'])} "
                 f"({a4['n_low_n_weeks']} low-N < {a4['min_weekly_n']}, kept "
                 f"but excluded from the rule)")
    lines.append(f"- weekly observable y30 positive rate: "
                 f"min {_pct(a4['rate_min'])}, mean {_pct(a4['rate_mean'])}, "
                 f"max {_pct(a4['rate_max'])}, std {_pct(a4['rate_std'])}")
    lines.append(f"- spread: {_fmt(a4['spread_pp'])} pp; max consecutive "
                 f"week change: {_fmt(a4['max_abs_week_over_week_pp'])} pp")
    if a4.get("extreme_weeks"):
        lines.append(f"- extreme weeks (>2 sigma from weekly mean): "
                     f"{', '.join(a4['extreme_weeks'])}")
    lines.append(f"- status: **{a4['status']}**")
    lines.append("")
    lines.append("| week | clean | first-opp | eligible | y30+ | rate | "
                 "median initial_qty | low-N |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for w in a4["weekly"]:
        lines.append(
            f"| {w['key']} | {w['clean_market_count']:,} | "
            f"{w['first_opposite_count']:,} | {w['y30_observable']:,} | "
            f"{w['y30_positive']:,} | {_pct(w['positive_rate_observable'])} | "
            f"{_fmt(w['median_initial_qty'], 1)} | "
            f"{'yes' if w['low_n'] else ''} |"
        )
    lines.append("")

    lines.append("## 5. Universe placebo (same construction, other scopes)")
    lines.append("")
    a5 = audits["universe_placebo"]
    ref = a5["reference"]
    lines.append(f"- reference {ref['name']}: clean {ref['n_clean']:,}, "
                 f"FirstOpposite rate {_pct(ref['first_opp_rate'])}, "
                 f"y30 observable rate "
                 f"{_pct(ref['y30_positive_rate_observable'])}")
    lines.append("")
    lines.append("| universe | fills | clean | first-opp rate | y30 rate | "
                 "dFO pp | dY30 pp | comparable |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for p in a5["placebos"]:
        lines.append(
            f"| {p['name']} | {p['n_fills']:,} | {p['n_clean']:,} | "
            f"{_pct(p['first_opp_rate'])} | "
            f"{_pct(p['y30_positive_rate_observable'])} | "
            f"{_fmt(p['delta_first_opp_pp'])} | {_fmt(p['delta_y30_pp'])} | "
            f"{'yes' if p['comparable'] else 'NO - ' + str(p['note'])} |"
        )
    lines.append("")

    lines.append("## 6. Negative controls")
    lines.append("")
    a6 = audits["negative_controls"]
    sh = a6["shuffle"]
    lines.append("### 6a. Within-ISO-week label shuffle (leakage control)")
    lines.append("")
    lines.append(f"- samples: {sh['n_samples']:,} (dropped for missing "
                 f"features / censoring: {sh['n_dropped']:,})")
    lines.append(f"- shuffles: {sh['n_shuffles']} (seed {sh['seed']})")
    lines.append(f"- AUC mean {_fmt(sh['auc_mean'])}, std "
                 f"{_fmt(sh['auc_std'])}, p95 {_fmt(sh['auc_p95'])}, "
                 f"max {_fmt(sh['auc_max'])}")
    lines.append(f"- rule: FAIL if p95 > 0.55 -> **{sh['status']}**")
    gd = a6.get("global_shuffle_diagnostic")
    if gd:
        lines.append("")
        lines.append(
            f"- global-shuffle diagnostic (NOT part of the rule): AUC mean "
            f"{_fmt(gd.get('auc_mean'))}, p95 {_fmt(gd.get('auc_p95'))}. "
            "A ~0.5 global-shuffle AUC together with a high within-week "
            "AUC means the predictability comes from between-week base-rate "
            "dispersion combined with time-drifting features - a calendar-"
            "structure effect, not per-sample feature leakage. See audit 4 "
            "for the weekly rate dispersion."
        )
    lines.append("")
    fw = a6["future_window"]
    lines.append("### 6b. Future-window placebo labels")
    lines.append("")
    lines.append("| window | eligible | positives | rate |")
    lines.append("|---|---|---|---|")
    for w in fw["windows"]:
        lines.append(
            f"| (t0+{w['start_offset_s']}s, t0+{w['end_offset_s']}s] | "
            f"{w['n_eligible']:,} | {w['n_positive']:,} | "
            f"{_pct(w['positive_rate'])} |"
        )
    lines.append("")

    lines.append("## Interpretation guardrails")
    lines.append("")
    for i, g in enumerate(INTERPRETATION_GUARDRAILS, 1):
        lines.append(f"{i}. {g}")
    lines.append("")

    lines.append("## Known limitations")
    lines.append("")
    for i, lim in enumerate(KNOWN_LIMITATIONS, 1):
        lines.append(f"{i}. {lim}")
    lines.append("")

    lines.append("## Provenance")
    lines.append("")
    prov = report.get("provenance", {})
    lines.append(f"- command: `{prov.get('command', 'n/a')}`")
    lines.append(f"- n_shuffles: {prov.get('n_shuffles')}, seed: "
                 f"{prov.get('seed')}, min_weekly_n: "
                 f"{prov.get('min_weekly_n')}")
    lines.append(f"- report json: `{prov.get('json_path', '')}`")
    lines.append("")
    lines.append(
        "No Phase 1 definition, label, episode rule, raw page, or setting "
        "was modified by this audit. No trades were requested, signed, or "
        "sent; no private keys were requested or stored."
    )
    return "\n".join(lines) + "\n"
