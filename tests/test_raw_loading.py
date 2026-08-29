from __future__ import annotations

import json

import pytest

from std0_quant.features.raw_loading import (
    iter_raw_rows,
    iter_book_rows,
    iter_btc_rows,
)


def _write(path, rows):
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def test_iter_raw_rows_preserves_source_file(tmp_path):
    path = _write(
        tmp_path / "a.ndjson",
        [{"x": 1}, {"x": 2}],
    )

    rows = list(iter_raw_rows([path]))

    assert [row["x"] for row in rows] == [1, 2]
    assert all(row["_source_file"] == str(path) for row in rows)


def test_iter_book_rows_rejects_cross_market_contamination(tmp_path):
    path = _write(
        tmp_path / "book.ndjson",
        [
            {"condition_id": "A", "receive_timestamp_ms": 1000},
            {"condition_id": "B", "receive_timestamp_ms": 1001},
        ],
    )

    iterator = iter_book_rows([path], condition_id="A")

    first = next(iterator)
    assert first["condition_id"] == "A"

    with pytest.raises(ValueError, match="unexpected condition_id"):
        next(iterator)


def test_iter_btc_rows_streams_selected_files_in_order(tmp_path):
    first = _write(
        tmp_path / "btc1.ndjson",
        [
            {"exchange_timestamp_ms": 1000, "price": 10},
            {"exchange_timestamp_ms": 1001, "price": 11},
        ],
    )
    second = _write(
        tmp_path / "btc2.ndjson",
        [
            {"exchange_timestamp_ms": 2000, "price": 12},
        ],
    )

    rows = list(iter_btc_rows([first, second]))

    assert [row["exchange_timestamp_ms"] for row in rows] == [
        1000,
        1001,
        2000,
    ]
    assert rows[0]["_source_file"] == str(first)
    assert rows[-1]["_source_file"] == str(second)
