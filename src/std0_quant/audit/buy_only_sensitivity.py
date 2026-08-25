"""Audit 2 - BUY-only label sensitivity (Phase 1.5, spec section 5).

Research question: the frozen ``y30`` label asks "did std0 BUY the
FirstOpposite direction again in ``(t0, t0+30s]``?". That event set is
BUY-only. In a two-outcome market, SELLing the *initial* direction token
is economically similar to re-entering the opposite direction. If many
markets have such SELLs inside the window but no opposite BUY, then the
BUY-only ``y30`` cannot be read as "did std0 re-engage the opposite
direction" - only as the narrower "did std0 BUY the opposite token".

AUDIT-ONLY sensitivity label (never a replacement)::

    y30_directional_sensitivity = 1 iff in (t0, t0+30s]
        (initial == Up  and (BUY Down or SELL Up)) or
        (initial == Down and (BUY Up  or SELL Down))

The BUY half of the disjunction is exactly the frozen ``y30`` event set
(FirstOpposite direction == the outcome opposing ``initial_direction``),
so ``y30_directional_sensitivity >= y30`` pointwise; the SELL half can
only add positives. Read-only: no ledger row, no frozen definition, and
no on-disk artifact is modified.

Window boundaries are identical to the frozen definition: an event at
exactly ``t0`` is NOT inside (it would have merged into the FirstOpposite
episode under the 3s rule), an event at exactly ``t0 + 30s`` IS inside,
and ``t0 + 30s + 1`` is outside. Horizon stays 30s; this audit varies the
event definition, not the horizon.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from std0_quant.audit.point_in_time import is_same_second
from std0_quant.events.event_ledger import Y30_HORIZON_SECONDS

# |rate_sensitivity - rate_original| below this is "label robust to the
# BUY-only restriction" (PASS); at or above it the Phase 1 wording must
# stay BUY-specific (WARN). Descriptive threshold, not a significance test.
SENSITIVITY_WARN_PP = 1.0

UP = "Up"
DOWN = "Down"


@dataclass
class MarketFillWindows:
    """Per-market fill timestamps feeding the sensitivity label.

    ``buy_ts_by_outcome`` / ``sell_ts_by_outcome`` map outcome name
    ("Up"/"Down") to sorted-or-unsorted fill timestamps in ms. Only the
    two outcomes matter; extra keys are ignored.
    """

    t0_ms: int
    initial_direction: str
    buy_ts_by_outcome: dict[str, list[int]] = field(default_factory=dict)
    sell_ts_by_outcome: dict[str, list[int]] = field(default_factory=dict)
    market_end_ms: int | None = None
    y30: int | None = None  # frozen label, for cross-checking


@dataclass
class DirectionalSensitivityOutcome:
    market_id: str
    t0_ms: int
    initial_direction: str
    opposite_direction: str
    horizon_eligible: bool
    y30: int | None
    y30_directional_sensitivity: int
    buy_opposite_event_ms: int | None = None   # earliest opposite BUY in window
    sell_initial_event_ms: int | None = None   # earliest initial-side SELL in window
    sell_only_upgrade: bool = False            # y30==0 but sensitivity==1
    consistency_error: str | None = None       # set when y30 disagrees with fills


@dataclass
class BuyOnlySensitivityResult:
    n_markets: int = 0
    n_eligible: int = 0
    n_with_first_opposite: int = 0
    original_positive: int = 0
    sensitivity_positive: int = 0
    original_rate: float | None = None
    sensitivity_rate: float | None = None
    delta_pp: float | None = None
    agreement_rate: float | None = None
    n_sell_only_upgrades: int = 0
    n_both_event_types: int = 0
    n_consistency_errors: int = 0
    per_market: list[DirectionalSensitivityOutcome] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.delta_pp is None:
            return "NOT_COMPUTABLE"
        return "WARN" if self.delta_pp >= SENSITIVITY_WARN_PP else "PASS"

    @property
    def sell_only_share_of_eligible(self) -> float | None:
        if self.n_eligible == 0:
            return None
        return self.n_sell_only_upgrades / self.n_eligible


def opposite_outcome(direction: str) -> str:
    if direction == UP:
        return DOWN
    if direction == DOWN:
        return UP
    raise ValueError(f"not a two-outcome direction: {direction!r}")


def _events_in_window(timestamps: Iterable[int], t0_ms: int) -> list[int]:
    window_end = t0_ms + Y30_HORIZON_SECONDS * 1000
    return sorted(
        ts for ts in timestamps if ts is not None and t0_ms < ts <= window_end
    )


def compute_directional_sensitivity(
    market_id: str,
    fills: MarketFillWindows,
) -> DirectionalSensitivityOutcome:
    """Audit-only label for one market (see module docstring)."""
    initial = fills.initial_direction
    opposite = opposite_outcome(initial)
    buy_events = _events_in_window(
        fills.buy_ts_by_outcome.get(opposite, ()), fills.t0_ms
    )
    sell_events = _events_in_window(
        fills.sell_ts_by_outcome.get(initial, ()), fills.t0_ms
    )
    sensitivity = 1 if (buy_events or sell_events) else 0

    eligible = (
        fills.market_end_ms is not None
        and fills.market_end_ms >= fills.t0_ms + Y30_HORIZON_SECONDS * 1000
    )

    error: str | None = None
    if fills.y30 is not None:
        y30_from_fills = 1 if buy_events else 0
        if y30_from_fills != fills.y30:
            error = (
                f"frozen y30={fills.y30} but window BUY events imply "
                f"{y30_from_fills}"
            )

    return DirectionalSensitivityOutcome(
        market_id=market_id,
        t0_ms=fills.t0_ms,
        initial_direction=initial,
        opposite_direction=opposite,
        horizon_eligible=eligible,
        y30=fills.y30,
        y30_directional_sensitivity=sensitivity,
        buy_opposite_event_ms=buy_events[0] if buy_events else None,
        sell_initial_event_ms=sell_events[0] if sell_events else None,
        sell_only_upgrade=bool(
            fills.y30 == 0 and sensitivity == 1 and eligible
        ),
        consistency_error=error,
    )


def run_buy_only_sensitivity(
    markets: Mapping[str, MarketFillWindows],
) -> BuyOnlySensitivityResult:
    """Aggregate the sensitivity label over clean FirstOpposite markets.

    Rates use the same denominator as Phase 1's observable Y30 rate:
    horizon-eligible markets only (censored markets stay counted but do
    not enter rates - "censored is not negative").
    """
    result = BuyOnlySensitivityResult()
    for market_id, fills in markets.items():
        outcome = compute_directional_sensitivity(market_id, fills)
        result.per_market.append(outcome)
        result.n_markets += 1
        if outcome.consistency_error:
            result.n_consistency_errors += 1
        if outcome.horizon_eligible:
            result.n_eligible += 1
            if outcome.y30 == 1:
                result.original_positive += 1
            if outcome.y30_directional_sensitivity == 1:
                result.sensitivity_positive += 1
                if outcome.buy_opposite_event_ms is not None:
                    if outcome.sell_initial_event_ms is not None:
                        result.n_both_event_types += 1
            if outcome.sell_only_upgrade:
                result.n_sell_only_upgrades += 1
    if result.n_markets:
        result.n_with_first_opposite = result.n_markets  # by construction
    if result.n_eligible:
        result.original_rate = result.original_positive / result.n_eligible
        result.sensitivity_rate = result.sensitivity_positive / result.n_eligible
        result.delta_pp = abs(result.sensitivity_rate - result.original_rate) * 100
        agreed = sum(
            1
            for m in result.per_market
            if m.horizon_eligible and m.y30 is not None
            and m.y30 == m.y30_directional_sensitivity
        )
        result.agreement_rate = agreed / result.n_eligible
    return result


def same_second_boundary_note(t0_ms: int, event_ms: int) -> str | None:
    """Flag events sharing t0's second: order vs t0 is not recoverable.

    Public fills are second-granular. An event in the same second as t0
    could precede or follow the FirstOpposite episode end; the frozen
    (t0, ...] half-open rule handles it deterministically, but the audit
    reports how often this matters.
    """
    if is_same_second(event_ms, t0_ms):
        return (
            f"event {event_ms} shares t0's second; relative order to "
            f"FirstOpposite end is not recoverable from public data"
        )
    return None
