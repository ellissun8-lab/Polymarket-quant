"""CLI: build the event ledger (one row per market) from raw fills.

Outputs:

* ``data/derived/event_ledger.parquet`` (and ``.csv`` for quick inspection)
* ``data/reports/reconciliation_<stamp>.{json,md}``
* ``data/reports/coverage_<stamp>.json`` (per-market book/BTC coverage)

Market windows come from the market slug (``btc-updown-5m-<unix_start_s>``,
verified live: window = [ts, ts+300s); gamma startDate is the CREATION time
and cannot serve as window start). Markets whose slug does not match the
study universe are excluded loudly (OTHER + detail), never dropped, so
reconciliation balances over ALL raw fills including non-BTC series.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(_PROJECT_SRC))

from std0_quant.audit.coverage import (  # noqa: E402
    FileCoverageProvider,
    write_json_report,
)
from std0_quant.audit.reconciliation import (  # noqa: E402
    assert_reconciles,
    build_reconciliation,
    write_reconciliation_report,
)
from std0_quant.config import load_settings, resolve_path  # noqa: E402
from std0_quant.events.event_ledger import (  # noqa: E402
    GammaMarketMetadataProvider,
    SlugWindowMetadataProvider,
    build_ledger_rows,
)
from std0_quant.events.fills import load_fills  # noqa: E402
from std0_quant.logging_setup import setup_logging  # noqa: E402
from std0_quant.storage import write_parquet  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true",
                        help="kept for compatibility; the slug-based provider "
                             "works fully offline in every mode")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    settings = load_settings()
    log_path = setup_logging(resolve_path(settings, "logs"), "build_event_ledger")

    raw_dir = resolve_path(settings, "raw_std0_trades")
    raw_files = sorted(raw_dir.glob("*.ndjson"))
    if not raw_files:
        print(f"no raw trade files under {raw_dir}; run scripts/sync_std0_trades.py first")
        return 1

    fills = list(load_fills(raw_files, keep_raw_json=False))
    if not fills:
        print("raw store is empty")
        return 1

    # Window metadata from slugs (offline, deterministic; gamma cannot serve
    # closed markets -- verified live -- so slug derivation is the primary
    # source). Non-BTC-5m markets are excluded by scope before metadata.
    book_cfg = settings.polymarket.book
    metadata_provider = SlugWindowMetadataProvider.from_fills(
        fills,
        slug_prefix=book_cfg.market_slug_prefix,
        window_seconds=book_cfg.market_window_seconds,
    )
    coverage_provider = FileCoverageProvider(
        book_dir=resolve_path(settings, "raw_polymarket_book"),
        btc_dir=resolve_path(settings, "raw_btc_ticks"),
        sessions_dir=resolve_path(settings, "sessions"),
        bucket_seconds=settings.coverage.bucket_seconds,
        gap_threshold_seconds=settings.coverage.gap_threshold_seconds,
    )

    rows = build_ledger_rows(
        fills, metadata_provider, coverage_provider,
        scope_slug_prefix=book_cfg.market_slug_prefix,
    )
    if not rows:
        print("ledger is empty")
        return 1
    unresolved = [
        row["condition_id"] for row in rows
        if row["exclude_reason"] == "MARKET_METADATA_MISSING"
    ]
    if unresolved:
        print(f"markets with unusable window metadata: {len(unresolved)} "
              "(slug does not encode a valid window; see ledger)")

    derived_dir = resolve_path(settings, "derived")
    ledger_path = write_parquet(rows, derived_dir / "event_ledger.parquet")
    csv_path = derived_dir / "event_ledger.csv"
    fieldnames = list(rows[0].keys())
    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    # -- reports ------------------------------------------------------------
    raw_condition_ids = {f.condition_id for f in fills if f.condition_id}
    buy_condition_ids = {
        f.condition_id for f in fills if f.condition_id and f.is_buy
    }
    report = build_reconciliation(raw_condition_ids, buy_condition_ids, rows)
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    write_reconciliation_report(report, resolve_path(settings, "reports"), stamp)

    coverage_reports = []
    for row in rows:
        if row.get("market_start_ms") and row.get("market_end_ms"):
            coverage_reports.append(coverage_provider.market_report(
                row["condition_id"], row["market_start_ms"], row["market_end_ms"]
            ))
    write_json_report(
        {"generated_at_stamp": stamp, "markets": coverage_reports},
        resolve_path(settings, "reports") / f"coverage_{stamp}.json",
    )

    print(f"ledger rows:          {len(rows)}")
    print(f"clean markets:        {report.clean_markets}")
    print(f"excluded markets:     {report.excluded_markets}")
    for reason, count in sorted(report.exclude_reason_counts.items()):
        print(f"  {reason}: {count}")
    print(f"markets with FirstOpposite: {report.markets_with_first_opp}")
    print(f"y30 positives / negatives / censored: "
          f"{report.y30_positives} / {report.y30_negatives} / {report.y30_censored}")
    if report.problems:
        print("RECONCILIATION PROBLEMS (see report):")
        for problem in report.problems:
            print(f"  - {problem}")
        return 2
    assert_reconciles(report)
    print(f"reconciliation:       OK (raw {report.raw_markets} == "
          f"clean {report.clean_markets} + excluded {report.excluded_markets})")
    print(f"ledger parquet:       {ledger_path}")
    print(f"ledger csv:           {csv_path}")
    print(f"log:                  {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
