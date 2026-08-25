"""Event ledger: one row per market summarizing std0's frozen behavioral events.

Ledger semantics (frozen, spec sections 1.3-1.5 and 9):

* one row per market (conditionId);
* ``initial_direction`` / FirstOpposite derived from BUY fills and the
  ``v1_3sec`` parent episodes;
* ``y30``: 1 if std0 BUYs the FirstOpposite direction again in
  ``(t0, t0 + 30s]`` where ``t0 = first_opp_end`` (episode END, not start);
* ``y30_horizon_eligible``: False when the market ended before ``t0 + 30s``
  -- such markets are NOT plain negative samples (censoring), per spec 1.5;
* every excluded market carries ``clean_flag = false`` and an
  ``exclude_reason``; ``OTHER`` additionally requires ``exclude_detail``.
  There is no silent dropping anywhere in this module.

INSUFFICIENT_Y30_HORIZON is deliberately NOT used as an exclusion reason:
spec Test F requires horizon censoring to be expressed via
``y30_horizon_eligible = false``, keeping the market in the clean set.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from std0_quant import (
    EPISODE_RULE_VERSION,
    EXCLUDE_BOOK_DATA_MISSING,
    EXCLUDE_BTC_DATA_MISSING,
    EXCLUDE_FIELD_INCOMPLETE,
    EXCLUDE_MARKET_METADATA_MISSING,
    EXCLUDE_OTHER,
    EXCLUDE_SAME_SECOND_AMBIGUITY,
    EXCLUDE_TIMESTAMP_INVALID,
    Y30_HORIZON_SECONDS,
)
from std0_quant.events.episode_builder import build_episodes, validate_buy_fill
from std0_quant.events.fills import Fill
from std0_quant.events.first_opposite import (
    analyze_initial_direction,
    find_first_opposite,
    qty_of_direction_before,
)
from std0_quant.storage import AppendOnlyNDJSON, read_ndjson
from std0_quant.timeutil import parse_ts_to_ms, utc_now_ms

logger = logging.getLogger(__name__)

# Tolerance before declaring trades/metadata inconsistent (gamma startDate /
# endDate are minute-aligned while fills are second-aligned).
MARKET_TIME_TOLERANCE_MS = 10 * 60 * 1000

# btc-updown-5m-<unix_start_seconds> (real observed slug format; window start
# is 5-minute aligned and endDate == ts + 300s, verified live on gamma).
_SLUG_WINDOW_RE = re.compile(r"btc-updown-5m-(\d{9,11})")


# ---------------------------------------------------------------------------
# Y30 (frozen definition, spec 1.5)
# ---------------------------------------------------------------------------

def compute_y30(
    first_opp_end_ms: int,
    same_direction_buy_ts_ms: list[int],
    horizon_seconds: int = Y30_HORIZON_SECONDS,
) -> tuple[int, int | None]:
    """Return ``(y30, y30_event_ts_ms)``.

    Window is ``(t0, t0 + horizon]``: a BUY at exactly ``t0`` is NOT a
    continuation (it would have merged into the FirstOpposite episode under
    the 3s rule); a BUY at ``t0 + horizon`` IS one; ``t0 + horizon + 1`` is not.
    """
    if horizon_seconds != Y30_HORIZON_SECONDS:
        raise ValueError(
            f"refusing non-frozen Y30 horizon {horizon_seconds}s "
            f"(frozen definition: {Y30_HORIZON_SECONDS}s)"
        )
    window_end = first_opp_end_ms + Y30_HORIZON_SECONDS * 1000
    events = [ts for ts in same_direction_buy_ts_ms if first_opp_end_ms < ts <= window_end]
    if events:
        return 1, min(events)
    return 0, None


def y30_horizon_eligible(
    first_opp_end_ms: int,
    market_end_ms: int | None,
    horizon_seconds: int = Y30_HORIZON_SECONDS,
) -> bool:
    """True when the full 30s observation window lies inside market lifetime."""
    if horizon_seconds != Y30_HORIZON_SECONDS:
        raise ValueError(
            f"refusing non-frozen Y30 horizon {horizon_seconds}s "
            f"(frozen definition: {Y30_HORIZON_SECONDS}s)"
        )
    if market_end_ms is None:
        return False
    return market_end_ms >= first_opp_end_ms + Y30_HORIZON_SECONDS * 1000


# ---------------------------------------------------------------------------
# Market metadata
# ---------------------------------------------------------------------------

@dataclass
class MarketMetadata:
    condition_id: str
    slug: str | None = None
    market_start_ms: int | None = None
    market_end_ms: int | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


class MarketMetadataProvider(Protocol):
    def get(self, condition_id: str) -> MarketMetadata | None: ...


class StaticMarketMetadataProvider:
    """In-memory provider (tests / manual overrides)."""

    def __init__(self, metadata: dict[str, MarketMetadata]) -> None:
        self._metadata = metadata

    def get(self, condition_id: str) -> MarketMetadata | None:
        return self._metadata.get(condition_id)


class SlugWindowMetadataProvider:
    """Derives the market window ``[ts, ts + window_seconds)`` from the slug.

    Real observed slug format for the study universe:
    ``btc-updown-5m-<unix_window_start_seconds>`` with the window start
    5-minute aligned. Verified live against the Gamma API: for active markets
    ``endDate == slug_ts + 300s`` (while ``startDate`` is the market CREATION
    time and must NOT be used as window start), and slug timestamps are
    5-minute aligned across observed fills.

    A slug that does not match the pattern (wrong prefix, non-numeric tail,
    or a non-aligned timestamp) yields ``None`` -- the ledger then flags the
    market ``MARKET_METADATA_MISSING`` instead of guessing.
    """

    def __init__(
        self,
        slug_by_condition: dict[str, str],
        slug_prefix: str = "btc-updown-5m-",
        window_seconds: int = 300,
    ) -> None:
        self._slugs = dict(slug_by_condition)
        self._prefix = slug_prefix
        self._window_seconds = window_seconds
        self._pattern = re.compile(re.escape(slug_prefix) + r"(\d{9,11})")

    @classmethod
    def from_fills(
        cls,
        fills: list[Fill],
        slug_prefix: str = "btc-updown-5m-",
        window_seconds: int = 300,
    ) -> "SlugWindowMetadataProvider":
        """Build the condition_id -> slug map from raw fills.

        Contradictory slugs for one condition_id are a data-integrity problem:
        the first slug wins and a loud warning is emitted (never silent).
        """
        slug_map: dict[str, str] = {}
        conflicts: dict[str, set[str]] = defaultdict(set)
        for fill in fills:
            if fill.condition_id and fill.slug:
                previous = slug_map.setdefault(fill.condition_id, fill.slug)
                if previous != fill.slug:
                    conflicts[fill.condition_id].update({previous, fill.slug})
        if conflicts:
            logger.warning(
                "condition_id mapped to multiple slugs; keeping first seen",
                extra={"conflicts": {k: sorted(v) for k, v in conflicts.items()}},
            )
        return cls(slug_map, slug_prefix=slug_prefix, window_seconds=window_seconds)

    def get(self, condition_id: str) -> MarketMetadata | None:
        slug = self._slugs.get(condition_id)
        if slug is None:
            return None
        match = self._pattern.fullmatch(slug)
        if match is None:
            return None
        start_s = int(match.group(1))
        if start_s % self._window_seconds != 0:
            logger.warning(
                "slug timestamp not window-aligned; refusing to derive window",
                extra={"slug": slug, "window_seconds": self._window_seconds},
            )
            return None
        return MarketMetadata(
            condition_id=condition_id,
            slug=slug,
            market_start_ms=start_s * 1000,
            market_end_ms=(start_s + self._window_seconds) * 1000,
            raw={"derivation": "slug_window", "window_seconds": self._window_seconds},
        )


class GammaMarketMetadataProvider:
    """Fetches market metadata from the public Gamma API with an
    append-only local cache. The cache stores every fetch (audit trail);
    ``get`` returns the most recent entry per condition_id.

    Network unavailability never raises out of ``get``: it returns ``None``
    and the ledger flags the market MARKET_METADATA_MISSING (rebuildable
    once the API becomes reachable).
    """

    def __init__(
        self,
        base_url: str,
        cache_path: Path | None = None,
        fetch_fn: Callable[[str, dict[str, Any]], tuple[int, str]] | None = None,
        request_timeout_seconds: float = 30.0,
        max_retries: int = 3,
        backoff_base_seconds: float = 1.5,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.cache_path = cache_path
        self._fetch = fetch_fn
        self._timeout = request_timeout_seconds
        self._max_retries = max_retries
        self._backoff = backoff_base_seconds
        self._sleep = sleeper
        self._cache: dict[str, MarketMetadata] = {}
        if cache_path is not None and Path(cache_path).is_file():
            self._load_cache()

    def _load_cache(self) -> None:
        try:
            for entry in read_ndjson(self.cache_path):  # type: ignore[arg-type]
                meta = _metadata_from_cache_entry(entry)
                if meta is not None:
                    self._cache[meta.condition_id] = meta
        except Exception as exc:
            logger.warning("failed to load metadata cache; starting empty",
                           extra={"error": repr(exc)})

    def get(self, condition_id: str) -> MarketMetadata | None:
        if condition_id in self._cache:
            return self._cache[condition_id]
        fetched = self._fetch_metadata(condition_id)
        if fetched is not None:
            self._cache[condition_id] = fetched
            self._append_cache(fetched)
        return fetched

    def _fetch_metadata(self, condition_id: str) -> MarketMetadata | None:
        if self._fetch is None:
            return None  # no network access configured (offline mode)
        url = f"{self.base_url}/markets"
        params = {"condition_ids": condition_id}
        last_error: str | None = None
        for attempt in range(self._max_retries + 1):
            try:
                status, body = self._fetch(url, params)
            except Exception as exc:
                status, body, last_error = -1, "", repr(exc)
            if status == 200:
                return _metadata_from_gamma_body(condition_id, body)
            if status not in (-1, 429) and not 500 <= status < 600:
                logger.warning("gamma metadata fetch failed",
                               extra={"status": status, "condition_id": condition_id})
                return None
            if attempt == self._max_retries:
                break
            self._sleep(self._backoff * (2**attempt))
        logger.warning("gamma metadata unavailable",
                       extra={"condition_id": condition_id, "error": last_error})
        return None

    def _append_cache(self, meta: MarketMetadata) -> None:
        if self.cache_path is None:
            return
        entry = {
            "fetched_at_ms": utc_now_ms(),
            "condition_id": meta.condition_id,
            "slug": meta.slug,
            "market_start_ms": meta.market_start_ms,
            "market_end_ms": meta.market_end_ms,
            "raw": meta.raw,
        }
        with AppendOnlyNDJSON(self.cache_path) as writer:
            writer.append(entry)


def _metadata_from_cache_entry(entry: dict[str, Any]) -> MarketMetadata | None:
    condition_id = entry.get("condition_id")
    if not condition_id:
        return None
    return MarketMetadata(
        condition_id=condition_id,
        slug=entry.get("slug"),
        market_start_ms=entry.get("market_start_ms"),
        market_end_ms=entry.get("market_end_ms"),
        raw=entry.get("raw") or {},
    )


def _metadata_from_gamma_body(condition_id: str, body: str) -> MarketMetadata | None:
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        logger.warning("gamma returned non-JSON body",
                       extra={"condition_id": condition_id})
        return None
    markets = parsed if isinstance(parsed, list) else parsed.get("data", [])
    if not isinstance(markets, list):
        return None
    for market in markets:
        if not isinstance(market, dict) or market.get("conditionId") != condition_id:
            continue
        slug = market.get("slug")
        end_ms = parse_ts_to_ms(market.get("endDate"))
        # gamma startDate is the market CREATION time, not the 5m window start
        # (verified live). When the slug embeds the window start, derive the
        # true window from it; otherwise fall back to startDate (flagged
        # downstream by the ledger's consistency checks).
        start_ms = parse_ts_to_ms(market.get("startDate"))
        if isinstance(slug, str):
            match = _SLUG_WINDOW_RE.fullmatch(slug)
            if match is not None:
                start_ms = int(match.group(1)) * 1000
                if end_ms is None:
                    end_ms = start_ms + 300_000
        return MarketMetadata(
            condition_id=condition_id,
            slug=slug,
            market_start_ms=start_ms,
            market_end_ms=end_ms,
            raw=market,
        )
    return None


# ---------------------------------------------------------------------------
# Coverage provider (book / BTC data availability)
# ---------------------------------------------------------------------------

@dataclass
class MarketCoverage:
    poly_book_coverage_pct: float | None = None
    btc_coverage_pct: float | None = None
    book_expected: bool = False  # a recorded session overlapped this market
    btc_expected: bool = False


class CoverageProvider(Protocol):
    def market_coverage(
        self, condition_id: str, market_start_ms: int | None, market_end_ms: int | None
    ) -> MarketCoverage: ...


class NullCoverageProvider:
    """No live collection data available (fresh installs / pure history mode)."""

    def market_coverage(
        self, condition_id: str, market_start_ms: int | None, market_end_ms: int | None
    ) -> MarketCoverage:
        return MarketCoverage()


# ---------------------------------------------------------------------------
# Ledger assembly
# ---------------------------------------------------------------------------

LEDGER_COLUMNS = [
    "market_id", "condition_id", "slug",
    "market_start_ms", "market_end_ms",
    "initial_direction", "initial_first_timestamp_ms", "initial_qty",
    "first_opp_direction", "first_opp_start_ms", "first_opp_end_ms",
    "first_opp_qty", "first_opp_vwap", "first_opp_fill_count",
    "up_qty_before_first_opp", "down_qty_before_first_opp",
    "old_direction_qty",
    "y30", "y30_horizon_eligible", "y30_event_ts_ms",
    "clean_flag", "exclude_reason", "exclude_detail",
    "poly_book_coverage_pct", "btc_coverage_pct",
    "episode_rule_version", "n_buy_fills", "n_sell_fills",
]


def build_ledger_rows(
    fills: list[Fill],
    metadata_provider: MarketMetadataProvider,
    coverage_provider: CoverageProvider | None = None,
    scope_slug_prefix: str | None = None,
) -> list[dict[str, Any]]:
    """Assemble one ledger row per market present in *fills*.

    Rows are returned sorted by condition_id; every market in the input
    produces exactly one row (excluded markets included, with reasons).

    ``scope_slug_prefix``: when set (e.g. ``"btc-updown-5m-"``), markets whose
    slug does not match are excluded with ``OTHER`` + detail
    (``out_of_scope_market``). They stay in the ledger so reconciliation
    still balances raw == clean + excluded -- no silent drops of any kind,
    even for market series outside the study universe (std0 also trades
    sol/eth-updown series).
    """
    coverage_provider = coverage_provider or NullCoverageProvider()

    by_market: dict[str, list[Fill]] = defaultdict(list)
    for fill in fills:
        if fill.condition_id is not None:
            by_market[fill.condition_id].append(fill)

    rows: list[dict[str, Any]] = []
    for condition_id in sorted(by_market):
        rows.append(
            _build_row(condition_id, by_market[condition_id],
                       metadata_provider, coverage_provider, scope_slug_prefix)
        )
    return rows


def _build_row(
    condition_id: str,
    market_fills: list[Fill],
    metadata_provider: MarketMetadataProvider,
    coverage_provider: CoverageProvider,
    scope_slug_prefix: str | None = None,
) -> dict[str, Any]:
    buy_fills = [f for f in market_fills if f.is_buy]
    sell_fills = [f for f in market_fills if not f.is_buy]

    row: dict[str, Any] = {col: None for col in LEDGER_COLUMNS}
    row.update({
        "condition_id": condition_id,
        "clean_flag": True,
        "exclude_reason": None,
        "exclude_detail": None,
        "episode_rule_version": EPISODE_RULE_VERSION,
        "n_buy_fills": len(buy_fills),
        "n_sell_fills": len(sell_fills),
    })

    def exclude(reason: str, detail: str | None = None) -> dict[str, Any]:
        row["clean_flag"] = False
        row["exclude_reason"] = reason
        if reason == EXCLUDE_OTHER:
            # OTHER without detail is a bug: enforce it loudly.
            row["exclude_detail"] = detail or "missing detail (BUG: detail required)"
        elif detail:
            row["exclude_detail"] = detail
        return row

    # -- 1. BUY fills present ----------------------------------------------
    if not buy_fills:
        return exclude(EXCLUDE_FIELD_INCOMPLETE, "market has no BUY fills for trader")

    # -- 2. field / timestamp validity ---------------------------------------
    invalid_reasons = [
        validate_buy_fill(f) for f in buy_fills if validate_buy_fill(f) is not None
    ]
    if invalid_reasons:
        if EXCLUDE_TIMESTAMP_INVALID in invalid_reasons:
            return exclude(
                EXCLUDE_TIMESTAMP_INVALID,
                f"{len(invalid_reasons)} BUY fill(s) with unparseable timestamp",
            )
        return exclude(
            EXCLUDE_FIELD_INCOMPLETE,
            f"{len(invalid_reasons)} BUY fill(s) with invalid size/price/fields",
        )

    # -- 3. initial direction (incl. same-second ambiguity) ------------------
    initial = analyze_initial_direction(buy_fills)
    if initial.too_many_outcomes:
        return exclude(
            EXCLUDE_FIELD_INCOMPLETE,
            f"unexpected outcomes {list(initial.distinct_outcomes)}; "
            "not a two-outcome Up/Down market",
        )
    if initial.ambiguous:
        # Spec Test C: never guess the Up/Down order.
        return exclude(EXCLUDE_SAME_SECOND_AMBIGUITY)

    row["initial_direction"] = initial.direction
    row["initial_first_timestamp_ms"] = initial.first_timestamp_ms

    # -- 4. episodes + first opposite ----------------------------------------
    episodes_result = build_episodes(market_fills)
    episodes = episodes_result.episodes_for(condition_id)
    first_opp = find_first_opposite(episodes, initial)
    if first_opp is not None:
        row["first_opp_direction"] = first_opp.direction
        row["first_opp_start_ms"] = first_opp.episode_start_ms
        row["first_opp_end_ms"] = first_opp.episode_end_ms
        row["first_opp_qty"] = first_opp.total_shares
        row["first_opp_vwap"] = first_opp.vwap
        row["first_opp_fill_count"] = first_opp.fill_count
        initial_episodes = [
            e for e in episodes if e.direction == initial.direction
        ]
        if initial_episodes:
            row["initial_qty"] = min(
                initial_episodes, key=lambda e: e.episode_start_ms
            ).total_shares

    # quantities before the first opposite episode starts
    # (with no first opposite, cutoff=inf -> totals for the market)
    cutoff: int | float = first_opp.episode_start_ms if first_opp is not None else float("inf")
    up_qty = qty_of_direction_before(buy_fills, "Up", cutoff)
    down_qty = qty_of_direction_before(buy_fills, "Down", cutoff)
    row["up_qty_before_first_opp"] = up_qty
    row["down_qty_before_first_opp"] = down_qty
    if first_opp is not None:
        row["old_direction_qty"] = (
            up_qty if initial.direction == "Up" else down_qty
        )

    # -- 4.5 study-universe scope check ----------------------------------------
    # std0 also trades other series (sol-updown-15m-*, eth-updown-5m-*...).
    # Markets outside the BTC-5m universe are excluded loudly (OTHER + detail)
    # rather than dropped, so reconciliation still balances over raw fills.
    if scope_slug_prefix is not None:
        market_slug = _fill_slug(market_fills)
        if market_slug is not None and not market_slug.startswith(scope_slug_prefix):
            return exclude(
                EXCLUDE_OTHER,
                f"out_of_scope_market: slug '{market_slug}' is outside the "
                f"study universe '{scope_slug_prefix}*' (BTC Up or Down 5m)",
            )

    # -- 5. market metadata ----------------------------------------------------
    meta = metadata_provider.get(condition_id)
    if meta is None:
        return exclude(EXCLUDE_MARKET_METADATA_MISSING,
                       "no usable market window: slug does not encode one and "
                       "metadata lookup failed")
    row["market_id"] = meta.slug or _fill_slug(market_fills)
    row["slug"] = meta.slug or _fill_slug(market_fills)
    row["market_start_ms"] = meta.market_start_ms
    row["market_end_ms"] = meta.market_end_ms
    if first_opp is not None and meta.market_end_ms is None:
        return exclude(EXCLUDE_MARKET_METADATA_MISSING, "metadata lacks endDate")

    # -- 6. trades-vs-metadata consistency (OTHER requires detail) -------------
    if first_opp is not None and meta.market_end_ms is not None:
        if first_opp.episode_end_ms > meta.market_end_ms + MARKET_TIME_TOLERANCE_MS:
            return exclude(
                EXCLUDE_OTHER,
                f"first_opp_end {first_opp.episode_end_ms} is after market_end "
                f"{meta.market_end_ms} beyond tolerance",
            )
        if (
            meta.market_start_ms is not None
            and initial.first_timestamp_ms is not None
            and initial.first_timestamp_ms < meta.market_start_ms - MARKET_TIME_TOLERANCE_MS
        ):
            return exclude(
                EXCLUDE_OTHER,
                f"first BUY {initial.first_timestamp_ms} precedes market_start "
                f"{meta.market_start_ms} beyond tolerance",
            )

    # -- 7. coverage (only enforced where a session promised data) -------------
    coverage = coverage_provider.market_coverage(
        condition_id, meta.market_start_ms, meta.market_end_ms
    )
    row["poly_book_coverage_pct"] = coverage.poly_book_coverage_pct
    row["btc_coverage_pct"] = coverage.btc_coverage_pct
    if coverage.book_expected and not (coverage.poly_book_coverage_pct or 0) > 0:
        return exclude(EXCLUDE_BOOK_DATA_MISSING,
                       "book session overlapped this market but no book data found")
    if coverage.btc_expected and not (coverage.btc_coverage_pct or 0) > 0:
        return exclude(EXCLUDE_BTC_DATA_MISSING,
                       "btc session overlapped this market but no btc data found")

    # -- 8. Y30 -----------------------------------------------------------------
    if first_opp is not None:
        same_direction_ts = [
            f.timestamp_ms for f in buy_fills
            if f.outcome == first_opp.direction and f.timestamp_ms is not None
        ]
        y30, y30_event_ts = compute_y30(first_opp.episode_end_ms, same_direction_ts)
        row["y30"] = y30
        row["y30_event_ts_ms"] = y30_event_ts
        row["y30_horizon_eligible"] = y30_horizon_eligible(
            first_opp.episode_end_ms, meta.market_end_ms
        )
    return row


def _fill_slug(market_fills: list[Fill]) -> str | None:
    for fill in market_fills:
        if fill.slug:
            return fill.slug
    return None
