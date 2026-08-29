from __future__ import annotations

import pytest

from std0_quant.features.btc_features import compute_btc_features
from std0_quant.features.btc_streaming import compute_btc_features_streaming


T = 2_000_000


def tick(ts, p=100, size=1, maker=False):
    return {
        "exchange_timestamp_ms": ts,
        "price": p,
        "size": size,
        "buyer_is_maker": maker,
    }


def assert_equal(old, new):
    assert set(old) == set(new)

    for key in old:
        if isinstance(old[key], float):
            assert new[key] == pytest.approx(old[key]), key
        else:
            assert new[key] == old[key], key


def compare(rows, market_start_ms, cutoff_ms):
    old = compute_btc_features(rows, market_start_ms, cutoff_ms)
    new = compute_btc_features_streaming(rows, market_start_ms, cutoff_ms)
    assert_equal(old, new)


def test_streaming_matches_unordered_rows():
    rows = [
        tick(T - 1000, 105),
        tick(T - 5000, 100),
        tick(T + 1, 999),
        tick(T - 30_000, 90),
        tick(T - 10_000, 95),
    ]

    compare(rows, T - 60_000, T - 1000)


def test_streaming_matches_predecessor_before_source_floor():
    cutoff = T
    market_start = T - 5000

    rows = [
        tick(T - 40_000, 80),
        tick(T - 30_500, 90),
        tick(T - 25_000, 95),
        tick(T - 5000, 100),
        tick(T, 110),
    ]

    compare(rows, market_start, cutoff)


def test_streaming_matches_market_start_nearby_tick_rule():
    rows = [
        tick(T - 1001, 90),
        tick(T - 1000, 91),
        tick(T, 100),
    ]

    compare(rows, T, T)


def test_streaming_matches_equal_timestamp_semantics():
    rows = [
        tick(T - 5000, 100),
        tick(T - 1000, 101),
        tick(T - 1000, 102),
        tick(T, 103),
    ]

    compare(rows, T - 5000, T)


def test_streaming_ignores_post_cutoff_rows():
    rows = [
        tick(T - 5000, 100),
        tick(T - 1000, 105),
        tick(T + 1, 999),
        tick(T + 5000, 777),
    ]

    compare(rows, T - 5000, T - 1000)
