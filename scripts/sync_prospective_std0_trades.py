"""Strict prospective-only std0 trade synchronization.

Writes to physically isolated prospective_v4 stores and never invokes the
legacy Std0TradesSyncer.

Examples
--------
python scripts/sync_prospective_std0_trades.py \
    --start 1787590800000

python scripts/sync_prospective_std0_trades.py \
    --start 1787590800000 \
    --end 1787677200000
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from std0_quant.collectors.prospective_std0_trades import (
    ProspectiveTradesSyncer,
)
from std0_quant.collectors.std0_trades import RetryingClient
from std0_quant.config import load_settings, resolve_path
from std0_quant.storage import AppendOnlyNDJSON, RawPageStore, SqliteState
from std0_quant.timeutil import parse_ts_to_ms, utc_now_ms


STORE_VERSION = "prospective_v4"


def parse_time_arg(value: str) -> int:
    parsed = parse_ts_to_ms(value)
    if parsed is None:
        raise argparse.ArgumentTypeError(
            f"cannot parse timestamp: {value!r}"
        )
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--start",
        type=parse_time_arg,
        required=True,
        help="strict inclusive public-time start (epoch s/ms or ISO-8601)",
    )
    parser.add_argument(
        "--end",
        type=parse_time_arg,
        default=None,
        help="strict inclusive public-time end; default is current UTC time",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    args = build_parser().parse_args(argv)
    end_ms = args.end if args.end is not None else utc_now_ms()

    if end_ms < args.start:
        print("end must be >= start")
        return 2

    settings = load_settings()

    raw_dir = (
        resolve_path(settings, "raw_std0_trades")
        / STORE_VERSION
    )
    pages_dir = (
        resolve_path(settings, "raw_api_pages")
        / STORE_VERSION
    )
    state_dir = (
        resolve_path(settings, "state")
        / STORE_VERSION
    )

    raw_path = raw_dir / "trades.ndjson"
    state_path = state_dir / "sync_state.db"

    client = RetryingClient(
        settings.polymarket.data_api_base,
        max_retries=settings.polymarket.request_max_retries,
        backoff_base_seconds=(
            settings.polymarket.request_backoff_base_seconds
        ),
    )

    with SqliteState(state_path) as state:
        with AppendOnlyNDJSON(raw_path) as raw_writer:
            syncer = ProspectiveTradesSyncer(
                settings,
                state,
                raw_writer,
                RawPageStore(pages_dir),
                client=client,
            )
            result = syncer.sync_range(
                start_ms=args.start,
                end_ms=end_ms,
            )

    payload = {
        "store_version": STORE_VERSION,
        "status": result.status,
        "start_ms": args.start,
        "end_ms": end_ms,
        "windows": result.windows,
        "pages_fetched": result.pages_fetched,
        "records_fetched": result.records_fetched,
        "new_trades": result.new_trades,
        "duplicates_skipped": result.duplicates_skipped,
        "raw_path": str(raw_path),
        "state_path": str(state_path),
        "pages_dir": str(pages_dir),
        "messages": result.messages,
    }
    print(json.dumps(payload, indent=2))

    return 0 if result.status == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
