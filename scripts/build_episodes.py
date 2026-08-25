"""CLI: build parent episodes (and normalized fills) from raw std0 trades.

Reads the append-only raw store and writes deterministic derived outputs:

* ``data/normalized/fills.parquet``   -- all fills (BUY + SELL), no raw_json
  (the raw NDJSON stays the single source of truth; rows reference
  ``fill_id``);
* ``data/derived/episodes.parquet``   -- parent episodes (rule v1_3sec).

Derived data is fully rebuildable: delete the parquet files and re-run.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(_PROJECT_SRC))

from std0_quant.config import load_settings, resolve_path  # noqa: E402
from std0_quant.events.episode_builder import episode_to_row  # noqa: E402
from std0_quant.events.fills import fill_to_row, load_fills  # noqa: E402
from std0_quant.logging_setup import setup_logging  # noqa: E402
from std0_quant.storage import write_parquet  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    build_parser().parse_args(argv)
    settings = load_settings()
    log_path = setup_logging(resolve_path(settings, "logs"), "build_episodes")

    raw_dir = resolve_path(settings, "raw_std0_trades")
    raw_files = sorted(raw_dir.glob("*.ndjson"))
    if not raw_files:
        print(f"no raw trade files under {raw_dir}; run scripts/sync_std0_trades.py first")
        return 1

    # Deterministic ordering: sort by (timestamp_ms, condition_id, fill_id).
    # Lean loading (no raw_json): millions of fills fit in memory; the raw
    # NDJSON remains the single source of truth.
    fills = sorted(
        load_fills(raw_files, keep_raw_json=False),
        key=lambda f: (f.timestamp_ms if f.timestamp_ms is not None else 0,
                       f.condition_id or "", f.fill_id),
    )
    fill_rows = [fill_to_row(f) for f in fills]

    from std0_quant.events.episode_builder import build_episodes

    result = build_episodes(fills)
    episode_rows = [episode_to_row(e) for e in result.episodes]

    fills_path = write_parquet(fill_rows, resolve_path(settings, "normalized") / "fills.parquet")
    episodes_path = write_parquet(
        episode_rows, resolve_path(settings, "derived") / "episodes.parquet"
    )

    n_buy = sum(1 for f in fills if f.is_buy)
    print(f"raw files:            {len(raw_files)}")
    print(f"fills (buy/sell/all): {n_buy}/{len(fills) - n_buy}/{len(fills)}")
    print(f"episodes:             {len(episode_rows)} (rule {settings.episode.rule})")
    print(f"invalid BUY fills:    {len(result.invalid_buy_fills)} "
          "(flagged FIELD_INCOMPLETE/TIMESTAMP_INVALID in the ledger)")
    print(f"fills parquet:        {fills_path}")
    print(f"episodes parquet:     {episodes_path}")
    print(f"log:                  {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
