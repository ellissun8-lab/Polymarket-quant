from __future__ import annotations

import json

import pytest

from std0_quant.features.btc_features import compute_btc_features
from std0_quant.features.btc_file_features import compute_btc_features_from_files


T = 2_000_000


def tick(ts, p=100, size=1, maker=False):
    return {
        "exchange_timestamp_ms": ts,
        "price": p,
        "size": size,
        "buyer_is_maker": maker,
    }


def write_ndjson(path, rows):
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def assert_equal(old, new):
    assert set(old) == set(new)

    for key in old:
        if isinstance(old[key], float):
            assert new[key] == pytest.approx(old[key]), key
        else:
            assert new[key] == old[key], key


def test_btc_features_from_rotated_files_match_existing_implementation(tmp_path):
    rows1 = [
        tick(T - 40_000, 80),
        tick(T - 30_500, 90),
        tick(T - 25_000, 95),
    ]
    rows2 = [
        tick(T - 5000, 100),
        tick(T - 1000, 105, size=2, maker=False),
        tick(T + 1, 999),
    ]

    part1 = write_ndjson(tmp_path / "btc_0001.ndjson", rows1)
    part2 = write_ndjson(tmp_path / "btc_0002.ndjson", rows2)

    all_rows = rows1 + rows2

    old = compute_btc_features(
        all_rows,
        T - 5000,
        T - 1000,
    )

    new = compute_btc_features_from_files(
        [part1, part2],
        market_start_ms=T - 5000,
        cutoff_ms=T - 1000,
    )

    assert_equal(old, new)
