"""Build isolated prospective derived rows without touching historical outputs."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from std0_quant.events.episode_builder import build_episodes, episode_to_row
from std0_quant.events.event_ledger import (
    SlugWindowMetadataProvider,
    build_ledger_rows,
)
from std0_quant.events.fills import fill_to_row, load_fills
from std0_quant.features.prospective_coverage import (
    ProspectiveCoverageSelector,
)
from std0_quant.features.prospective_coverage_provider import (
    build_market_coverage_provider,
)


@dataclass
class ProspectiveDerivedRows:
    fill_rows: list[dict[str, Any]]
    episode_rows: list[dict[str, Any]]
    ledger_rows: list[dict[str, Any]]
    coverage_selection_rows: list[dict[str, Any]]


def build_prospective_derived_rows(
    *,
    raw_path: Path | str,
    book_dir: Path | str,
    btc_dir: Path | str,
    sessions_dir: Path | str,
    slug_prefix: str,
    market_window_seconds: int,
    coverage_bucket_seconds: float,
    coverage_gap_threshold_seconds: float,
    book_stale_seconds: float = 5.0,
) -> ProspectiveDerivedRows:
    """Build prospective-only fills, episodes, ledger, and source eligibility.

    Coverage source ambiguity/missing evidence is recorded separately and never
    resolved by stitching sessions. Ledger truth definitions remain unchanged.
    """
    raw_path = Path(raw_path)

    if not raw_path.is_file():
        raise FileNotFoundError(
            f"prospective raw trade store missing: {raw_path}"
        )

    fills = sorted(
        load_fills(raw_path, keep_raw_json=False),
        key=lambda f: (
            f.timestamp_ms if f.timestamp_ms is not None else 0,
            f.condition_id or "",
            f.fill_id,
        ),
    )

    if not fills:
        raise ValueError("prospective raw trade store is empty")

    fill_rows = [fill_to_row(fill) for fill in fills]

    episode_result = build_episodes(fills)
    episode_rows = [
        episode_to_row(episode)
        for episode in episode_result.episodes
    ]

    metadata_provider = SlugWindowMetadataProvider.from_fills(
        fills,
        slug_prefix=slug_prefix,
        window_seconds=market_window_seconds,
    )

    coverage_selector = ProspectiveCoverageSelector.from_paths(
        sessions_dir=sessions_dir,
        book_dir=book_dir,
        btc_dir=btc_dir,
    )

    by_market = defaultdict(list)
    for fill in fills:
        if fill.condition_id is not None:
            by_market[fill.condition_id].append(fill)

    ledger_rows: list[dict[str, Any]] = []
    selection_rows: list[dict[str, Any]] = []

    for condition_id in sorted(by_market):
        market_fills = by_market[condition_id]
        meta = metadata_provider.get(condition_id)

        coverage_provider = None

        if (
            meta is not None
            and meta.market_start_ms is not None
            and meta.market_end_ms is not None
        ):
            sources = coverage_selector.select(
                condition_id=condition_id,
                market_start_ms=meta.market_start_ms,
                market_end_ms=meta.market_end_ms,
            )

            selection_rows.append(
                {
                    "condition_id": condition_id,
                    "status": sources.status,
                    "book_session_id": sources.book_session_id,
                    "btc_session_id": sources.btc_session_id,
                    "book_files": [str(path) for path in sources.book_files],
                    "btc_files": [str(path) for path in sources.btc_files],
                    "reasons": list(sources.reasons),
                }
            )

            if sources.status == "ELIGIBLE":
                coverage_provider = build_market_coverage_provider(
                    sources,
                    book_dir=book_dir,
                    btc_dir=btc_dir,
                    sessions_dir=sessions_dir,
                    bucket_seconds=coverage_bucket_seconds,
                    gap_threshold_seconds=coverage_gap_threshold_seconds,
                    book_stale_seconds=book_stale_seconds,
                )
        else:
            selection_rows.append(
                {
                    "condition_id": condition_id,
                    "status": "INELIGIBLE",
                    "book_session_id": None,
                    "btc_session_id": None,
                    "book_files": [],
                    "btc_files": [],
                    "reasons": [
                        "market_metadata_unavailable_for_coverage_selection"
                    ],
                }
            )

        rows = build_ledger_rows(
            market_fills,
            metadata_provider,
            coverage_provider,
            scope_slug_prefix=slug_prefix,
        )

        if len(rows) != 1:
            raise AssertionError(
                f"expected one prospective ledger row for {condition_id}, "
                f"got {len(rows)}"
            )

        ledger_rows.extend(rows)

    ledger_rows.sort(key=lambda row: str(row["condition_id"]))
    selection_rows.sort(key=lambda row: str(row["condition_id"]))

    return ProspectiveDerivedRows(
        fill_rows=fill_rows,
        episode_rows=episode_rows,
        ledger_rows=ledger_rows,
        coverage_selection_rows=selection_rows,
    )
