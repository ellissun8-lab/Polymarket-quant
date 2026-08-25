"""Tests for slug-derived market windows and the study-universe scope filter.

Real observed slug format (verified live): ``btc-updown-5m-<unix_start_s>``
with a 5-minute-aligned window start; gamma ``endDate == slug_ts + 300s``
while gamma ``startDate`` is the market CREATION time (not the window start).
"""

from __future__ import annotations

import json
import logging

from conftest import make_trade
from std0_quant.events.event_ledger import (
    EXCLUDE_OTHER,
    GammaMarketMetadataProvider,
    SlugWindowMetadataProvider,
    StaticMarketMetadataProvider,
    build_ledger_rows,
)
from std0_quant.events.fills import fill_from_envelope
from std0_quant.storage import envelope

from test_event_ledger import fill, meta  # noqa: F401  (reused helpers)


class TestSlugWindowMetadataProvider:
    def test_valid_slug_derives_five_minute_window(self) -> None:
        provider = SlugWindowMetadataProvider(
            {"0xcA": "btc-updown-5m-1787480700"}
        )
        meta = provider.get("0xcA")
        assert meta is not None
        assert meta.market_start_ms == 1_787_480_700_000
        assert meta.market_end_ms == 1_787_481_000_000
        assert meta.slug == "btc-updown-5m-1787480700"

    def test_unknown_condition_returns_none(self) -> None:
        provider = SlugWindowMetadataProvider({})
        assert provider.get("0xunknown") is None

    def test_wrong_prefix_returns_none(self) -> None:
        provider = SlugWindowMetadataProvider(
            {"0xSOL": "sol-updown-15m-1787480100"}
        )
        assert provider.get("0xSOL") is None

    def test_non_numeric_suffix_returns_none(self) -> None:
        provider = SlugWindowMetadataProvider(
            {"0xX": "btc-updown-5m-jan-1-1200pm-et"}
        )
        assert provider.get("0xX") is None

    def test_unaligned_timestamp_returns_none(self) -> None:
        # 1787480701 is not 5-minute aligned: refuse instead of guessing.
        provider = SlugWindowMetadataProvider(
            {"0xX": "btc-updown-5m-1787480701"}
        )
        assert provider.get("0xX") is None

    def test_custom_window_length(self) -> None:
        provider = SlugWindowMetadataProvider(
            {"0xSOL": "sol-updown-15m-1787480100"},
            slug_prefix="sol-updown-15m-",
            window_seconds=900,
        )
        meta = provider.get("0xSOL")
        assert meta is not None
        assert meta.market_start_ms == 1_787_480_100_000
        assert meta.market_end_ms == 1_787_481_000_000

    def test_from_fills_builds_map_and_warns_on_conflicts(
        self, caplog
    ) -> None:
        def f(tx, slug, cid="0xcA"):
            record = make_trade(tx, 1000, condition_id=cid, slug=slug)
            return fill_from_envelope(envelope("test", record, "run-1"))

        with caplog.at_level(logging.WARNING):
            provider = SlugWindowMetadataProvider.from_fills([
                f("0x1", "btc-updown-5m-1787480700"),
                f("0x2", "btc-updown-5m-1787480700"),
                f("0x3", "btc-updown-5m-1787481000"),  # conflicting slug
                f("0x4", "btc-updown-5m-1787480700", cid="0xcB"),
            ])
        assert provider.get("0xcA").slug == "btc-updown-5m-1787480700"
        assert provider.get("0xcB").slug == "btc-updown-5m-1787480700"
        assert any("multiple slugs" in message for message in caplog.messages)


