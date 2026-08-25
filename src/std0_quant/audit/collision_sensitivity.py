"""Audit 3 - within-page identity collision sensitivity (Phase 1.5, section 6).

Background: :func:`std0_quant.collectors.std0_trades.trade_identity` hashes
``transactionHash|asset|side|size|price|timestamp|outcomeIndex``. Two
byte-identical fills inside one transaction collapse into one identity, so
a record can be silently dropped as a "duplicate" during dedupe even though
it was a distinct economic fill. The sync counters already report how often
this happens (excess records within a page); this audit re-derives the
collision set from the raw ``api_pages`` bodies (append-only raw data is
never modified) and measures whether excluding collision-affected markets
moves the Phase 1 headline numbers.

Datasets:

* A = all clean markets (frozen Phase 1 truth);
* B = clean markets whose ``condition_id`` never appears in a collision.

Reported: counts, FirstOpposite rate, observable Y30 positive rate,
medians, ``delta_y30_pp``, ``delta_first_opp_pp``, affected share, and
concentration of collisions by date / hour / side / outcome / label.

Rule (descriptive, not a significance test): ``|delta_y30_pp| < 1pp`` and
``|delta_first_opp_pp| < 1pp`` -> LOW_SENSITIVITY, else HIGH_SENSITIVITY.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from std0_quant.collectors.std0_trades import trade_identity

LOW_SENSITIVITY_THRESHOLD_PP = 1.0


@dataclass
class PageCollision:
    """One identity that appears more than once inside a single page."""

    page_label: str
    identity: str
    occurrences: int  # records sharing this identity in this page
    condition_id: str | None
    side: str | None
    outcome: str | None
    timestamp_ms: int | None
    size: float | None

    @property
    def excess_records(self) -> int:
        return self.occurrences - 1


@dataclass
class PageScanResult:
    n_pages: int = 0
    n_records: int = 0
    n_pages_with_collisions: int = 0
    collisions: list[PageCollision] = field(default_factory=list)

    @property
    def n_collisions(self) -> int:
        return len(self.collisions)

    @property
    def excess_records(self) -> int:
        """Records dropped as within-page duplicates (matches the sync counter)."""
        return sum(c.excess_records for c in self.collisions)

    @property
    def affected_condition_ids(self) -> set[str]:
        return {
            c.condition_id for c in self.collisions if c.condition_id
        }

    def collision_fill_count_by_condition(self) -> Counter:
        """Records involved in collisions (all copies), per condition_id."""
        counter: Counter = Counter()
        for c in self.collisions:
            if c.condition_id:
                counter[c.condition_id] += c.occurrences
        return counter


def scan_page_for_collisions(records: Sequence[Mapping[str, Any]],
                             page_label: str = "page") -> list[PageCollision]:
    """Within-page duplicate identities for one API page body.

    Pure: takes the parsed record list of a single page. The script layer
    is responsible for reading and parsing the stored raw envelopes.
    """
    by_identity: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        by_identity.setdefault(trade_identity(dict(record)), []).append(record)
    collisions: list[PageCollision] = []
    for identity, group in by_identity.items():
        if len(group) < 2:
            continue
        first = group[0]
        ts = first.get("timestamp")
        size = first.get("size")
        try:
            size_f = float(size) if size is not None else None
        except (TypeError, ValueError):
            size_f = None
        collisions.append(
            PageCollision(
                page_label=page_label,
                identity=identity,
                occurrences=len(group),
                condition_id=first.get("conditionId"),
                side=first.get("side"),
                outcome=first.get("outcome"),
                timestamp_ms=int(ts) * 1000 if ts is not None else None,
                size=size_f,
            )
        )
    collisions.sort(key=lambda c: (c.page_label, c.identity))
    return collisions


def scan_pages(
    pages: Iterable[tuple[str, Sequence[Mapping[str, Any]]]],
) -> PageScanResult:
    """Scan ``(page_label, records)`` pairs and aggregate collisions."""
    result = PageScanResult()
    for label, records in pages:
        result.n_pages += 1
        result.n_records += len(records)
        collisions = scan_page_for_collisions(records, page_label=label)
        if collisions:
            result.n_pages_with_collisions += 1
            result.collisions.extend(collisions)
    return result


# ---------------------------------------------------------------------------
# Dataset A vs B comparison
# ---------------------------------------------------------------------------

@dataclass
class DatasetStats:
    n_clean: int = 0
    n_first_opp: int = 0
    n_eligible: int = 0
    y30_positive: int = 0
    initial_qty_values: list[float] = field(default_factory=list)

    @property
    def first_opp_rate(self) -> float | None:
        return self.n_first_opp / self.n_clean if self.n_clean else None

    @property
    def y30_positive_rate_observable(self) -> float | None:
        return self.y30_positive / self.n_eligible if self.n_eligible else None

    @property
    def median_initial_qty(self) -> float | None:
        return _median(self.initial_qty_values)


@dataclass
class CollisionSensitivityResult:
    stats_a: DatasetStats = field(default_factory=DatasetStats)
    stats_b: DatasetStats = field(default_factory=DatasetStats)
    n_clean_affected: int = 0
    n_excluded_affected: int = 0  # affected markets outside the clean set
    collision_fill_count: int = 0  # records involved (all copies)
    delta_y30_pp: float | None = None
    delta_first_opp_pp: float | None = None
    share_clean_affected: float | None = None

    @property
    def status(self) -> str:
        if self.delta_y30_pp is None or self.delta_first_opp_pp is None:
            return "NOT_COMPUTABLE"
        if (
            self.delta_y30_pp < LOW_SENSITIVITY_THRESHOLD_PP
            and self.delta_first_opp_pp < LOW_SENSITIVITY_THRESHOLD_PP
        ):
            return "LOW_SENSITIVITY"
        return "HIGH_SENSITIVITY"


def _median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _dataset_stats(rows: Iterable[Mapping[str, Any]]) -> DatasetStats:
    stats = DatasetStats()
    for row in rows:
        stats.n_clean += 1
        if row.get("first_opp_end_ms") is not None:
            stats.n_first_opp += 1
        if row.get("y30_horizon_eligible"):
            stats.n_eligible += 1
            if row.get("y30") == 1:
                stats.y30_positive += 1
        qty = row.get("initial_qty")
        if isinstance(qty, (int, float)):
            stats.initial_qty_values.append(float(qty))
    return stats


def compare_datasets(
    clean_rows: Sequence[Mapping[str, Any]],
    excluded_rows: Sequence[Mapping[str, Any]],
    affected_condition_ids: set[str],
    collision_fill_count_by_condition: Mapping[str, int] | None = None,
) -> CollisionSensitivityResult:
    """Dataset A (all clean) vs Dataset B (collision-free clean)."""
    result = CollisionSensitivityResult()
    rows_b: list[Mapping[str, Any]] = []
    for row in clean_rows:
        cid = row.get("condition_id")
        if cid in affected_condition_ids:
            result.n_clean_affected += 1
        else:
            rows_b.append(row)
    result.stats_a = _dataset_stats(clean_rows)
    result.stats_b = _dataset_stats(rows_b)
    known = {row.get("condition_id") for row in clean_rows}
    for row in excluded_rows:
        if row.get("condition_id") in affected_condition_ids:
            result.n_excluded_affected += 1
    if result.stats_a.n_clean:
        result.share_clean_affected = (
            result.n_clean_affected / result.stats_a.n_clean
        )
    rate_a = result.stats_a.y30_positive_rate_observable
    rate_b = result.stats_b.y30_positive_rate_observable
    if rate_a is not None and rate_b is not None:
        result.delta_y30_pp = abs(rate_a - rate_b) * 100
    fo_a = result.stats_a.first_opp_rate
    fo_b = result.stats_b.first_opp_rate
    if fo_a is not None and fo_b is not None:
        result.delta_first_opp_pp = abs(fo_a - fo_b) * 100
    if collision_fill_count_by_condition is not None:
        result.collision_fill_count = sum(
            count
            for cid, count in collision_fill_count_by_condition.items()
            if cid in known
        )
    return result


# ---------------------------------------------------------------------------
# Concentration
# ---------------------------------------------------------------------------

@dataclass
class CollisionConcentration:
    by_utc_date: dict[str, int] = field(default_factory=dict)
    by_utc_hour: dict[int, int] = field(default_factory=dict)
    by_side: dict[str, int] = field(default_factory=dict)
    by_outcome: dict[str, int] = field(default_factory=dict)
    by_market_top: list[tuple[str, int]] = field(default_factory=list)
    affected_clean_labels: dict[str, int] = field(default_factory=dict)


def collision_concentration(
    collisions: Sequence[PageCollision],
    clean_rows: Sequence[Mapping[str, Any]] | None = None,
    top_markets: int = 10,
) -> CollisionConcentration:
    """Where do collisions sit? Descriptive only - no causal reading."""
    conc = CollisionConcentration()
    for c in collisions:
        if c.timestamp_ms is not None:
            # UTC bucketing; timestamps are second-granular public data
            dt = datetime.fromtimestamp(c.timestamp_ms / 1000, tz=timezone.utc)
            date_key = dt.strftime("%Y-%m-%d")
            conc.by_utc_date[date_key] = conc.by_utc_date.get(date_key, 0) + 1
            hour = dt.hour
            conc.by_utc_hour[hour] = conc.by_utc_hour.get(hour, 0) + 1
        if c.side:
            conc.by_side[c.side] = conc.by_side.get(c.side, 0) + 1
        if c.outcome:
            conc.by_outcome[c.outcome] = conc.by_outcome.get(c.outcome, 0) + 1
    per_market: Counter = Counter()
    for c in collisions:
        if c.condition_id:
            per_market[c.condition_id] += 1
    conc.by_market_top = per_market.most_common(top_markets)
    if clean_rows is not None:
        affected = {
            c.condition_id for c in collisions if c.condition_id
        }
        for row in clean_rows:
            cid = row.get("condition_id")
            if cid in affected:
                if row.get("first_opp_end_ms") is None:
                    label = "no_first_opp"
                elif row.get("y30_horizon_eligible"):
                    label = f"y30={row.get('y30')}"
                else:
                    label = "censored"
                conc.affected_clean_labels[label] = (
                    conc.affected_clean_labels.get(label, 0) + 1
                )
    return conc
