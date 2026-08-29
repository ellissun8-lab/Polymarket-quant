from __future__ import annotations

import json

import pytest

from std0_quant.features.prospective_coverage import MarketCoverageSources
from std0_quant.features.prospective_coverage_provider import (
    build_market_coverage_provider,
)


def write_ndjson(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def test_provider_reads_only_explicitly_selected_files(tmp_path):
    book_dir = tmp_path / "book"
    btc_dir = tmp_path / "btc"
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()

    selected_book = write_ndjson(
        book_dir / "selected.ndjson",
        [
            {
                "condition_id": "cid-1",
                "token_id": "up-token",
                "outcome": "Up",
                "receive_timestamp_ms": 3000,
                "book_state_valid": True,
            }
        ],
    )
    selected_btc = write_ndjson(
        btc_dir / "selected.ndjson",
        [
            {
                "exchange_timestamp_ms": 4000,
            }
        ],
    )

    # If FileCoverageProvider ignores the bounded file lists and falls back
    # to rglob(), either of these malformed files will make the test fail.
    (book_dir / "poison.ndjson").write_text(
        "{not-json}\n",
        encoding="utf-8",
    )
    (btc_dir / "poison.ndjson").write_text(
        "{not-json}\n",
        encoding="utf-8",
    )

    sources = MarketCoverageSources(
        status="ELIGIBLE",
        condition_id="cid-1",
        book_session_id="book-1",
        btc_session_id="btc-1",
        book_files=(selected_book,),
        btc_files=(selected_btc,),
        reasons=(),
    )

    provider = build_market_coverage_provider(
        sources,
        book_dir=book_dir,
        btc_dir=btc_dir,
        sessions_dir=sessions_dir,
        bucket_seconds=1.0,
        gap_threshold_seconds=5.0,
    )

    book_rows = provider.book_rows_by_condition()
    btc_ts = provider.btc_timestamps()

    assert list(book_rows) == ["cid-1"]
    assert len(book_rows["cid-1"]) == 1
    assert btc_ts == [4000]


def test_ineligible_sources_fail_closed(tmp_path):
    sources = MarketCoverageSources(
        status="INELIGIBLE",
        condition_id="cid-1",
        book_session_id=None,
        btc_session_id=None,
        book_files=(),
        btc_files=(),
        reasons=("book_session_ambiguous:a,b",),
    )

    with pytest.raises(ValueError, match="ineligible sources"):
        build_market_coverage_provider(
            sources,
            book_dir=tmp_path / "book",
            btc_dir=tmp_path / "btc",
            sessions_dir=tmp_path / "sessions",
            bucket_seconds=1.0,
            gap_threshold_seconds=5.0,
        )
