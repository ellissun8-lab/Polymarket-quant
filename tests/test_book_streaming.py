from __future__ import annotations

import pytest

from std0_quant.features.book_features import compute_book_features
from std0_quant.features.book_streaming import compute_book_features_streaming


T = 2_000_000


def book(
    ts,
    *,
    token="opp",
    outcome="Down",
    bid=.4,
    ask=.6,
    valid_marker=None,
):
    row = {
        "receive_timestamp_ms": ts,
        "token_id": token,
        "outcome": outcome,
        "best_bid": bid,
        "best_ask": ask,
        "mid": (bid + ask) / 2,
        "spread": ask - bid,
        "bids": [
            {"price": bid, "size": 10},
            {"price": bid - .01, "size": 4},
            {"price": bid - .02, "size": 2},
        ],
        "asks": [
            {"price": ask, "size": 5},
            {"price": ask + .01, "size": 3},
            {"price": ask + .02, "size": 1},
        ],
    }
    if valid_marker is not None:
        row["book_state_valid"] = valid_marker
    return row


def assert_features_equal(old, new):
    assert set(old) == set(new)

    for key in old:
        a = old[key]
        b = new[key]

        if isinstance(a, float):
            assert b == pytest.approx(a), key
        else:
            assert b == a, key


def compare(rows, cutoff=T, stale_after_ms=5000):
    old = compute_book_features(
        rows,
        cutoff,
        "Down",
        "Up",
        stale_after_ms=stale_after_ms,
    )
    new = compute_book_features_streaming(
        rows,
        cutoff,
        "Down",
        "Up",
        stale_after_ms=stale_after_ms,
    )
    assert_features_equal(old, new)


def test_streaming_matches_legacy_unordered_cutoff_and_post_cutoff():
    rows = [
        book(T - 1000, bid=.42, ask=.58),
        book(
            T - 3000,
            token="initial",
            outcome="Up",
            bid=.3,
            ask=.7,
        ),
        book(T - 5000, bid=.35, ask=.65),
        book(T + 1, bid=.49, ask=.51),
        book(T - 2000, bid=.40, ask=.60),
    ]

    compare(rows)


def test_streaming_matches_validity_filtering():
    rows = [
        book(T - 7000, bid=.20, ask=.80, valid_marker=True),
        book(T - 4000, bid=.25, ask=.75, valid_marker=False),
        book(T - 3000, bid=.35, ask=.65, valid_marker=True),
        book(
            T - 2500,
            token="initial",
            outcome="Up",
            bid=.45,
            ask=.55,
            valid_marker=True,
        ),
        book(T - 1000, bid=.48, ask=.52, valid_marker=False),
        # Presence of validity metadata after cutoff still switches the
        # old implementation into validity-aware mode.
        book(T + 100, bid=.49, ask=.51, valid_marker=False),
    ]

    compare(rows)


def test_streaming_matches_dense_valid_coverage():
    rows = []

    for offset in range(30_000, -1, -1000):
        ts = T - offset
        rows.append(
            book(
                ts,
                token="opp",
                outcome="Down",
                bid=.40,
                ask=.60,
                valid_marker=True,
            )
        )
        rows.append(
            book(
                ts,
                token="initial",
                outcome="Up",
                bid=.45,
                ask=.55,
                valid_marker=True,
            )
        )

    compare(rows)


def test_streaming_matches_stale_state():
    rows = [
        book(T - 10_000, bid=.20, ask=.80),
        book(
            T - 9000,
            token="initial",
            outcome="Up",
            bid=.30,
            ask=.70,
        ),
    ]

    compare(rows, stale_after_ms=2000)


def test_streaming_matches_equal_timestamp_last_input_wins():
    rows = [
        book(T - 1000, bid=.30, ask=.70),
        book(T - 1000, bid=.44, ask=.56),
        book(
            T - 1000,
            token="initial",
            outcome="Up",
            bid=.35,
            ask=.65,
        ),
    ]

    compare(rows)
