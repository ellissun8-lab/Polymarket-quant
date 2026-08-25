"""Audit 1 - FirstOpposite selection bias (Phase 1.5, spec section 4).

Research question: are clean BTC-5m markets WITH a FirstOpposite (G1)
systematically different from clean BTC-5m markets WITHOUT one (G0) on
variables that exist BEFORE the FirstOpposite?

Read-only: consumes ledger rows (+ reconstructed aggregates); produces
statistics only. No label is modified.

Magnitude buckets (descriptive, NOT significance tests):

* |SMD| < 0.10   small
* 0.10 - 0.20    noticeable
* > 0.20         material

``selection_bias_material = True`` (-> WARN, never FAIL) when at least two
core pre-FirstOpposite variables show |SMD| > 0.20. Selection bias can be
a genuine behavioral structure, so WARN is informational.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

# Variables that are fully determined BEFORE the FirstOpposite can exist
# (market structure + std0's initial action). Variables describing whole-
# market totals accumulate fills after t0 as well and are flagged
# post-inclusive: they are reported for context but excluded from the WARN
# rule, honoring "不得使用 FirstOpposite 之后才产生的信息描述组间差异".
PRE_VARIABLES = (
    "initial_qty",
    "market_start_ms",
    "market_end_ms",
    "initial_first_timestamp_ms",
    "seconds_from_market_start_to_initial",
    "seconds_remaining_at_initial",
)
POST_INCLUSIVE_VARIABLES = (
    "n_buy_fills",
    "n_sell_fills",
    "total_buy_qty",
    "total_sell_qty",
    "total_fill_count",
)
ALL_VARIABLES = PRE_VARIABLES + POST_INCLUSIVE_VARIABLES

SMD_MATERIAL = 0.20
SMD_NOTICEABLE = 0.10
MATERIAL_PRE_VARIABLES_FOR_WARN = 2


@dataclass
class GroupStats:
    n: int
    missing: int = 0
    mean: float | None = None
    median: float | None = None
    std: float | None = None
    p25: float | None = None
    p75: float | None = None


@dataclass
class VariableComparison:
    variable: str
    pre_first_opposite: bool
    g1: GroupStats
    g0: GroupStats
    smd: float | None
    smd_note: str | None = None
    magnitude: str = "undefined"  # small | noticeable | material | undefined


@dataclass
class SelectionBiasResult:
    g1_count: int
    g0_count: int
    comparisons: list[VariableComparison] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def material_pre_variables(self) -> list[str]:
        return [
            c.variable for c in self.comparisons
            if c.pre_first_opposite
            and c.smd is not None
            and abs(c.smd) > SMD_MATERIAL
        ]

    @property
    def selection_bias_material(self) -> bool:
        return len(self.material_pre_variables) >= MATERIAL_PRE_VARIABLES_FOR_WARN

    @property
    def status(self) -> str:
        return "WARN" if self.selection_bias_material else "PASS"

    def max_abs_smd(self) -> float | None:
        smds = [abs(c.smd) for c in self.comparisons if c.smd is not None]
        return max(smds) if smds else None

    def median_abs_smd(self) -> float | None:
        smds = sorted(abs(c.smd) for c in self.comparisons if c.smd is not None)
        if not smds:
            return None
        mid = len(smds) // 2
        if len(smds) % 2:
            return smds[mid]
        return (smds[mid - 1] + smds[mid]) / 2.0


def describe(values: Sequence[float]) -> GroupStats:
    """Mean / median / std / p25 / p75 with missing-value accounting."""
    vals = [float(v) for v in values if v is not None]
    stats = GroupStats(n=len(vals), missing=len(values) - len(vals))
    if not vals:
        return stats
    vals.sort()
    stats.mean = sum(vals) / len(vals)
    stats.median = _percentile(vals, 50)
    stats.p25 = _percentile(vals, 25)
    stats.p75 = _percentile(vals, 75)
    if len(vals) >= 2:
        mean = stats.mean
        stats.std = math.sqrt(
            sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)
        )
    else:
        stats.std = 0.0
    return stats


def _percentile(sorted_vals: list[float], pct: float) -> float:
    """Linear-interpolation percentile on an already-sorted list."""
    if not sorted_vals:
        raise ValueError("empty")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    rank = (len(sorted_vals) - 1) * pct / 100.0
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return sorted_vals[int(rank)]
    frac = rank - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def compute_smd(values_g1: Sequence[float],
                values_g0: Sequence[float]) -> tuple[float | None, str | None]:
    """Standardized Mean Difference (pooled-std form).

    Returns ``(smd, note)``; ``smd`` is None when undefined. Zero-variance
    handling: identical constant groups -> 0.0; constant-but-different (or
    empty) groups -> None with an explanatory note (never a crash, never a
    fabricated number).
    """
    v1 = [float(v) for v in values_g1 if v is not None]
    v0 = [float(v) for v in values_g0 if v is not None]
    if not v1 or not v0:
        return None, "undefined: one group has no observations"
    mean1 = sum(v1) / len(v1)
    mean0 = sum(v0) / len(v0)
    var1 = _variance(v1, mean1)
    var0 = _variance(v0, mean0)
    pooled = (var1 + var0) / 2.0
    if pooled == 0.0:
        if mean1 == mean0:
            return 0.0, "zero variance in both groups; identical constants"
        return None, "undefined: zero pooled variance with different means"
    return (mean1 - mean0) / math.sqrt(pooled), None


def _variance(vals: list[float], mean: float) -> float:
    if len(vals) < 2:
        return 0.0
    return sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)


def magnitude_of(smd: float | None) -> str:
    if smd is None:
        return "undefined"
    absolute = abs(smd)
    if absolute < SMD_NOTICEABLE:
        return "small"
    if absolute <= SMD_MATERIAL:
        return "noticeable"
    return "material"


def extract_variables(
    row: dict[str, Any],
    extras: dict[str, Any] | None = None,
) -> dict[str, float | None]:
    """Build the Phase-1-safe variable set for one clean market row.

    ``extras`` may supply reconstructed aggregates (``total_buy_qty``,
    ``total_sell_qty``, ``total_fill_count``) and an ``initial_qty``
    override for markets where the ledger leaves it empty (no
    FirstOpposite -> Phase 1 ledger does not populate initial_qty).
    """
    extras = extras or {}
    out: dict[str, float | None] = {}
    out["initial_qty"] = row.get("initial_qty")
    if out["initial_qty"] is None:
        out["initial_qty"] = extras.get("initial_qty")
    out["n_buy_fills"] = row.get("n_buy_fills")
    out["n_sell_fills"] = row.get("n_sell_fills")
    out["market_start_ms"] = row.get("market_start_ms")
    out["market_end_ms"] = row.get("market_end_ms")
    out["initial_first_timestamp_ms"] = row.get("initial_first_timestamp_ms")
    start = row.get("market_start_ms")
    end = row.get("market_end_ms")
    first = row.get("initial_first_timestamp_ms")
    out["seconds_from_market_start_to_initial"] = (
        (first - start) / 1000.0
        if first is not None and start is not None else None
    )
    out["seconds_remaining_at_initial"] = (
        (end - first) / 1000.0
        if first is not None and end is not None else None
    )
    out["total_buy_qty"] = extras.get("total_buy_qty")
    out["total_sell_qty"] = extras.get("total_sell_qty")
    out["total_fill_count"] = extras.get(
        "total_fill_count",
        (row.get("n_buy_fills") or 0) + (row.get("n_sell_fills") or 0)
        if row.get("n_buy_fills") is not None
        and row.get("n_sell_fills") is not None
        else None,
    )
    return out


def run_selection_bias(
    rows: Iterable[dict[str, Any]],
    extras_by_market: dict[str, dict[str, Any]] | None = None,
) -> SelectionBiasResult:
    """Compare G1 (FirstOpposite present) vs G0 (absent) clean markets."""
    extras_by_market = extras_by_market or {}
    g1_vars: dict[str, list[float | None]] = {v: [] for v in ALL_VARIABLES}
    g0_vars: dict[str, list[float | None]] = {v: [] for v in ALL_VARIABLES}
    n1 = n0 = 0

    for row in rows:
        has_first_opp = row.get("first_opp_end_ms") is not None
        variables = extract_variables(row, extras_by_market.get(row.get("condition_id")))
        target = g1_vars if has_first_opp else g0_vars
        if has_first_opp:
            n1 += 1
        else:
            n0 += 1
        for name, value in variables.items():
            target[name].append(value)

    result = SelectionBiasResult(g1_count=n1, g0_count=n0)
    for name in ALL_VARIABLES:
        smd, note = compute_smd(g1_vars[name], g0_vars[name])
        comparison = VariableComparison(
            variable=name,
            pre_first_opposite=name in PRE_VARIABLES,
            g1=describe(g1_vars[name]),
            g0=describe(g0_vars[name]),
            smd=smd,
            smd_note=note,
            magnitude=magnitude_of(smd),
        )
        result.comparisons.append(comparison)

    result.notes.append(
        "Post-inclusive variables (whole-market totals incl. fills after "
        "t0) are reported for context only and excluded from the WARN rule."
    )
    if result.selection_bias_material:
        result.notes.append(
            "WARN: >= 2 core pre-FirstOpposite variables exceed |SMD| > "
            f"{SMD_MATERIAL}: {result.material_pre_variables}. Selection "
            "bias can be genuine behavioral structure; investigate, do not "
            "treat as a data error."
        )
    return result
