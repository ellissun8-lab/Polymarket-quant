"""Build a bounded FileCoverageProvider for one prospective market."""
from __future__ import annotations

from pathlib import Path

from std0_quant.audit.coverage import FileCoverageProvider
from std0_quant.features.prospective_coverage import (
    MarketCoverageSources,
)


def build_market_coverage_provider(
    sources: MarketCoverageSources,
    *,
    book_dir: Path | str,
    btc_dir: Path | str,
    sessions_dir: Path | str,
    bucket_seconds: float,
    gap_threshold_seconds: float,
    book_stale_seconds: float = 5.0,
) -> FileCoverageProvider:
    """Create a provider restricted to one already-validated source set.

    Caller must supply an ELIGIBLE MarketCoverageSources.  Ambiguous or missing
    source selections fail closed here instead of falling back to full rglob.
    """
    if sources.status != "ELIGIBLE":
        raise ValueError(
            "refusing to build coverage provider from ineligible sources: "
            + ";".join(sources.reasons)
        )

    if not sources.book_files:
        raise ValueError("eligible sources must contain book_files")
    if not sources.btc_files:
        raise ValueError("eligible sources must contain btc_files")

    return FileCoverageProvider(
        book_dir=book_dir,
        btc_dir=btc_dir,
        sessions_dir=sessions_dir,
        bucket_seconds=bucket_seconds,
        gap_threshold_seconds=gap_threshold_seconds,
        book_stale_seconds=book_stale_seconds,
        book_files=list(sources.book_files),
        btc_files=list(sources.btc_files),
    )
