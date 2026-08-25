"""Event ledger integration tests: exclusion cascade, Y30 wiring, and
one-row-per-market guarantees."""

from __future__ import annotations

from conftest import make_trade
from std0_quant.events.event_ledger import (
    GammaMarketMetadataProvider,
    MarketCoverage,
    MarketMetadata,
    NullCoverageProvider,
    StaticMarketMetadataProvider,
    build_ledger_rows,
)
from std0_quant.events.fills import fill_from_envelope
from std0_quant.storage import envelope

BASE = 1_700_000_100 * 1000  # start of the 5-minute market window
MARKET_END = BASE + 300_000


def fill(tx: str, ts_ms: int, *, outcome: str, condition_id: str = "0xcA",
         side: str = "BUY", size: float = 100.0, price: float = 0.5,
         slug: str = "bitcoin-up-or-down-test"):
    idx = 0 if outcome == "Up" else 1
    record = make_trade(tx, ts_ms // 1000, side=side, outcome=outcome,
                        outcome_index=idx, size=size, price=price,
                        condition_id=condition_id, slug=slug)
    return fill_from_envelope(envelope("test", record, "run-1"))


def meta(condition_id: str = "0xcA", *, start_ms: int | None = BASE,
         end_ms: int | None = MARKET_END, slug: str | None = "test-market"):
    return MarketMetadata(condition_id=condition_id, slug=slug,
                          market_start_ms=start_ms, market_end_ms=end_ms)


class FakeCoverage:
    def __init__(self, per_market: dict | None = None) -> None:
        self.per_market = per_market or {}
        self.calls: list[str] = []

    def market_coverage(self, condition_id, market_start_ms, market_end_ms):
        self.calls.append(condition_id)
        return self.per_market.get(condition_id, MarketCoverage())


