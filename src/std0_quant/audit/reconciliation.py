"""Reconciliation report (spec Test H).

Verifies that nothing was lost between the raw fill store and the event
ledger:

* every market present in raw fills has exactly one ledger row;
* ``clean_markets + excluded_markets == raw_markets``;
* ``sum(exclude_reason counts) == excluded_markets``;
* every excluded row carries a reason (and ``OTHER`` rows carry details).

Any violation raises :class:`ReconciliationError` -- reconciliation is an
assertion, not a best-effort report.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from std0_quant import EXCLUDE_OTHER
from std0_quant.audit.coverage import write_json_report

logger = logging.getLogger(__name__)


class ReconciliationError(RuntimeError):
    """Raised when raw data and the event ledger do not reconcile."""


@dataclass
class ReconciliationReport:
    raw_markets: int
    markets_with_trades: int
    markets_with_buy_trades: int
    markets_with_first_opp: int
    clean_markets: int
    excluded_markets: int
    exclude_reason_counts: dict[str, int] = field(default_factory=dict)
    y30_positives: int = 0
    y30_negatives: int = 0
    y30_censored: int = 0
    ledger_rows: int = 0
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_markets": self.raw_markets,
            "markets_with_trades": self.markets_with_trades,
            "markets_with_buy_trades": self.markets_with_buy_trades,
            "markets_with_first_opp": self.markets_with_first_opp,
            "clean_markets": self.clean_markets,
            "excluded_markets": self.excluded_markets,
            "exclude_reason_counts": dict(sorted(self.exclude_reason_counts.items())),
            "y30_positives": self.y30_positives,
            "y30_negatives": self.y30_negatives,
            "y30_censored": self.y30_censored,
            "ledger_rows": self.ledger_rows,
            "problems": self.problems,
        }


def build_reconciliation(
    raw_condition_ids: set[str],
    buy_condition_ids: set[str],
    ledger_rows: list[dict[str, Any]],
) -> ReconciliationReport:
    """Compare raw market universe against the ledger. Collects problems."""
    reason_counts: Counter[str] = Counter()
    clean = 0
    excluded = 0
    first_opp = 0
    y30_pos = y30_neg = y30_censored = 0
    seen_conditions: set[str] = set()
    problems: list[str] = []

    for row in ledger_rows:
        condition_id = row.get("condition_id")
        if condition_id in seen_conditions:
            problems.append(f"duplicate ledger row for {condition_id}")
        seen_conditions.add(condition_id)
        if row.get("clean_flag"):
            clean += 1
        else:
            excluded += 1
            reason = row.get("exclude_reason")
            if not reason:
                problems.append(f"excluded market {condition_id} has no exclude_reason")
            else:
                reason_counts[reason] += 1
            if reason == EXCLUDE_OTHER and not row.get("exclude_detail"):
                problems.append(f"{condition_id}: OTHER exclusion missing detail")
        if row.get("first_opp_start_ms") is not None:
            first_opp += 1
        if row.get("y30") == 1:
            y30_pos += 1
        elif row.get("y30") == 0:
            if row.get("y30_horizon_eligible") is False:
                y30_censored += 1
            else:
                y30_neg += 1

    raw_markets = len(raw_condition_ids)
    if seen_conditions != raw_condition_ids:
        missing = raw_condition_ids - seen_conditions
        extra = seen_conditions - raw_condition_ids
        if missing:
            problems.append(f"markets in raw with no ledger row: {sorted(missing)[:10]}")
        if extra:
            problems.append(f"ledger rows with no raw market: {sorted(extra)[:10]}")
    if clean + excluded != len(ledger_rows):
        problems.append("clean + excluded != ledger rows")
    if clean + excluded != raw_markets:
        problems.append(
            f"clean({clean}) + excluded({excluded}) != raw_markets({raw_markets})"
        )
    if sum(reason_counts.values()) != excluded:
        problems.append("exclude reason counts do not sum to excluded_markets")

    return ReconciliationReport(
        raw_markets=raw_markets,
        markets_with_trades=len(raw_condition_ids),
        markets_with_buy_trades=len(buy_condition_ids),
        markets_with_first_opp=first_opp,
        clean_markets=clean,
        excluded_markets=excluded,
        exclude_reason_counts=dict(reason_counts),
        y30_positives=y30_pos,
        y30_negatives=y30_neg,
        y30_censored=y30_censored,
        ledger_rows=len(ledger_rows),
        problems=problems,
    )


def assert_reconciles(report: ReconciliationReport) -> None:
    if report.problems:
        raise ReconciliationError(
            "reconciliation failed:\n  - " + "\n  - ".join(report.problems)
        )


def write_reconciliation_report(
    report: ReconciliationReport, reports_dir: Path | str, stamp: str
) -> Path:
    """Write reconciliation JSON + human-readable markdown."""
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    write_json_report(report.to_dict(), reports_dir / f"reconciliation_{stamp}.json")

    lines = [
        "# Reconciliation report",
        "",
        f"- raw markets: {report.raw_markets}",
        f"- markets with trades: {report.markets_with_trades}",
        f"- markets with BUY trades: {report.markets_with_buy_trades}",
        f"- markets with FirstOpposite: {report.markets_with_first_opp}",
        f"- clean markets: {report.clean_markets}",
        f"- excluded markets: {report.excluded_markets}",
        "",
        "## Exclude reasons",
        "",
    ]
    if report.exclude_reason_counts:
        for reason, count in sorted(report.exclude_reason_counts.items()):
            lines.append(f"- {reason}: {count}")
    else:
        lines.append("- (none)")
    lines += [
        "",
        "## Y30 (markets with FirstOpposite)",
        "",
        f"- positives: {report.y30_positives}",
        f"- negatives (horizon eligible): {report.y30_negatives}",
        f"- censored (horizon ineligible): {report.y30_censored}",
        "",
        "## Problems",
        "",
    ]
    lines += [f"- {p}" for p in report.problems] or ["- none"]
    path = reports_dir / f"reconciliation_{stamp}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
