"""Emit the Phase 2B v3 interpretation-precision erratum (v3.1 spec sections 1-3).

The frozen v3 artifacts stay IMMUTABLE; this appends a standalone erratum
that corrects exactly one interpretation sentence. Data, metrics and the
research spec version are unchanged - only the interpretation wording is
corrected.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from std0_quant.collectors.live_storage import streaming_sha256  # noqa: E402

CLASSIFICATION = "INTERPRETATION_PRECISION_CORRECTION"

ORIGINAL_STATEMENT = (
    "BTC generally leads PM within roughly 0-2s on these 3 markets; "
    "direction consistent, magnitude not timing-resolved."
)

REASON_FOR_CORRECTION = (
    "The frozen v3 timing audit itself measured an overall minimum resolvable "
    "lag of 10323 ms. A 'within roughly 0-2s' statement is therefore ALSO "
    "below the current timing resolution: 0-2s cannot be supported as a "
    "timing magnitude conclusion any more than 250ms can. Only direction "
    "consistency is supported; the lag magnitude is unresolved."
)

CORRECTED_STATEMENT_EN = (
    "Across the first three eligible prospective_v4 markets, all three clock "
    "views classify the dominant association as BTC_LEAD. However, the "
    "measured positive lags are below the current timing-resolution bound, "
    "so the lag magnitude is unresolved."
)

CORRECTED_STATEMENT_CN = (
    "前三个 eligible prospective_v4 市场中,多个时钟视图均显示方向上 "
    "BTC_LEAD;但测得的正 lag 均低于当前 timing-resolution bound,因此目前"
    "只能支持方向一致性,不能识别具体滞后量级。"
)


def _artifact_ref(path: Path) -> dict | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {"artifact": str(path.name), "run_id": payload.get("run_id"),
            "sha256": streaming_sha256(path),
            "research_spec_version": payload.get("research_spec_version")}


def build_erratum(reports_dir: Path, v3_run_id: str) -> dict:
    audit = reports_dir / f"phase2b_timing_audit_{v3_run_id}.json"
    research = reports_dir / f"phase2b_research_v3_{v3_run_id}.json"
    audit_ref = _artifact_ref(audit)
    research_ref = _artifact_ref(research)
    if audit_ref is None and research_ref is None:
        raise SystemExit(f"no v3 artifacts found for run {v3_run_id} in {reports_dir}")
    return {
        "classification": CLASSIFICATION,
        "erratum_for_research_spec": "phase2b_research_v3",
        "v3_run_id": v3_run_id,
        "original_statement": ORIGINAL_STATEMENT,
        "where_it_appeared": [
            "README.md v3 section 'Honest summary' (pre-erratum wording)",
            "v3 acceptance report final summary",
        ],
        "reason_for_correction": REASON_FOR_CORRECTION,
        "corrected_statement_en": CORRECTED_STATEMENT_EN,
        "corrected_statement_cn": CORRECTED_STATEMENT_CN,
        "corrected_interpretation": ["DIRECTION_REPLICATED_EARLY",
                                     "LAG_MAGNITUDE_UNRESOLVED"],
        "data_unchanged": True,
        "metrics_unchanged": True,
        "research_spec_unchanged": True,
        "v2_reassessment_unchanged": "DIRECTIONALLY_ROBUST_BUT_LAG_UNRESOLVED",
        "immutable_artifacts_referenced": [r for r in (audit_ref, research_ref) if r],
        "evidence": {
            "overall_minimum_resolvable_lag_ms": 10323,
            "observed_peak_lags_ms": [250, 500],
            "rule": ("peak_lag_ms < timing_resolution_ms => "
                     "lag_magnitude_status = UNRESOLVED; numeric peaks are "
                     "descriptive statistics only"),
        },
        "statement_sha256": hashlib.sha256(
            ORIGINAL_STATEMENT.encode("utf-8")).hexdigest(),
        "no_real_trading": True,
    }


def emit_erratum(reports_dir: Path, v3_run_id: str,
                 timestamp: str | None = None) -> tuple[Path | None, dict]:
    erratum = build_erratum(reports_dir, v3_run_id)
    # idempotent: one erratum per corrected statement
    for existing in sorted(reports_dir.glob(
            "phase2b_v3_interpretation_erratum_*.json")):
        try:
            prior = json.loads(existing.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if prior.get("statement_sha256") == erratum["statement_sha256"]:
            return None, erratum
    stamp = timestamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = reports_dir / f"phase2b_v3_interpretation_erratum_{stamp}.json"
    tmp = path.with_name(path.name + f".{stamp}.tmp")
    tmp.write_text(json.dumps(erratum, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(path)
    lines = [
        "# Phase 2B v3 Interpretation Erratum",
        "",
        f"- Classification: **{erratum['classification']}** (run "
        f"`{erratum['v3_run_id']}`)",
        f"- Original statement: \"{erratum['original_statement']}\"",
        f"- Reason: {erratum['reason_for_correction']}",
        f"- Corrected statement (EN): {erratum['corrected_statement_en']}",
        "",
        f"- 修正后表述(中文):{erratum['corrected_statement_cn']}",
        f"- Corrected interpretation: **{' + '.join(erratum['corrected_interpretation'])}**",
        f"- v2 reassessment unchanged: **{erratum['v2_reassessment_unchanged']}**",
        "- Data unchanged: True; metrics unchanged: True; research spec "
        "unchanged: True - only interpretation wording corrected.",
        "- The immutable v3 reports "
        f"(`phase2b_timing_audit_{v3_run_id}` / "
        f"`phase2b_research_v3_{v3_run_id}`) were NOT modified.",
        "",
        "RESEARCH ONLY - NO REAL TRADING.",
        "",
    ]
    path.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")
    return path, erratum


def main() -> int:
    reports = ROOT / "data" / "reports"
    path, erratum = emit_erratum(reports, "20260824T193505Z")
    if path is None:
        print("erratum already exists for this statement; nothing written")
        return 0
    print(json.dumps({"status": "WRITTEN", "path": str(path),
                      "classification": erratum["classification"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
