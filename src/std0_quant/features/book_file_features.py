"""Low-memory Book feature orchestration over explicitly selected raw files."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Any

from std0_quant.features.book_streaming import compute_book_features_streaming
from std0_quant.features.raw_loading import iter_book_rows


def compute_book_features_from_files(
    paths: Iterable[Path | str],
    *,
    condition_id: str,
    cutoff_ms: int,
    opp_outcome: str,
    initial_outcome: str,
    stale_after_ms: int = 5000,
) -> dict[str, Any]:
    """Compute frozen Book features without materializing selected raw files."""
    rows = iter_book_rows(
        paths,
        condition_id=condition_id,
    )

    return compute_book_features_streaming(
        rows,
        cutoff_ms,
        opp_outcome,
        initial_outcome,
        stale_after_ms=stale_after_ms,
    )
