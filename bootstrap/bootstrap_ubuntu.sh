#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$HERE/.." && pwd)"
APP="$ROOT/app"
PYTHON_BIN="${PYTHON_BIN:-python3}"

command -v "$PYTHON_BIN" >/dev/null 2>&1 || {
  echo "Python interpreter not found: $PYTHON_BIN" >&2
  echo "Prefer the Windows-validated Python 3.12 minor, or explicitly set PYTHON_BIN after review." >&2
  exit 1
}

"$PYTHON_BIN" - <<'PY'
import sys
if sys.version_info < (3, 12) or sys.version_info >= (3, 15):
    raise SystemExit(f"Unsupported bootstrap Python: {sys.version.split()[0]} (expected 3.12-3.14)")
print(f"bootstrap Python: {sys.version.split()[0]}")
PY

"$PYTHON_BIN" -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/python" -m pip install --upgrade pip setuptools wheel
"$ROOT/.venv/bin/python" -m pip install -e "$APP[dev]"

cd "$APP"
"$ROOT/.venv/bin/python" -m pytest -q
"$ROOT/.venv/bin/python" - <<'PY'
from std0_quant.config import load_settings
from std0_quant.collectors.gamma_discovery import GAMMA_DISCOVERY_ISOLATION_FIX_VERSION
from std0_quant.collectors.network_stability import NETWORK_ENGINEERING_FIX_VERSION
from std0_quant.audit.coverage_evidence import COVERAGE_EVIDENCE_VERSION, COVERAGE_SELECTION_FIX_VERSION
from std0_quant.audit.eligibility_policy import ELIGIBILITY_POLICY_VERSION
s = load_settings()
assert s.episode.rule == "v1_3sec"
assert s.y30.horizon_seconds == 30
assert s.coverage.bucket_seconds == 1
assert NETWORK_ENGINEERING_FIX_VERSION == "network_stability_fix_v1"
assert COVERAGE_EVIDENCE_VERSION == "coverage_evidence_v2"
assert COVERAGE_SELECTION_FIX_VERSION == "coverage_selection_fix_v1"
assert GAMMA_DISCOVERY_ISOLATION_FIX_VERSION == "gamma_discovery_isolation_fix_v1"
assert ELIGIBILITY_POLICY_VERSION == "prospective_v4_eligibility_v2"
print("static frozen-version checks: PASS")
PY

echo "BOOTSTRAP_COMPLETE_NO_RECORDER_STARTED"