def test_clean_market_with_first_opposite_and_y30_positive() -> None:
    t0_episode = [
        fill("0xup1", BASE + 1_000, outcome="Up", size=100, price=0.6),
        fill("0xdn1", BASE + 60_000, outcome="Down", size=200, price=0.35),
        fill("0xdn2", BASE + 61_000, outcome="Down", size=100, price=0.40),
        # continuation buy of the Down direction 10s after episode end
        fill("0xdn3", BASE + 61_000 + 10_000, outcome="Down", size=50, price=0.45),
    ]
    rows = build_ledger_rows(
        t0_episode, StaticMarketMetadataProvider({"0xcA": meta()}),
        NullCoverageProvider(),
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["clean_flag"] is True
    assert row["exclude_reason"] is None
    assert row["initial_direction"] == "Up"
    assert row["initial_first_timestamp_ms"] == BASE + 1_000
    assert row["initial_qty"] == 100  # first Up episode size
    assert row["first_opp_direction"] == "Down"
    assert row["first_opp_start_ms"] == BASE + 60_000
    assert row["first_opp_end_ms"] == BASE + 61_000
    assert row["first_opp_qty"] == 300.0
    assert row["first_opp_vwap"] == (200 * 0.35 + 100 * 0.40) / 300.0
    assert row["first_opp_fill_count"] == 2
    assert row["up_qty_before_first_opp"] == 100.0
    assert row["down_qty_before_first_opp"] == 0.0
    assert row["old_direction_qty"] == 100.0  # initial direction = Up
    assert row["y30"] == 1
    assert row["y30_event_ts_ms"] == BASE + 71_000
    assert row["y30_horizon_eligible"] is True
    assert row["episode_rule_version"] == "v1_3sec"
    assert row["market_start_ms"] == BASE
    assert row["market_end_ms"] == MARKET_END


def test_y30_negative_when_no_continuation_buy() -> None:
    fills = [
        fill("0xup1", BASE + 1_000, outcome="Up"),
        fill("0xdn1", BASE + 60_000, outcome="Down"),
    ]
    rows = build_ledger_rows(
        fills, StaticMarketMetadataProvider({"0xcA": meta()})
    )
    row = rows[0]
    assert row["y30"] == 0
    assert row["y30_horizon_eligible"] is True


def test_horizon_censoring_in_ledger() -> None:
    """Spec Test F end-to-end: market ends inside the 30s window."""
    # first opposite ends at BASE+60_000; market ends at BASE+80_000 (20s short)
    fills = [
        fill("0xup1", BASE + 1_000, outcome="Up"),
        fill("0xdn1", BASE + 60_000, outcome="Down"),
    ]
    rows = build_ledger_rows(
        fills,
        StaticMarketMetadataProvider(
            {"0xcA": meta(end_ms=BASE + 80_000)}
        ),
    )
    row = rows[0]
    assert row["clean_flag"] is True  # censored, not excluded
    assert row["y30"] == 0
    assert row["y30_horizon_eligible"] is False


def test_same_second_ambiguity_excluded_not_guessed() -> None:
    """Spec Test C end-to-end through the ledger."""
    fills = [
        fill("0xup1", BASE + 5_000, outcome="Up"),
        fill("0xdn1", BASE + 5_000, outcome="Down"),
        fill("0xdn2", BASE + 7_000, outcome="Down"),
    ]
    rows = build_ledger_rows(
        fills, StaticMarketMetadataProvider({"0xcA": meta()})
    )
    row = rows[0]
    assert row["clean_flag"] is False
    assert row["exclude_reason"] == "SAME_SECOND_DIRECTION_AMBIGUITY"
    assert row["initial_direction"] is None


def test_no_buy_fills_excluded_field_incomplete() -> None:
    fills = [fill("0xsell", BASE + 5_000, outcome="Up", side="SELL")]
    rows = build_ledger_rows(
        fills, StaticMarketMetadataProvider({"0xcA": meta()})
    )
    row = rows[0]
    assert row["clean_flag"] is False
    assert row["exclude_reason"] == "FIELD_INCOMPLETE"
    assert "no BUY fills" in row["exclude_detail"]


def test_invalid_timestamp_excluded() -> None:
    record = make_trade("0xbad", "not-a-timestamp", outcome="Up")
    fills = [fill_from_envelope(envelope("test", record, "run-1"))]
    rows = build_ledger_rows(
        fills, StaticMarketMetadataProvider({"0xcA": meta()})
    )
    assert rows[0]["exclude_reason"] == "TIMESTAMP_INVALID"


def test_missing_metadata_excluded() -> None:
    fills = [fill("0xup1", BASE + 1_000, outcome="Up")]
    rows = build_ledger_rows(
        fills, StaticMarketMetadataProvider({})
    )
    row = rows[0]
    assert row["clean_flag"] is False
    assert row["exclude_reason"] == "MARKET_METADATA_MISSING"


def test_metadata_without_end_date_excluded() -> None:
    fills = [
        fill("0xup1", BASE + 1_000, outcome="Up"),
        fill("0xdn1", BASE + 60_000, outcome="Down"),
    ]
    rows = build_ledger_rows(
        fills, StaticMarketMetadataProvider({"0xcA": meta(end_ms=None)})
    )
    assert rows[0]["exclude_reason"] == "MARKET_METADATA_MISSING"


def test_inconsistent_market_times_excluded_as_other_with_detail() -> None:
    # FirstOpposite ends AFTER the market end (beyond tolerance) -> OTHER.
    fills = [
        fill("0xup1", BASE + 1_000, outcome="Up"),
        fill("0xdn1", MARKET_END + 20 * 60_000, outcome="Down"),
    ]
    rows = build_ledger_rows(
        fills, StaticMarketMetadataProvider({"0xcA": meta()})
    )
    row = rows[0]
    assert row["exclude_reason"] == "OTHER"
    assert row["exclude_detail"]  # OTHER must always carry detail


def test_single_direction_market_is_clean_without_events() -> None:
    fills = [
        fill("0xup1", BASE + 1_000, outcome="Up"),
        fill("0xup2", BASE + 2_000, outcome="Up"),
    ]
    rows = build_ledger_rows(
        fills, StaticMarketMetadataProvider({"0xcA": meta()})
    )
    row = rows[0]
    assert row["clean_flag"] is True
    assert row["initial_direction"] == "Up"
    assert row["first_opp_direction"] is None
    assert row["y30"] is None
    assert row["y30_horizon_eligible"] is None
    assert row["up_qty_before_first_opp"] == 200.0
    assert row["old_direction_qty"] is None  # defined only with a first opposite


def test_coverage_exclusions_only_when_session_promised_data() -> None:
    fills = [
        fill("0xup1", BASE + 1_000, outcome="Up"),
        fill("0xdn1", BASE + 60_000, outcome="Down"),
    ]
    coverage = FakeCoverage({
        "0xcA": MarketCoverage(
            poly_book_coverage_pct=0.0, btc_coverage_pct=None,
            book_expected=True, btc_expected=True,
        )
    })
    rows = build_ledger_rows(
        fills, StaticMarketMetadataProvider({"0xcA": meta()}), coverage
    )
    assert rows[0]["exclude_reason"] == "BOOK_DATA_MISSING"

    coverage_ok = FakeCoverage({
        "0xcA": MarketCoverage(
            poly_book_coverage_pct=0.98, btc_coverage_pct=0.99,
            book_expected=True, btc_expected=True,
        )
    })
    rows = build_ledger_rows(
        fills, StaticMarketMetadataProvider({"0xcA": meta()}), coverage_ok
    )
    assert rows[0]["clean_flag"] is True
    assert rows[0]["poly_book_coverage_pct"] == 0.98


def test_one_row_per_market_and_sorted() -> None:
    fills = [
        fill("0xb1", BASE + 1_000, outcome="Up", condition_id="0xcB"),
        fill("0xa1", BASE + 1_000, outcome="Up", condition_id="0xcA"),
        fill("0xa2", BASE + 2_000, outcome="Up", condition_id="0xcA"),
    ]
    rows = build_ledger_rows(
        fills,
        StaticMarketMetadataProvider({
            "0xcA": meta("0xcA"), "0xcB": meta("0xcB"),
        }),
    )
    assert [r["condition_id"] for r in rows] == ["0xcA", "0xcB"]


def test_gamma_provider_offline_returns_none_and_ledger_flags() -> None:
    provider = GammaMarketMetadataProvider("http://gamma.local", fetch_fn=None)
    fills = [fill("0xup1", BASE + 1_000, outcome="Up")]
    rows = build_ledger_rows(fills, provider)
    assert rows[0]["exclude_reason"] == "MARKET_METADATA_MISSING"


def test_gamma_provider_parses_market_body(tmp_path) -> None:
    import json as _json

    body = _json.dumps([{
        "conditionId": "0xcA", "slug": "bitcoin-up-or-down-x",
        "startDate": "2026-08-23T15:00:00Z", "endDate": "2026-08-23T15:05:00Z",
    }])

    def fetch(url, params):
        assert params == {"condition_ids": "0xcA"}
        return 200, body

    provider = GammaMarketMetadataProvider(
        "http://gamma.local", cache_path=tmp_path / "meta.ndjson", fetch_fn=fetch
    )
    got = provider.get("0xcA")
    assert got is not None
    assert got.slug == "bitcoin-up-or-down-x"
    assert got.market_start_ms is not None
    assert got.market_end_ms == got.market_start_ms + 300_000

    # Second provider instance reads from the append-only cache, no network.
    offline = GammaMarketMetadataProvider(
        "http://gamma.local", cache_path=tmp_path / "meta.ndjson", fetch_fn=None
    )
    cached = offline.get("0xcA")
    assert cached is not None
    assert cached.slug == "bitcoin-up-or-down-x"


def test_fill_after_market_end_but_within_tolerance_is_kept() -> None:
    # Fill 2 minutes after market end (within the 10-minute tolerance).
    fills = [
        fill("0xup1", BASE + 1_000, outcome="Up"),
        fill("0xdn1", MARKET_END + 60_000, outcome="Down"),
    ]
    rows = build_ledger_rows(
        fills, StaticMarketMetadataProvider({"0xcA": meta()})
    )
    assert rows[0]["clean_flag"] is True
