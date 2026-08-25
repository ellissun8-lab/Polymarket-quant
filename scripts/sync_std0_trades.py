"""CLI entry point: incremental sync of std0 trades from the Polymarket data API.

Examples
--------
    python scripts/sync_std0_trades.py                     # incremental
    python scripts/sync_std0_trades.py --full              # page entire history
    python scripts/sync_std0_trades.py --start 2026-08-01 --end 2026-08-08 --use-time-params
    python scripts/sync_std0_trades.py --backfill --start 2026-03-01  # deep history
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(_PROJECT_SRC))

from std0_quant.collectors.std0_trades import Std0TradesSyncer  # noqa: E402
from std0_quant.config import load_settings, resolve_path  # noqa: E402
from std0_quant.logging_setup import setup_logging  # noqa: E402
from std0_quant.storage import (  # noqa: E402
    AppendOnlyNDJSON,
    RawPageStore,
    SqliteState,
    new_run_id,
)
from std0_quant.timeutil import parse_ts_to_ms  # noqa: E402


def parse_time_arg(value: str) -> int:
    parsed = parse_ts_to_ms(value)
    if parsed is None:
        raise argparse.ArgumentTypeError(f"cannot parse timestamp: {value!r}")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=parse_time_arg, default=None,
                        help="window start (epoch s/ms or ISO-8601)")
    parser.add_argument("--end", type=parse_time_arg, default=None,
                        help="window end (epoch s/ms or ISO-8601)")
    parser.add_argument("--use-time-params", action="store_true",
                        help="pass start/end to the API (verified on first page; "
                             "abort if the API ignores them)")
    parser.add_argument("--full", action="store_true",
                        help="page through full history instead of incremental early stop")
    parser.add_argument("--backfill", action="store_true",
                        help="deep-history mode: repeatedly slice the window "
                             "(start, end] past the API's 10000-offset cap by "
                             "shrinking end to earliest-seen minus 1s; requires "
                             "--start (and optionally --end, default: now)")
    parser.add_argument("--taker-only", dest="taker_only", action="store_true", default=None,
                        help="override configured takerOnly=true")
    parser.add_argument("--no-taker-only", dest="taker_only", action="store_false",
                        help="override configured takerOnly=false")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    settings = load_settings()
    log_path = setup_logging(resolve_path(settings, "logs"), "sync_std0_trades")

    if args.taker_only is not None:
        settings.polymarket.sync.taker_only = args.taker_only

    raw_dir = resolve_path(settings, "raw_std0_trades")
    run_id = new_run_id("sync-trades")
    with SqliteState(resolve_path(settings, "state") / "sync_state.db") as state:
        with AppendOnlyNDJSON(raw_dir / "trades.ndjson") as raw_writer:
            page_store = RawPageStore(resolve_path(settings, "raw_api_pages"))
            syncer = Std0TradesSyncer(
                settings, state, raw_writer, page_store,
                run_id=None if args.backfill else run_id,
            )
            if args.backfill:
                if args.start is None:
                    print("--backfill requires --start (epoch s/ms or ISO-8601)")
                    return 2
                from std0_quant.timeutil import utc_now_ms

                end_ms = args.end if args.end is not None else utc_now_ms()
                result = syncer.sync_backfill(start_ms=args.start, end_ms=end_ms)
                print(f"windows: {result.windows}  (first run_id: {result.first_run_id})")
                print(f"status: {result.status}")
                print(f"pages: {result.pages_fetched}  records: {result.records_fetched}")
                print(f"new_trades: {result.new_trades}  "
                      f"duplicates_skipped: {result.duplicates_skipped}")
                print(f"within_page_identity_collisions: "
                      f"{result.within_page_identity_collisions}")
                if result.earliest_ts_ms is not None:
                    print(f"earliest trade ts (ms): {result.earliest_ts_ms}")
                for message in result.messages:
                    print(f"note: {message}")
                print(f"log: {log_path}")
                return 0 if result.status == "complete" else 2
            result = syncer.sync(
                start_ms=args.start,
                end_ms=args.end,
                use_time_params=args.use_time_params,
                full=args.full,
            )
    print(f"run_id: {result.run_id}")
    print(f"status: {result.status}")
    print(f"pages: {result.pages_fetched}  records: {result.records_fetched}")
    print(f"new_trades: {result.new_trades}  duplicates_skipped: {result.duplicates_skipped}")
    print(f"within_page_identity_collisions: {result.within_page_identity_collisions}")
    for message in result.messages:
        print(f"note: {message}")
    print(f"log: {log_path}")
    return 0 if result.status in ("complete", "empty") else 2


if __name__ == "__main__":
    raise SystemExit(main())
