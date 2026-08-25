"""InitialDirection and FirstOpposite detection (frozen definitions).

Frozen definitions (spec sections 1.3 / 1.4):

* ``initial_direction``: the outcome of std0's earliest BUY in a market.
* If the earliest BUY-Up and earliest BUY-Down share the same timestamp and
  no reliable field recovers the order, do NOT guess: the market is flagged
  ``SAME_SECOND_DIRECTION_AMBIGUITY`` (handled by the event ledger).
* ``FirstOpposite``: the first parent episode of BUYs in the direction
  opposite to ``initial_direction``.

FirstOpposite is a behavioral state node only. Nothing here assumes it is
directional alpha, hedging, inventory management, or anything else.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from std0_quant.events.episode_builder import Episode
from std0_quant.events.fills import Fill

logger = logging.getLogger(__name__)


@dataclass
class InitialDirection:
    direction: str | None
    first_timestamp_ms: int | None
    ambiguous: bool = False
    distinct_outcomes: tuple[str, ...] = ()

    @property
    def too_many_outcomes(self) -> bool:
        return len(self.distinct_outcomes) > 2


def analyze_initial_direction(buy_fills: list[Fill]) -> InitialDirection:
    """Earliest BUY outcome per market.

    ``ambiguous`` is True when two distinct outcomes first appear at the
    exact same timestamp (the API reports seconds, so same-second Up and Down
    BUYs cannot be ordered from public data).
    """
    first_ts: dict[str, int] = {}
    for fill in buy_fills:
        if not fill.is_buy or fill.timestamp_ms is None or fill.outcome is None:
            continue
        current = first_ts.get(fill.outcome)
        if current is None or fill.timestamp_ms < current:
            first_ts[fill.outcome] = fill.timestamp_ms
    if not first_ts:
        return InitialDirection(direction=None, first_timestamp_ms=None)

    outcomes = tuple(sorted(first_ts))
    if len(outcomes) > 2:
        # Not an Up/Down two-outcome market; caller flags FIELD_INCOMPLETE.
        return InitialDirection(
            direction=None,
            first_timestamp_ms=None,
            ambiguous=False,
            distinct_outcomes=outcomes,
        )
    if len(outcomes) == 2 and first_ts[outcomes[0]] == first_ts[outcomes[1]]:
        return InitialDirection(
            direction=None,
            first_timestamp_ms=None,
            ambiguous=True,
            distinct_outcomes=outcomes,
        )
    direction = min(outcomes, key=lambda o: first_ts[o])
    return InitialDirection(
        direction=direction,
        first_timestamp_ms=first_ts[direction],
        distinct_outcomes=outcomes,
    )


def find_first_opposite(
    episodes: list[Episode], initial: InitialDirection
) -> Episode | None:
    """Earliest episode whose direction opposes ``initial.direction``."""
    if initial.direction is None:
        return None
    opposite = [e for e in episodes if e.direction != initial.direction]
    if not opposite:
        return None
    earliest = min(opposite, key=lambda e: (e.episode_start_ms, e.direction))
    if initial.first_timestamp_ms is not None:
        if earliest.episode_start_ms < initial.first_timestamp_ms:
            # Cannot happen with consistent data: initial is the earliest BUY.
            logger.warning(
                "first opposite precedes initial direction (inconsistent data)",
                extra={"market": earliest.market_id},
            )
        elif earliest.episode_start_ms == initial.first_timestamp_ms:
            # Same-second tie across directions; caller must have flagged
            # ambiguity already. Defensive re-check, never a guess:
            logger.warning(
                "first opposite tied with initial direction timestamp",
                extra={"market": earliest.market_id},
            )
    return earliest


def qty_of_direction_before(
    buy_fills: list[Fill], direction: str, cutoff_ms: int
) -> float:
    """Total BUY shares of *direction* strictly before *cutoff_ms*."""
    return sum(
        f.size or 0.0
        for f in buy_fills
        if f.is_buy and f.outcome == direction and f.timestamp_ms is not None
        and f.timestamp_ms < cutoff_ms
    )


def qty_of_direction_total(buy_fills: list[Fill], direction: str) -> float:
    return sum(
        f.size or 0.0
        for f in buy_fills
        if f.is_buy and f.outcome == direction and f.timestamp_ms is not None
    )
