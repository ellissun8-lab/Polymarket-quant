"""Audit tooling: coverage, gap, and reconciliation reporting."""

from std0_quant.audit.coverage import (
    FileCoverageProvider,
    coverage_pct,
    bounded_state_coverage_pct,
    find_gaps,
    stream_coverage,
)
from std0_quant.audit.leakage import (
    FutureLeakageError,
    assert_no_future_leakage,
)
from std0_quant.audit.reconciliation import (
    ReconciliationError,
    ReconciliationReport,
    assert_reconciles,
    build_reconciliation,
)

__all__ = [
    "FileCoverageProvider",
    "coverage_pct",
    "bounded_state_coverage_pct",
    "find_gaps",
    "stream_coverage",
    "FutureLeakageError",
    "assert_no_future_leakage",
    "ReconciliationError",
    "ReconciliationReport",
    "assert_reconciles",
    "build_reconciliation",
]
