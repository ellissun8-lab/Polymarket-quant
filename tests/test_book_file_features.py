from __future__ import annotations

import json

import pytest

from std0_quant.features.book_features import compute_book_features
from std0_quant.features.book_file_features import compute_book_features_from_files


T = 2_000_000
CID = "market-a"


def book(ts, *, token="opp", outcome="Down", bid=.4, ask=.6):
    return {
        "condition_id": CID,
        "receive_timestamp_ms": ts,
        "token_id": token,
        "outcome": outcome,
        "best_bid": bid,
        "best_ask": ask,
        "mid": (bid + ask) / 2,
        "spread": ask - bid,
        "bids": [{"price": bid, "size": 10}],
        "asks": [{"price": ask, "size": 5}],
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


def test_book_features_from_rotated_files_match_existing_implementation(tmp_path):
    rows1 = [
        book(T - 5000, bid=.30, ask=.70),
        book(
            T - 4000,
            token="initial",
            outcome="Up",
            bid=.35,
            ask=.65,
        ),
    ]
    rows2 = [
        book(T - 2000, bid=.40, ask=.60),
        book(T - 1000, bid=.45, ask=.55),
        book(T + 1, bid=.49, ask=.51),
    ]

    part1 = write_ndjson(tmp_path / "book_0001.ndjson", rows1)
    part2 = write_ndjson(tmp_path / "book_0002.ndjson", rows2)

    old = compute_book_features(
        rows1 + rows2,
        T - 1000,
        "Down",
        "Up",
        stale_after_ms=5000,
    )

    new = compute_book_features_from_files(
        [part1, part2],
        condition_id=CID,
        cutoff_ms=T - 1000,
        opp_outcome="Down",
        initial_outcome="Up",
        stale_after_ms=5000,
    )

    assert_equal(old, new)
