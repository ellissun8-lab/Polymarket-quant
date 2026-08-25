#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$HERE/.." && pwd)"
APP="$ROOT/app"
PY="$ROOT/.venv/bin/python"
EXPECTED_SETTINGS_SHA="580abdc9a7ea5e34cc854d1ded12de35703bfcbbf2f216b1020a04cd6a0b05ae"

fail() { echo "VERIFY_FAIL: $*" >&2; exit 1; }
pass() { echo "VERIFY_PASS: $*"; }

disk_kb="$(df -Pk "$ROOT" | awk 'NR==2 {print $4}')"
(( disk_kb >= 10 * 1024 * 1024 )) || fail "less than 10GiB free disk"
pass "disk space"

mem_kb="$(awk '/MemTotal/ {print $2}' /proc/meminfo)"
swap_kb="$(awk '/SwapTotal/ {print $2}' /proc/meminfo)"
(( mem_kb >= 1500 * 1024 )) || fail "less than 1.5GiB RAM"
(( swap_kb >= 1024 * 1024 )) || fail "less than 1GiB swap"
pass "RAM and swap"

timezone="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
[[ "$timezone" == "UTC" || "$timezone" == "Etc/UTC" ]] || fail "timezone is not UTC: $timezone"
if command -v chronyc >/dev/null 2>&1; then
  chronyc tracking >/dev/null || fail "chrony tracking failed"
else
  synced="$(timedatectl show -p NTPSynchronized --value 2>/dev/null || true)"
  [[ "$synced" == "yes" ]] || fail "NTP synchronization not confirmed"
fi
pass "UTC and clock synchronization"

[[ -x "$PY" ]] || fail "bootstrap venv missing"
"$PY" - <<'PY'
import sys
assert (3, 12) <= sys.version_info[:2] < (3, 15), sys.version
print("Python", sys.version.split()[0])
PY
pass "Python version"

cd "$APP"
"$PY" -c 'import std0_quant'
"$PY" -m pytest -q
pass "project import and pytest"

actual_settings_sha="$(sha256sum config/settings.yaml | awk '{print $1}')"
[[ "$actual_settings_sha" == "$EXPECTED_SETTINGS_SHA" ]] || fail "settings SHA mismatch"
pass "settings SHA"

"$PY" - <<'PY'
import json
from pathlib import Path
from std0_quant import EPISODE_RULE_VERSION, Y30_HORIZON_SECONDS
from std0_quant.audit.coverage_evidence import COVERAGE_EVIDENCE_VERSION, COVERAGE_SELECTION_FIX_VERSION
from std0_quant.audit.eligibility_policy import ELIGIBILITY_POLICY_VERSION
from std0_quant.collectors.gamma_discovery import GAMMA_DISCOVERY_ISOLATION_FIX_VERSION
from std0_quant.collectors.network_stability import NETWORK_ENGINEERING_FIX_VERSION
from std0_quant.storage import RUN_ID_UNIQUENESS_FIX_VERSION
assert EPISODE_RULE_VERSION == "v1_3sec"
assert Y30_HORIZON_SECONDS == 30
assert NETWORK_ENGINEERING_FIX_VERSION == "network_stability_fix_v1"
assert COVERAGE_EVIDENCE_VERSION == "coverage_evidence_v2"
assert COVERAGE_SELECTION_FIX_VERSION == "coverage_selection_fix_v1"
assert GAMMA_DISCOVERY_ISOLATION_FIX_VERSION == "gamma_discovery_isolation_fix_v1"
assert RUN_ID_UNIQUENESS_FIX_VERSION == "run_id_uniqueness_fix_v1"
assert ELIGIBILITY_POLICY_VERSION == "prospective_v4_eligibility_v2"
freeze = json.loads(Path("data/state/eligibility_policy_freeze_prospective_v4_eligibility_v2.json").read_text())
assert freeze["effective_from_session_id"] == "supervisor-1787652746725-13792"
assert freeze["primary_cohort_retroactive_reclassification"] == "FORBIDDEN"
print("frozen governance: PASS")
PY
pass "frozen versions and governance"

for stale in supervisor_status.json live_health.json network_health.json; do
  [[ ! -e "data/state/$stale" ]] || fail "stale runtime state inherited: $stale"
done
if find data -path '*/raw/*' -type f -print -quit 2>/dev/null | grep -q .; then
  fail "raw file inheritance detected"
fi
if find data -path '*/sessions/*' -type f -print -quit 2>/dev/null | grep -q .; then
  fail "session identity inheritance detected"
fi
pass "no stale PID/session/raw inheritance"

for name in HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy; do
  value="${!name:-}"
  if [[ "$value" == *"127.0.0.1"* || "$value" == *"localhost"* ]]; then
    fail "$name points to a localhost proxy that cannot migrate to AWS"
  fi
done
pass "proxy environment has no inherited localhost route"

command -v curl >/dev/null 2>&1 || fail "curl missing"
curl --noproxy '*' --fail --silent --show-error --max-time 15 https://api.binance.com/api/v3/time >/dev/null || fail "Binance public connectivity"
curl --noproxy '*' --fail --silent --show-error --max-time 15 'https://gamma-api.polymarket.com/markets?limit=1' >/dev/null || fail "Gamma public connectivity"
curl --noproxy '*' --fail --silent --show-error --max-time 15 https://clob.polymarket.com/time >/dev/null || fail "CLOB public connectivity"
pass "Binance, Gamma, and CLOB public connectivity"

echo "READY_TO_START_NEW_AWS_SESSION"
echo "O3 starts at 0/86400; session stitching is forbidden. No recorder was started."
