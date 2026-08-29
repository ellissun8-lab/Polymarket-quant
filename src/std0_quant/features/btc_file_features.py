"""Low-memory BTC feature orchestration over explicitly selected raw files."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Any

from std0_quant.features.btc_streaming import compute_btc_features_streaming
from std0_quant.features.raw_loading import iter_btc_rows


def compute_btc_features_from_files(
    paths: Iterable[Path | str],
    *,
    market_start_ms: int,
    cutoff_ms: int,
) -> dict[str, Any]:
    """Compute frozen BTC features without materializing selected raw files."""
    rows = iter_btc_rows(paths)

    return compute_btc_features_streaming(
        rows,
        market_start_ms,
        cutoff_ms,
    )
