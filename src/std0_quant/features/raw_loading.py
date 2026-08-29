"""Streaming access to explicitly selected immutable raw NDJSON files."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Iterator, Any

from std0_quant.storage import read_ndjson


def iter_raw_rows(
    paths: Iterable[Path | str],
) -> Iterator[dict[str, Any]]:
    """Yield raw records one at a time and preserve file provenance."""
    for item in paths:
        path = Path(item)
        for row in read_ndjson(path):
            record = dict(row)
            record["_source_file"] = str(path)
            yield record


def iter_book_rows(
    paths: Iterable[Path | str],
    *,
    condition_id: str,
) -> Iterator[dict[str, Any]]:
    """Stream one book market; reject cross-market contamination loudly."""
    expected = str(condition_id)

    for row in iter_raw_rows(paths):
        actual = row.get("condition_id")

        if actual is not None and str(actual) != expected:
            raise ValueError(
                f"book session contains unexpected condition_id "
                f"{actual!r}; expected {expected!r}"
            )

        yield row


def iter_btc_rows(
    paths: Iterable[Path | str],
) -> Iterator[dict[str, Any]]:
    """Stream selected BTC files without materializing them."""
    yield from iter_raw_rows(paths)
