"""Development utility: seed a clearly-synthetic fixture dataset and run the
full pipeline against it (validates sync -> episodes -> ledger -> reports
without network access).

Creates an isolated workspace at ``data_fixture/`` with its own config and
data tree so fixture data can never mix with real collections.

    python scripts/dev_seed_fixture.py
    cd data_fixture
    python ../scripts/build_episodes.py
    python ../scripts/build_event_ledger.py --offline

All fixture markets use the REAL slug format ``btc-updown-5m-<unix_start>``
(condition ids prefixed ``0xfix`` keep them distinguishable from real data),
so the slug-window metadata path is exercised exactly as in production.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_PROJECT_SRC = _PROJECT_ROOT / "src"
if str(_PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(_PROJECT_SRC))

from std0_quant.collectors.std0_trades import (  # noqa: E402
    RetryingClient,
    Std0TradesSyncer,
)
from std0_quant.config import load_settings  # noqa: E402
from std0_quant.events.event_ledger import GammaMarketMetadataProvider  # noqa: E402
from std0_quant.storage import (  # noqa: E402
    AppendOnlyNDJSON,
    RawPageStore,
    SqliteState,
    envelope,
)
from std0_quant.timeutil import utc_now_ms  # noqa: E402

WALLET = "0xdf7930e89a2c47560165331863c31deca0733dcd"
MINUTE = 60_000
MARKET_LEN = 5 * MINUTE


def trade(tx: str, ts_ms: int, outcome: str, condition_id: str, *,
          side: str = "BUY", size: float = 100.0, price: float = 0.5,
          slug: str = "fixture-market", bad_ts: object | None = None) -> dict:
    return {
        "proxyWallet": WALLET,
        "side": side,
        "asset": f"tok-{condition_id[-4:]}-{outcome.lower()}",
        "conditionId": condition_id,
        "size": str(size),
        "price": str(price),
        "timestamp": str(bad_ts if bad_ts is not None else ts_ms // 1000),
        "title": f"Bitcoin Up or Down (FIXTURE) {condition_id}",
        "slug": slug,
        "outcome": outcome,
        "outcomeIndex": 0 if outcome == "Up" else 1,
        "transactionHash": tx,
        "name": "std0",
        "pseudonym": "std0",
    }


class FakeAPI:
    """Serves fixture trades with newest-first offset paging."""

    def __init__(self, records: list[dict]) -> None:
        self.records = records
        self.calls = 0

    def __call__(self, url: str, params: dict) -> tuple[int, str]:
        self.calls += 1
        offset = int(params.get("offset", 0))
        limit = int(params.get("limit", 100))
        return 200, json.dumps(self.records[offset : offset + limit])


class FakeGamma:
    """Serves fixture market metadata for the cache-seeding provider."""

    def __init__(self, markets: dict[str, dict]) -> None:
        self.markets = markets
        self.calls = 0

    def __call__(self, url: str, params: dict) -> tuple[int, str]:
        self.calls += 1
        cid = params.get("condition_ids")
        market = self.markets.get(cid)
        if market is None:
            return 200, "[]"
        return 200, json.dumps([market])


def build_fixture(now_ms: int) -> tuple[list[dict], dict[str, dict]]:
    """~12 fixture markets covering clean / censored / excluded paths."""
    trades: list[dict] = []
    meta: dict[str, dict] = {}
    # align to a 5-minute boundary: real btc-updown-5m slugs always are
    base = (now_ms - 13 * MARKET_LEN) // MARKET_LEN * MARKET_LEN

    def add_market(name: str, fills: list[dict], *,
                   slug_override: str | None = None) -> None:
        cid = f"0xfix{name}"
        start = base + int(name) * MARKET_LEN
        end = start + MARKET_LEN
        slug = slug_override or f"btc-updown-5m-{start // 1000}"
        for fill in fills:
            fill["conditionId"] = cid
            fill["slug"] = slug
            trades.append(fill)
        meta[cid] = {
            "conditionId": cid, "slug": slug,
            "startDate": str(start), "endDate": str(end),
            "tokens": [
                {"token_id": f"tok-{name}-up", "outcome": "Up"},
                {"token_id": f"tok-{name}-dn", "outcome": "Down"},
            ],
        }

    # 00: clean, initial Up, first opposite Down at +60s, Y30=1 (continuation)
    s = base + 0 * MARKET_LEN
    add_market("00", [
        trade("fx00a", s + 5_000, "Up", "", size=100, price=0.55),
        trade("fx00b", s + 6_000, "Up", "", size=50, price=0.57),
        trade("fx00c", s + 60_000, "Down", "", size=200, price=0.35),
        trade("fx00d", s + 75_000, "Down", "", size=80, price=0.40),
    ])
    # 01: clean, Y30=0 (no continuation)
    s = base + 1 * MARKET_LEN
    add_market("01", [
        trade("fx01a", s + 10_000, "Up", "", size=120, price=0.5),
        trade("fx01b", s + 80_000, "Down", "", size=90, price=0.3),
    ])
    # 02: clean, initial Down, first opposite Up, Y30=1
    s = base + 2 * MARKET_LEN
    add_market("02", [
        trade("fx02a", s + 8_000, "Down", "", size=60, price=0.4),
        trade("fx02b", s + 50_000, "Up", "", size=70, price=0.6),
        trade("fx02c", s + 70_000, "Up", "", size=30, price=0.65),
    ])
    # 03: horizon-censored: first opposite ends at s+285s; the 30s Y30 window
    # would need data past the s+300s market end -> y30_horizon_eligible=false
    s = base + 3 * MARKET_LEN
    add_market("03", [
        trade("fx03a", s + 5_000, "Up", "", size=100, price=0.5),
        trade("fx03b", s + 285_000, "Down", "", size=100, price=0.5),
    ])
    # 04: SAME_SECOND_DIRECTION_AMBIGUITY
    s = base + 4 * MARKET_LEN
    add_market("04", [
        trade("fx04a", s + 20_000, "Up", "", size=100, price=0.5),
        trade("fx04b", s + 20_000, "Down", "", size=100, price=0.5),
        trade("fx04c", s + 30_000, "Down", "", size=100, price=0.5),
    ])
    # 05: SELL-only market -> FIELD_INCOMPLETE
    s = base + 5 * MARKET_LEN
    add_market("05", [
        trade("fx05a", s + 15_000, "Up", "", side="SELL", size=100, price=0.5),
    ])
    # 06: TIMESTAMP_INVALID
    s = base + 6 * MARKET_LEN
    add_market("06", [
        trade("fx06a", s + 15_000, "Up", "", bad_ts="not-a-timestamp"),
    ])
    # 07: single direction only (clean, no FirstOpposite)
    s = base + 7 * MARKET_LEN
    add_market("07", [
        trade("fx07a", s + 10_000, "Up", "", size=40, price=0.5),
        trade("fx07b", s + 11_500, "Up", "", size=60, price=0.52),
    ])
    # 08: same-second multi-fill burst inside one episode (Test B scenario)
    s = base + 8 * MARKET_LEN
    burst = s + 40_000
    add_market("08", [
        trade("fx08a", burst, "Up", "", size=10, price=0.40),
        trade("fx08b", burst, "Up", "", size=20, price=0.50),
        trade("fx08c", burst, "Up", "", size=30, price=0.60),
        trade("fx08d", burst + 2_500, "Down", "", size=100, price=0.5),
        trade("fx08e", burst + 40_000, "Down", "", size=50, price=0.45),
    ])
    # 09: MARKET_METADATA_MISSING (slug matches the universe prefix but does
    # not encode a valid aligned window -> slug derivation refuses to guess)
    s = base + 9 * MARKET_LEN
    add_market("09", [
        trade("fx09a", s + 5_000, "Up", "", size=100, price=0.5),
        trade("fx09b", s + 60_000, "Down", "", size=100, price=0.5),
    ], slug_override="btc-updown-5m-malformed")
    meta.pop(f"0xfix09")  # <- market 09 intentionally left without metadata
    # 10: clean with Y30=0 and a SELL afterwards (sells never break episodes)
    s = base + 10 * MARKET_LEN
    add_market("10", [
        trade("fx10a", s + 5_000, "Up", "", size=100, price=0.5),
        trade("fx10b", s + 60_000, "Down", "", size=100, price=0.5),
        trade("fx10c", s + 90_000, "Down", "", side="SELL", size=100, price=0.6),
    ])
    # 11: clean, boundary case: continuation buy exactly at t0+30s -> Y30=1
    s = base + 11 * MARKET_LEN
    add_market("11", [
        trade("fx11a", s + 5_000, "Up", "", size=100, price=0.5),
        trade("fx11b", s + 60_000, "Down", "", size=100, price=0.5),
        trade("fx11c", s + 60_000 + 30_000, "Down", "", size=25, price=0.5),
    ])
    return trades, meta


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    workspace = _PROJECT_ROOT / "data_fixture"
    if workspace.exists():
        shutil.rmtree(workspace)
    (workspace / "config").mkdir(parents=True)
    shutil.copy(_PROJECT_ROOT / "config" / "settings.yaml",
                workspace / "config" / "settings.yaml")

    # Run inside the fixture workspace so all relative data paths land there.
    import os
    os.chdir(workspace)
    settings = load_settings()
    from std0_quant.config import resolve_path

    trades, meta = build_fixture(utc_now_ms())
    trades_desc = sorted(
        trades,
        key=lambda t: int(t["timestamp"]) if str(t["timestamp"]).isdigit() else 0,
        reverse=True,
    )

    # 1) sync via the REAL syncer against the fake API (exercises dedupe)
    api = FakeAPI(trades_desc)
    client = RetryingClient("http://fixture-api.local", fetch_fn=api,
                            max_retries=0, sleeper=lambda _s: None)
    with SqliteState(resolve_path(settings, "state") / "sync_state.db") as state:
        with AppendOnlyNDJSON(
            resolve_path(settings, "raw_std0_trades") / "trades.ndjson"
        ) as writer:
            pages = RawPageStore(resolve_path(settings, "raw_api_pages"))
            syncer = Std0TradesSyncer(settings, state, writer, pages, client=client)
            result = syncer.sync(full=True)
            # sync twice to prove idempotency on the real code path
            result2 = syncer.sync(full=True)
    print(f"fixture sync: pages={result.pages_fetched} new={result.new_trades} "
          f"status={result.status}")
    print(f"re-sync:      new={result2.new_trades} "
          f"duplicates_skipped={result2.duplicates_skipped}")

    # 2) seed the gamma metadata cache through the real provider
    gamma = FakeGamma(meta)
    provider = GammaMarketMetadataProvider(
        base_url="http://fixture-gamma.local",
        cache_path=resolve_path(settings, "state") / "market_meta.ndjson",
        fetch_fn=gamma, max_retries=0, sleeper=lambda _s: None,
    )
    for condition_id in meta:
        provider.get(condition_id)
    print(f"metadata cache: {len(meta)} fixture markets seeded "
          f"({gamma.calls} gamma calls)")

    print(f"\nfixture workspace ready: {workspace}")
    print("next steps:")
    print("  cd data_fixture")
    print("  python ../scripts/build_episodes.py")
    print("  python ../scripts/build_event_ledger.py --offline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
