"""Generate versioned coverage evidence from recorded session files."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from std0_quant.audit.coverage import (  # noqa: E402
    FileCoverageProvider, load_sessions, session_overlaps,
)
from std0_quant.audit.coverage_evidence import (  # noqa: E402
    COVERAGE_EVIDENCE_VERSION, COVERAGE_SELECTION_FIX_VERSION,
    FINAL_PASS_OR_FAIL, PENDING_ACTIVE_SOURCE_FILE, SOURCE_NOT_CAPTURED,
    classify_source_evidence, formal_coverage_eligible,
    source_file_overlaps_window,
    write_immutable_evidence,
)
from std0_quant.config import load_settings, resolve_path  # noqa: E402


def _candidate_files(sessions, condition_id: str, start: int,
                     end: int) -> tuple[list[str], list[str]]:
    book: list[str] = []
    btc: list[str] = []
    for session in sessions:
        files = [(str(event["file"]), int(event.get("timestamp_ms", 0)))
                 for event in session.events
                 if event.get("event") == "file_open" and event.get("file")]
        overlapping_files = [name for name, opened_at in files
                             if source_file_overlaps_window(
                                 name, start, end, opened_at
                             )]
        if (session.kind == "polymarket_book" and
                any(event.get("event") == "subscribed" and
                    event.get("market") == condition_id
                    for event in session.events)):
            book.extend(overlapping_files)
        if (session.kind == "btc_ticks" and
                session_overlaps([session], start, end, "btc_ticks")):
            btc.extend(overlapping_files)
    return sorted(set(book)), sorted(set(btc))


def _provider(settings, book_files: list[str], btc_files: list[str]):
    return FileCoverageProvider(
        resolve_path(settings, "raw_polymarket_book"),
        resolve_path(settings, "raw_btc_ticks"),
        resolve_path(settings, "sessions"),
        settings.coverage.bucket_seconds,
        settings.coverage.gap_threshold_seconds,
        settings.live.book_stale_seconds,
        book_files,
        btc_files,
    )


def _overall_status(book_status: str, btc_status: str) -> str:
    states = {book_status, btc_status}
    if PENDING_ACTIVE_SOURCE_FILE in states:
        return PENDING_ACTIVE_SOURCE_FILE
    if states == {FINAL_PASS_OR_FAIL}:
        return FINAL_PASS_OR_FAIL
    return SOURCE_NOT_CAPTURED


def _finalized_at(book_selection: dict, btc_selection: dict) -> str | None:
    values = list(book_selection["source_closed_at_ms"].values()) + list(
        btc_selection["source_closed_at_ms"].values()
    )
    if not values:
        return None
    return datetime.fromtimestamp(max(values) / 1000, timezone.utc).isoformat()


def build_report(condition_id: str, slug: str,
                 repair_parent: str | None = None) -> dict:
    settings = load_settings()
    match = re.fullmatch(r"btc-updown-5m-(\d+)", slug)
    if not match:
        raise ValueError("invalid BTC5m slug")
    start = int(match.group(1)) * 1000
    end = start + settings.polymarket.book.market_window_seconds * 1000
    sessions = load_sessions(resolve_path(settings, "sessions"))
    book_candidates, btc_candidates = _candidate_files(
        sessions, condition_id, start, end
    )
    book_selection = classify_source_evidence(book_candidates)
    btc_selection = classify_source_evidence(btc_candidates)
    status = _overall_status(
        book_selection["coverage_evidence_status"],
        btc_selection["coverage_evidence_status"],
    )

    provisional = _provider(settings, book_candidates, btc_candidates).market_report(
        condition_id, start, end
    )
    final = _provider(
        settings,
        book_selection["source_files_final"],
        btc_selection["source_files_final"],
    ).market_report(condition_id, start, end)

    book_is_final = book_selection["coverage_evidence_status"] == FINAL_PASS_OR_FAIL
    btc_is_final = btc_selection["coverage_evidence_status"] == FINAL_PASS_OR_FAIL
    report = {
        "condition_id": condition_id,
        "slug": slug,
        "market_start_ms": start,
        "market_end_ms": end,
        "coverage_semantics": "1s fully-valid buckets; 5s bounded book state",
        "coverage_evidence_status": status,
        "coverage_evidence_version": COVERAGE_EVIDENCE_VERSION,
        "coverage_selection_fix_version": COVERAGE_SELECTION_FIX_VERSION,
        "book_coverage_evidence_status":
            book_selection["coverage_evidence_status"],
        "btc_coverage_evidence_status":
            btc_selection["coverage_evidence_status"],
        "book_coverage_pct": final["book_coverage_pct"] if book_is_final else None,
        "btc_coverage_pct": final["btc_coverage_pct"] if btc_is_final else None,
        "provisional_book_coverage_pct": provisional["book_coverage_pct"],
        "provisional_btc_coverage_pct": provisional["btc_coverage_pct"],
        "provisional_coverage_evidence_status": "PROVISIONAL_ACTIVE_FILE"
            if status == PENDING_ACTIVE_SOURCE_FILE else None,
        "book_tokens": final["book_tokens"] if book_is_final else {},
        "btc_n_observations": final["btc_n_observations"] if btc_is_final else None,
        "btc_max_gap_ms": final["btc_max_gap_ms"] if btc_is_final else None,
        "btc_gaps": final["btc_gaps"] if btc_is_final else [],
        "provisional_book_tokens": provisional["book_tokens"],
        "provisional_btc_n_observations": provisional["btc_n_observations"],
        "provisional_btc_max_gap_ms": provisional["btc_max_gap_ms"],
        "provisional_btc_gaps": provisional["btc_gaps"],
        "book_files_candidate": book_candidates,
        "book_files_final": book_selection["source_files_final"],
        "btc_files_candidate": btc_candidates,
        "btc_files_final": btc_selection["source_files_final"],
        "book_files": book_selection["source_files_final"],
        "btc_files": btc_selection["source_files_final"],
        "source_files_pending": sorted(
            book_selection["source_files_pending"] +
            btc_selection["source_files_pending"]
        ),
        "source_integrity_failures": {
            **book_selection["source_integrity_failures"],
            **btc_selection["source_integrity_failures"],
        },
        "source_sha256": {
            **book_selection["source_sha256"],
            **btc_selection["source_sha256"],
        },
        "coverage_finalized_at": _finalized_at(book_selection, btc_selection)
            if status == FINAL_PASS_OR_FAIL else None,
        "coverage_repair_parent_artifact": repair_parent,
    }
    report["formal_primary_eligibility_allowed"] = formal_coverage_eligible(report)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--condition-id", required=True)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--repair-parent")
    args = parser.parse_args(argv)
    report = build_report(args.condition_id, args.slug, args.repair_parent)
    settings = load_settings()
    reports = resolve_path(settings, "reports") / "coverage"
    if args.repair_parent:
        fingerprint = hashlib.sha256(json.dumps(
            report, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")).hexdigest()[:16]
        path = reports / f"{args.slug}_repair_{fingerprint}_{COVERAGE_EVIDENCE_VERSION}.json"
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = reports / f"{args.slug}_{stamp}_{COVERAGE_EVIDENCE_VERSION}.json"
    write_immutable_evidence(path, report)
    print(json.dumps({
        "path": str(path),
        "coverage_evidence_status": report["coverage_evidence_status"],
        "btc_coverage_pct": report["btc_coverage_pct"],
        "provisional_btc_coverage_pct": report["provisional_btc_coverage_pct"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
