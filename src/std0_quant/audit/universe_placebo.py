"""Audit 5 - universe placebo (Phase 1.5, spec section 8).

Research question: are the Phase 1 headline rates (FirstOpposite rate,
observable Y30 positive rate) specific to the BTC-5m universe, or do they
reappear when the SAME frozen construction (episode rule, Y30 window,
exclusion rules, slug-derived windows) is applied to structurally similar
universes (BTC-15m, ETH-5m, SOL-5m, XRP-5m)?

What this is NOT: not a search for new alpha, not a claim that std0 trades
these universes the same way, and not a causal statement. A placebo
universe with a similar rate does not explain BTC-5m; one with a very
different rate does not prove BTC-5m is special either - the universes
differ in many ways at once.

Placebo ledgers are rebuilt by the SAME ``build_ledger_rows`` with a
different ``scope_slug_prefix`` and ``SlugWindowMetadataProvider`` window:
no slug is "fixed", no exclusion is relaxed, no definition is adjusted. A
universe where the wallet has too little clean data is reported as
``NOT_COMPARABLE`` (kept, never silently dropped).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

# Below this many clean markets the rates are too noisy to compare.
MIN_COMPARABLE_CLEAN_MARKETS = 30


@dataclass(frozen=True)
class UniverseSpec:
    name: str
    slug_prefix: str
    window_seconds: int


# Placebo universes: same construction, different scope. Window seconds
# follow each slug family's real cadence (15m -> 900s, 5m -> 300s).
PLACEBO_UNIVERSES: tuple[UniverseSpec, ...] = (
    UniverseSpec("BTC-15m", "btc-updown-15m-", 900),
    UniverseSpec("ETH-5m", "eth-updown-5m-", 300),
    UniverseSpec("SOL-5m", "sol-updown-5m-", 300),
    UniverseSpec("XRP-5m", "xrp-updown-5m-", 300),
)


@dataclass
class UniverseSummary:
    name: str
    slug_prefix: str
    window_seconds: int
    n_fills: int = 0
    n_markets: int = 0
    n_clean: int = 0
    n_excluded: int = 0
    exclusion_reasons: dict[str, int] = field(default_factory=dict)
    n_first_opp: int = 0
    n_eligible: int = 0
    y30_positive: int = 0
    y30_censored: int = 0
    min_clean_markets: int = MIN_COMPARABLE_CLEAN_MARKETS

    @property
    def first_opp_rate(self) -> float | None:
        return self.n_first_opp / self.n_clean if self.n_clean else None

    @property
    def y30_positive_rate_observable(self) -> float | None:
        return self.y30_positive / self.n_eligible if self.n_eligible else None

    @property
    def comparable(self) -> bool:
        return self.not_comparable_reason is None

    @property
    def not_comparable_reason(self) -> str | None:
        if self.n_fills == 0:
            return "no fills in universe"
        if self.n_clean < self.min_clean_markets:
            return (
                f"only {self.n_clean} clean markets "
                f"(< {self.min_clean_markets})"
            )
        if self.n_eligible == 0:
            return "no horizon-eligible markets"
        return None


@dataclass
class UniverseComparisonRow:
    name: str
    first_opp_rate: float | None
    y30_positive_rate_observable: float | None
    delta_first_opp_pp: float | None
    delta_y30_pp: float | None
    comparable: bool
    note: str | None = None


@dataclass
class UniversePlaceboResult:
    reference: UniverseSummary
    placebos: list[UniverseSummary] = field(default_factory=list)

    @property
    def comparison(self) -> list[UniverseComparisonRow]:
        rows: list[UniverseComparisonRow] = []
        for p in self.placebos:
            delta_fo = _delta_pp(p.first_opp_rate, self.reference.first_opp_rate)
            delta_y = _delta_pp(
                p.y30_positive_rate_observable,
                self.reference.y30_positive_rate_observable,
            )
            rows.append(
                UniverseComparisonRow(
                    name=p.name,
                    first_opp_rate=p.first_opp_rate,
                    y30_positive_rate_observable=p.y30_positive_rate_observable,
                    delta_first_opp_pp=delta_fo,
                    delta_y30_pp=delta_y,
                    comparable=p.comparable,
                    note=p.not_comparable_reason,
                )
            )
        return rows

    @property
    def status(self) -> str:
        # Informational audit: there is no pass/fail notion of "wrong
        # universe" - the audit reports whether the pattern replicates.
        comparable = [p for p in self.placebos if p.comparable]
        if not comparable:
            return "NOT_COMPUTABLE"
        return "REPORTED"


def _delta_pp(rate: float | None, reference: float | None) -> float | None:
    if rate is None or reference is None:
        return None
    return (rate - reference) * 100


def summarize_universe(
    spec: UniverseSpec,
    ledger_rows: Sequence[Mapping[str, Any]],
    n_fills: int,
    min_clean_markets: int = MIN_COMPARABLE_CLEAN_MARKETS,
) -> UniverseSummary:
    """Summarize one universe's ledger rows (built by the frozen pipeline)."""
    summary = UniverseSummary(
        name=spec.name,
        slug_prefix=spec.slug_prefix,
        window_seconds=spec.window_seconds,
        n_fills=n_fills,
        min_clean_markets=min_clean_markets,
    )
    for row in ledger_rows:
        summary.n_markets += 1
        if row.get("clean_flag"):
            summary.n_clean += 1
            if row.get("first_opp_end_ms") is not None:
                summary.n_first_opp += 1
                if row.get("y30_horizon_eligible"):
                    summary.n_eligible += 1
                    if row.get("y30") == 1:
                        summary.y30_positive += 1
                else:
                    summary.y30_censored += 1
        else:
            summary.n_excluded += 1
            reason = row.get("exclude_reason") or "UNKNOWN"
            summary.exclusion_reasons[reason] = (
                summary.exclusion_reasons.get(reason, 0) + 1
            )
    return summary


def run_universe_placebo(
    reference: UniverseSummary,
    placebos: Sequence[UniverseSummary],
) -> UniversePlaceboResult:
    return UniversePlaceboResult(reference=reference, placebos=list(placebos))
