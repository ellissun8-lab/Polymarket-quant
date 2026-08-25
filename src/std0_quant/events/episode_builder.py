"""Parent episode builder -- FROZEN rule ``v1_3sec``.

Rule (frozen; do not change without explicit approval):

    Fills of the same market, same direction (outcome), executed continuously
    are merged into one parent episode when consecutive same-direction fills
    are at most 3 seconds apart. A gap strictly greater than 3 seconds starts
    a new episode.

Implementation interpretation (documented in README):

* Only BUY fills participate: InitialDirection, FirstOpposite and Y30 are all
  defined on BUYs, and episodes exist to serve those definitions. SELL fills
  are preserved in normalized data but do not form episodes in Phase 1.
* Chaining is per (market, direction): an interleaved BUY of the opposite
  direction does not break a same-direction chain (the frozen rule groups
  "same market, same direction" fills).
* The 3-second window is inclusive: gap == 3000 ms merges, gap == 3001 ms
  splits.
* Fills are sorted by ``(timestamp_ms, fill_id)``. The ``fill_id`` tie-break
  is a determinism device only; it never decides Up-vs-Down ordering (ties
  across directions are handled by the SAME_SECOND_DIRECTION_AMBIGUITY rule).
* BUY fills with unparseable timestamp / non-positive size / out-of-range
  price are excluded from episodes AND reported (never silently dropped);
  the event ledger flags their market FIELD_INCOMPLETE / TIMESTAMP_INVALID.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field

from std0_quant import EPISODE_RULE_VERSION, EPISODE_WINDOW_SECONDS
from std0_quant.events.fills import Fill

logger = logging.getLogger(__name__)

PRICE_MIN = 0.0
PRICE_MAX = 1.0


@dataclass(frozen=True)
class InvalidFill:
    fill_id: str
    condition_id: str | None
    reason: str  # TIMESTAMP_INVALID | FIELD_INCOMPLETE


@dataclass
class Episode:
    market_id: str  # conditionId
    direction: str  # outcome label, e.g. "Up" / "Down"
    episode_start_ms: int
    episode_end_ms: int
    total_shares: float
    total_dollars: float
    vwap: float
    fill_count: int
    constituent_fill_ids: list[str] = field(default_factory=list)
    constituent_tx_hashes: list[str] = field(default_factory=list)
    episode_rule: str = EPISODE_RULE_VERSION


@dataclass
class EpisodeBuildResult:
    episodes: list[Episode]
    invalid_buy_fills: list[InvalidFill]

    def episodes_for(self, condition_id: str) -> list[Episode]:
        return [e for e in self.episodes if e.market_id == condition_id]


def validate_buy_fill(fill: Fill) -> str | None:
    """Return an exclusion reason if the fill cannot support episode math."""
    if fill.timestamp_ms is None:
        return "TIMESTAMP_INVALID"
    if fill.size is None or fill.size <= 0:
        return "FIELD_INCOMPLETE"
    if fill.price is None or not (PRICE_MIN <= fill.price <= PRICE_MAX):
        return "FIELD_INCOMPLETE"
    if fill.condition_id is None or fill.outcome is None:
        return "FIELD_INCOMPLETE"
    return None


def build_episodes(
    fills: list[Fill],
    window_ms: int = EPISODE_WINDOW_SECONDS * 1000,
    rule: str = EPISODE_RULE_VERSION,
) -> EpisodeBuildResult:
    """Build parent episodes from fills (all markets at once)."""
    if rule != EPISODE_RULE_VERSION:
        raise ValueError(
            f"refusing to build episodes with rule {rule!r}: the frozen "
            f"research definition is {EPISODE_RULE_VERSION!r}"
        )

    invalid: list[InvalidFill] = []
    # (condition_id, direction) -> list of fills
    groups: dict[tuple[str, str], list[Fill]] = defaultdict(list)

    for fill in fills:
        if not fill.is_buy:
            continue  # SELL fills are out of episode scope (documented)
        reason = validate_buy_fill(fill)
        if reason is not None:
            invalid.append(InvalidFill(fill.fill_id, fill.condition_id, reason))
            continue
        groups[(fill.condition_id, fill.outcome)].append(fill)

    episodes: list[Episode] = []
    for (condition_id, direction), group in sorted(groups.items()):
        ordered = sorted(group, key=lambda f: (f.timestamp_ms, f.fill_id))
        current: list[Fill] = []
        for fill in ordered:
            if current and fill.timestamp_ms - current[-1].timestamp_ms > window_ms:
                episodes.append(_make_episode(condition_id, direction, current))
                current = []
            current.append(fill)
        if current:
            episodes.append(_make_episode(condition_id, direction, current))

    episodes.sort(key=lambda e: (e.market_id, e.direction, e.episode_start_ms))
    return EpisodeBuildResult(episodes=episodes, invalid_buy_fills=invalid)


def _make_episode(condition_id: str, direction: str, fills: list[Fill]) -> Episode:
    total_shares = sum(f.size for f in fills)  # type: ignore[misc]
    total_dollars = sum(f.price * f.size for f in fills)  # type: ignore[misc]
    vwap = total_dollars / total_shares if total_shares > 0 else float("nan")
    return Episode(
        market_id=condition_id,
        direction=direction,
        episode_start_ms=fills[0].timestamp_ms,  # type: ignore[misc]
        episode_end_ms=fills[-1].timestamp_ms,  # type: ignore[misc]
        total_shares=total_shares,
        total_dollars=total_dollars,
        vwap=vwap,
        fill_count=len(fills),
        constituent_fill_ids=[f.fill_id for f in fills],
        constituent_tx_hashes=[f.transaction_hash or "" for f in fills],
        episode_rule=EPISODE_RULE_VERSION,
    )


def episode_to_row(episode: Episode) -> dict[str, object]:
    """Flat dict form used for the derived episodes parquet file."""
    return {
        "market_id": episode.market_id,
        "direction": episode.direction,
        "episode_start_ms": episode.episode_start_ms,
        "episode_end_ms": episode.episode_end_ms,
        "total_shares": episode.total_shares,
        "total_dollars": episode.total_dollars,
        "vwap": episode.vwap,
        "fill_count": episode.fill_count,
        "constituent_fill_ids": episode.constituent_fill_ids,
        "constituent_tx_hashes": episode.constituent_tx_hashes,
        "episode_rule": episode.episode_rule,
    }