class TestGammaSlugStartDerivation:
    def test_gamma_body_start_comes_from_slug_not_creation_date(self) -> None:
        # startDate = creation (200s before window); slug encodes the window.
        body = json.dumps([{
            "conditionId": "0xcA",
            "slug": "btc-updown-5m-1787480700",
            "startDate": 1_787_480_500_000,
            "endDate": 1_787_481_000_000,
        }])
        provider = GammaMarketMetadataProvider(
            "http://gamma.local", fetch_fn=lambda url, params: (200, body)
        )
        meta = provider.get("0xcA")
        assert meta is not None
        assert meta.market_start_ms == 1_787_480_700_000  # from slug
        assert meta.market_end_ms == 1_787_481_000_000

    def test_gamma_body_without_known_slug_falls_back_to_dates(self) -> None:
        body = json.dumps([{
            "conditionId": "0xcA",
            "slug": "some-other-market",
            "startDate": 1_787_480_500_000,
            "endDate": 1_787_481_000_000,
        }])
        provider = GammaMarketMetadataProvider(
            "http://gamma.local", fetch_fn=lambda url, params: (200, body)
        )
        meta = provider.get("0xcA")
        assert meta is not None
        assert meta.market_start_ms == 1_787_480_500_000
        assert meta.market_end_ms == 1_787_481_000_000


class TestScopeExclusion:
    def test_out_of_universe_market_excluded_with_other_detail(self) -> None:
        # std0 also trades sol-updown-15m: must stay in the ledger (reconciliation
        # balance) but be excluded loudly, never silently dropped.
        sol_fills = [
            fill("0xup1", 1_700_000_101_000, outcome="Up",
                 condition_id="0xSOL", slug="sol-updown-15m-1700000100"),
            fill("0xdn1", 1_700_000_160_000, outcome="Down",
                 condition_id="0xSOL", slug="sol-updown-15m-1700000100"),
        ]
        rows = build_ledger_rows(
            sol_fills, StaticMarketMetadataProvider({}),
            scope_slug_prefix="btc-updown-5m-",
        )
        assert len(rows) == 1
        row = rows[0]
        assert row["clean_flag"] is False
        assert row["exclude_reason"] == EXCLUDE_OTHER
        assert "out_of_scope_market" in row["exclude_detail"]
        assert "sol-updown-15m-1700000100" in row["exclude_detail"]

    def test_in_universe_market_untouched_by_scope_filter(self) -> None:
        slug = "btc-updown-5m-1700000100"  # matches BASE window in helpers
        fills = [
            fill("0xup1", 1_700_000_101_000, outcome="Up", condition_id="0xcA",
                 slug=slug),
            fill("0xdn1", 1_700_000_160_000, outcome="Down", condition_id="0xcA",
                 slug=slug),
        ]
        rows = build_ledger_rows(
            fills, StaticMarketMetadataProvider({"0xcA": meta()}),
            scope_slug_prefix="btc-updown-5m-",
        )
        assert rows[0]["clean_flag"] is True
        assert rows[0]["exclude_reason"] is None

    def test_no_scope_filter_keeps_legacy_behavior(self) -> None:
        # scope_slug_prefix=None (default): old tests/fixtures unaffected.
        fills = [
            fill("0xup1", 1_700_000_101_000, outcome="Up", condition_id="0xcA"),
        ]
        rows = build_ledger_rows(
            fills, StaticMarketMetadataProvider({"0xcA": meta()})
        )
        assert rows[0]["clean_flag"] is True

    def test_mixed_universe_reconciles_over_everything(self) -> None:
        from std0_quant.audit.reconciliation import build_reconciliation

        fills = [
            fill("0xup1", 1_700_000_101_000, outcome="Up", condition_id="0xcA",
                 slug="btc-updown-5m-1700000100"),
            fill("0xdn1", 1_700_000_160_000, outcome="Down", condition_id="0xcA",
                 slug="btc-updown-5m-1700000100"),
            fill("0xup1", 1_700_000_101_000, outcome="Up",
                 condition_id="0xSOL", slug="sol-updown-15m-1700000100"),
        ]
        rows = build_ledger_rows(
            fills, StaticMarketMetadataProvider({"0xcA": meta()}),
            scope_slug_prefix="btc-updown-5m-",
        )
        report = build_reconciliation({"0xcA", "0xSOL"}, {"0xcA", "0xSOL"}, rows)
        assert report.raw_markets == 2
        assert report.clean_markets == 1
        assert report.excluded_markets == 1
        assert report.problems == []
