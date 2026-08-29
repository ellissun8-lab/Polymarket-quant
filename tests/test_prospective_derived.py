from __future__ import annotations

import json

import pytest
from conftest import make_trade

from std0_quant.events.prospective_derived import (
    build_prospective_derived_rows,
)


START_S = 1_700_000_100  # exactly 5-minute aligned
START_MS = START_S * 1000
END_MS = START_MS + 300_000
CID = "0xcid1"
SLUG = f"btc-updown-5m-{START_S}"


def write_ndjson(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def write_sidecar(raw_path, *, session_id, first_ms, last_ms, source):
    sidecar = raw_path.with_suffix(raw_path.suffix + ".meta.json")
    sidecar.write_text(
        json.dumps(
            {
                "file": str(raw_path),
                "source": source,
                "session_id": session_id,
                "first_timestamp_ms": first_ms,
                "last_timestamp_ms": last_ms,
                "record_count": 1,
                "sha256": "fixture-sha",
                "parse_errors": 0,
                "integrity_status": "OK",
            }
        ),
        encoding="utf-8",
    )


def trade_envelope(tx, ts_s, *, outcome, outcome_index, size=10):
    return {
        "source": "fixture",
        "fetched_at_ms": END_MS + 1000,
        "sync_run_id": "fixture-run",
        "record": make_trade(
            tx,
            ts_s,
            condition_id=CID,
            slug=SLUG,
            outcome=outcome,
            outcome_index=outcome_index,
            size=size,
        ),
    }


def write_session(path, *, session_id, kind, market=None):
    rows = [
        {
            "session_id": session_id,
            "kind": kind,
            "event": "connected",
            "timestamp_ms": START_MS - 5000,
        }
    ]

    if market is not None:
        rows.append(
            {
                "session_id": session_id,
                "kind": kind,
                "event": "subscribed",
                "timestamp_ms": START_MS - 4000,
                "market": market,
            }
        )

    rows.append(
        {
            "session_id": session_id,
            "kind": kind,
            "event": "session_end",
            "timestamp_ms": END_MS + 5000,
        }
    )

    write_ndjson(path, rows)


def prepare_unique_sources(tmp_path):
    book_dir = tmp_path / "book"
    btc_dir = tmp_path / "btc"
    sessions_dir = tmp_path / "sessions"

    write_session(
        sessions_dir / "book.ndjson",
        session_id="book-1",
        kind="polymarket_book",
        market=CID,
    )
    write_session(
        sessions_dir / "btc.ndjson",
        session_id="btc-1",
        kind="btc_ticks",
    )

    book_raw = write_ndjson(
        book_dir / "book_0001.ndjson",
        [
            {
                "condition_id": CID,
                "token_id": "up-token",
                "outcome": "Up",
                "receive_timestamp_ms": START_MS + 1000,
                "book_state_valid": True,
            },
            {
                "condition_id": CID,
                "token_id": "down-token",
                "outcome": "Down",
                "receive_timestamp_ms": START_MS + 1000,
                "book_state_valid": True,
            },
        ],
    )
    write_sidecar(
        book_raw,
        session_id="book-1",
        first_ms=START_MS + 1000,
        last_ms=START_MS + 1000,
        source="polymarket_book",
    )

    btc_raw = write_ndjson(
        btc_dir / "btc_0001.ndjson",
        [
            {"exchange_timestamp_ms": START_MS + 1000},
            {"exchange_timestamp_ms": START_MS + 2000},
        ],
    )
    write_sidecar(
        btc_raw,
        session_id="btc-1",
        first_ms=START_MS + 1000,
        last_ms=START_MS + 2000,
        source="btc_ticks",
    )

    return book_dir, btc_dir, sessions_dir


def test_builds_isolated_fills_episodes_ledger_and_y30(tmp_path):
    raw_path = write_ndjson(
        tmp_path / "prospective" / "trades.ndjson",
        [
            # Initial Up episode.
            trade_envelope("0xup", START_S + 10, outcome="Up", outcome_index=0),

            # FirstOpposite Down episode.
            trade_envelope("0xdown1", START_S + 20, outcome="Down", outcome_index=1),

            # 5 seconds later => a new Down parent episode, inside Y30.
            trade_envelope("0xdown2", START_S + 25, outcome="Down", outcome_index=1),
        ],
    )

    book_dir, btc_dir, sessions_dir = prepare_unique_sources(tmp_path)

    result = build_prospective_derived_rows(
        raw_path=raw_path,
        book_dir=book_dir,
        btc_dir=btc_dir,
        sessions_dir=sessions_dir,
        slug_prefix="btc-updown-5m-",
        market_window_seconds=300,
        coverage_bucket_seconds=1.0,
        coverage_gap_threshold_seconds=5.0,
    )

    assert len(result.fill_rows) == 3
    assert len(result.episode_rows) == 3
    assert len(result.ledger_rows) == 1
    assert len(result.coverage_selection_rows) == 1

    ledger = result.ledger_rows[0]
    selection = result.coverage_selection_rows[0]

    assert ledger["condition_id"] == CID
    assert ledger["initial_direction"] == "Up"
    assert ledger["first_opp_direction"] == "Down"
    assert ledger["first_opp_start_ms"] == (START_S + 20) * 1000
    assert ledger["first_opp_end_ms"] == (START_S + 20) * 1000

    # Frozen Y30 semantics: (t0, t0+30s]; +5s recurrence => Y30=1.
    assert ledger["y30"] == 1
    assert ledger["y30_horizon_eligible"] is True
    assert ledger["episode_rule_version"] == "v1_3sec"

    assert selection["status"] == "ELIGIBLE"
    assert selection["book_session_id"] == "book-1"
    assert selection["btc_session_id"] == "btc-1"


def test_session_ambiguity_is_separate_from_ledger_truth(tmp_path):
    raw_path = write_ndjson(
        tmp_path / "prospective" / "trades.ndjson",
        [
            trade_envelope("0xup", START_S + 10, outcome="Up", outcome_index=0),
            trade_envelope("0xdown", START_S + 20, outcome="Down", outcome_index=1),
        ],
    )

    book_dir = tmp_path / "book"
    btc_dir = tmp_path / "btc"
    sessions_dir = tmp_path / "sessions"

    # Two matching Book sessions => prospective source selection is ambiguous.
    write_session(
        sessions_dir / "book-a.ndjson",
        session_id="book-a",
        kind="polymarket_book",
        market=CID,
    )
    write_session(
        sessions_dir / "book-b.ndjson",
        session_id="book-b",
        kind="polymarket_book",
        market=CID,
    )
    write_session(
        sessions_dir / "btc.ndjson",
        session_id="btc-1",
        kind="btc_ticks",
    )

    # BTC raw exists; Book ambiguity must still fail coverage eligibility
    # before any cross-session file stitching can occur.
    btc_raw = write_ndjson(
        btc_dir / "btc.ndjson",
        [{"exchange_timestamp_ms": START_MS + 1000}],
    )
    write_sidecar(
        btc_raw,
        session_id="btc-1",
        first_ms=START_MS + 1000,
        last_ms=START_MS + 1000,
        source="btc_ticks",
    )

    result = build_prospective_derived_rows(
        raw_path=raw_path,
        book_dir=book_dir,
        btc_dir=btc_dir,
        sessions_dir=sessions_dir,
        slug_prefix="btc-updown-5m-",
        market_window_seconds=300,
        coverage_bucket_seconds=1.0,
        coverage_gap_threshold_seconds=5.0,
    )

    selection = result.coverage_selection_rows[0]
    ledger = result.ledger_rows[0]

    assert selection["status"] == "INELIGIBLE"
    assert any(
        reason.startswith("book_session_ambiguous")
        for reason in selection["reasons"]
    )

    # Session eligibility is a prospective governance gate, not a new
    # FirstOpposite/Y30/ledger exclusion definition.
    assert ledger["condition_id"] == CID
    assert ledger["initial_direction"] == "Up"
    assert ledger["first_opp_direction"] == "Down"


def test_missing_prospective_raw_fails_closed(tmp_path):
    with pytest.raises(FileNotFoundError, match="prospective raw trade store missing"):
        build_prospective_derived_rows(
            raw_path=tmp_path / "missing.ndjson",
            book_dir=tmp_path / "book",
            btc_dir=tmp_path / "btc",
            sessions_dir=tmp_path / "sessions",
            slug_prefix="btc-updown-5m-",
            market_window_seconds=300,
            coverage_bucket_seconds=1.0,
            coverage_gap_threshold_seconds=5.0,
        )
