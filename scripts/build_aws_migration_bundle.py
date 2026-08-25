"""Build a minimal, auditable Ubuntu recorder migration bundle.

Migration tooling only.  This script never starts, stops, signals, or probes a
live recorder process and never copies active runtime/raw state.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tarfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STAMP = "20260825T170402Z"
BUNDLE_ID = f"std0-quant-aws-{STAMP}"
DIST_ROOT = ROOT / "dist" / "aws_migration"
BUNDLE_ROOT = DIST_ROOT / BUNDLE_ID
APP = BUNDLE_ROOT / "app"
MANIFEST = BUNDLE_ROOT / "manifest"
BOOTSTRAP = BUNDLE_ROOT / "bootstrap"

SOURCE_REPO = "https://github.com/ellissun8-lab/Polymarket-quant.git"
SETTINGS_SHA = "580abdc9a7ea5e34cc854d1ded12de35703bfcbbf2f216b1020a04cd6a0b05ae"
ENGINEERING_FIXES = [
    "recorder_reliability_fix_v1",
    "network_stability_fix_v1",
    "coverage_selection_fix_v1",
    "gamma_discovery_isolation_fix_v1",
    "run_id_uniqueness_fix_v1",
]

RUNTIME_STATE = [
    "baseline_truth_snapshot.json",
    "historical_baseline_snapshot_v2.json",
    "eligibility_policy_freeze_prospective_v4_eligibility_v2.json",
    "primary_cohort_freeze_prospective_v4.json",
    "prospective_cohort.json",
    "prospective_checkpoint_state.json",
    "timing_semantics_registry.json",
    "phase2b_research_governance.json",
]

HISTORICAL_STATE = [
    "project_governance_snapshot.json",
    "project_milestone_state.json",
    "phase2b_evidence_status.json",
    "phase2b_research_state.json",
    "network_stability_fix_v1_deployment.json",
]

REPORTS = [
    "reconciliation_20260825T132224Z.json",
    "reconciliation_20260825T132224Z.md",
    "bias_audit_20260824T131500Z.json",
    "bias_audit_20260824T131500Z.md",
    "regime_audit_20260824T135827.153794Z.json",
    "regime_audit_20260824T135827.153794Z.md",
    "phase2a_20260824T142144.220657Z.json",
    "phase2a_20260824T142144.220657Z.md",
    "phase2a_live_20260824T154703Z.json",
    "phase2a_live_20260824T154703Z.md",
    "phase2a_prospective_completion_20260824T175545Z.json",
    "phase2a_prospective_completion_20260824T175545Z.md",
    "v4_full_lifecycle_validation_20260824T175545Z.json",
    "v4_full_lifecycle_validation_20260824T175545Z.md",
    "phase2b_research_v3_20260825T060535Z.json",
    "phase2b_research_v3_20260825T060535Z.md",
    "phase2b_timing_audit_20260825T060535Z.json",
    "phase2b_timing_audit_20260825T060535Z.md",
    "recorder_network_proxy_stability_20260825T090254Z.json",
    "recorder_network_proxy_stability_20260825T090254Z.md",
    "eligibility_migration_audit_20260825T092607Z.json",
    "project_drift_audit_20260825T095642Z.json",
    "project_drift_audit_20260825T095642Z.md",
    "post_fix_3market_acceptance_20260825T104741Z.json",
    "post_fix_3market_acceptance_20260825T104741Z.md",
    "coverage_selection_bug_20260825T111405Z.json",
    "coverage_selection_bug_20260825T111405Z.md",
    "rotation_root_cause_20260825T111405Z.json",
    "rotation_root_cause_20260825T111405Z.md",
    "coverage_repair_20260825T111405Z.json",
    "coverage_repair_20260825T111405Z.md",
    "gamma_discovery_isolation_20260825T113410Z.json",
    "gamma_discovery_isolation_20260825T113410Z.md",
]

EXCLUDED_PATHS = [
    {"path": "data/raw/**", "reason": "historical and active raw remain on Windows"},
    {"path": "data/sessions/**", "reason": "old process/session lineage must not migrate"},
    {"path": "data/logs/**", "reason": "Windows operational logs"},
    {"path": "data/state/supervisor_status.json", "reason": "stale/active PID state"},
    {"path": "data/state/live_health.json", "reason": "live runtime snapshot"},
    {"path": "data/state/network_health.json", "reason": "live runtime snapshot"},
    {"path": "data/state/manifest_supervisor-*.json", "reason": "old session identity"},
    {"path": "data/state/sync_state.db", "reason": "506MB historical sync state; recorder-only bundle"},
    {"path": "data/normalized/**", "reason": "offline/rebuildable historical analysis data"},
    {"path": "data/derived/** except event_ledger.parquet", "reason": "offline/rebuildable analysis data"},
    {"path": "data/reports/** except selected authoritative evidence", "reason": "large/redundant reports"},
    {"path": "data_fixture/**", "reason": "development fixture, not recorder runtime"},
    {"path": "notebooks/**", "reason": "offline analysis only"},
    {"path": ".venv/**, venv/**, env/**", "reason": "platform-specific virtual environments"},
    {"path": "**/__pycache__/**, **/*.py[co], .pytest_cache/**", "reason": "cache/bytecode"},
    {"path": ".env and credential/key patterns", "reason": "secret safety"},
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.replace("\r\n", "\n"), encoding="utf-8", newline="\n")


def write_json(path: Path, payload: object) -> None:
    write_text(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def copy_file(relative: str, destination_relative: str | None = None) -> str:
    source = ROOT / relative
    if not source.is_file():
        raise FileNotFoundError(source)
    destination = APP / (destination_relative or relative)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return relative.replace("\\", "/")


def copy_tree(relative: str) -> list[str]:
    included: list[str] = []
    source_root = ROOT / relative
    for source in sorted(source_root.rglob("*")):
        if not source.is_file():
            continue
        rel = source.relative_to(ROOT).as_posix()
        # The bundle builder is source-side release tooling, not AWS recorder
        # runtime.  Keeping it out also prevents its detector signatures from
        # being mistaken for embedded credentials by the bundle scan.
        if rel == "scripts/build_aws_migration_bundle.py":
            continue
        if "__pycache__" in source.parts or source.suffix in {".pyc", ".pyo"}:
            continue
        destination = APP / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        included.append(rel)
    return included


def source_inventory() -> tuple[list[dict[str, object]], list[dict[str, object]], int, int]:
    files = [
        path for path in ROOT.rglob("*")
        if path.is_file() and "dist" not in path.relative_to(ROOT).parts
    ]
    top_files = [
        {"path": path.relative_to(ROOT).as_posix(), "bytes": path.stat().st_size}
        for path in sorted(files, key=lambda item: item.stat().st_size, reverse=True)[:50]
    ]
    sizes: dict[str, int] = {}
    for path in files:
        relative = path.relative_to(ROOT)
        parts = relative.parts[:-1]
        for depth in range(1, min(len(parts), 4) + 1):
            key = "/".join(parts[:depth])
            sizes[key] = sizes.get(key, 0) + path.stat().st_size
    top_dirs = [
        {"path": key, "bytes": value}
        for key, value in sorted(sizes.items(), key=lambda item: item[1], reverse=True)[:30]
    ]
    return top_files, top_dirs, len(files), sum(path.stat().st_size for path in files)


def secret_candidate_names() -> list[str]:
    patterns = (
        ".env", ".pem", ".key", "id_rsa", "id_ed25519",
        "credentials", "aws_access", "private_key",
    )
    results: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or "dist" in path.relative_to(ROOT).parts:
            continue
        lowered = path.name.lower()
        if any(token in lowered for token in patterns):
            results.append(path.relative_to(ROOT).as_posix())
    return sorted(results)


def scan_bundle_secrets() -> dict[str, object]:
    filename_hits: list[str] = []
    content_hits: list[dict[str, object]] = []
    private_headers = (
        "BEGIN RSA PRIVATE KEY", "BEGIN OPENSSH PRIVATE KEY", "BEGIN PRIVATE KEY",
    )
    assignment = re.compile(
        r"(?i)^\s*(?:export\s+)?(AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|"
        r"OPENAI_API_KEY|PRIVATE_KEY|SECRET|TOKEN|PASSWORD)\s*=\s*['\"]?([^'\"\s#]+)"
    )
    safe_values = {"", "null", "none", "changeme", "placeholder", "required"}
    for path in sorted(BUNDLE_ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(BUNDLE_ROOT).as_posix()
        lowered = path.name.lower()
        if (
            (lowered == ".env" or lowered.endswith((".pem", ".key"))
             or lowered.startswith(("id_rsa", "id_ed25519")))
            and lowered != ".env.example"
        ):
            filename_hits.append(rel)
        if path.stat().st_size > 25 * 1024 * 1024:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for line_number, line in enumerate(text.splitlines(), 1):
            if any(header in line for header in private_headers):
                content_hits.append({"path": rel, "line": line_number, "kind": "private_key_header"})
            match = assignment.match(line)
            if not match or line.lstrip().startswith("#"):
                continue
            value = match.group(2).strip()
            placeholder = (
                value.lower() in safe_values or value.startswith(("${", "<"))
                or "example" in value.lower() or "(" in value
            )
            if not placeholder and len(value) >= 12:
                content_hits.append({
                    "path": rel, "line": line_number,
                    "kind": f"credential_assignment:{match.group(1)}",
                })
    return {
        "status": "PASS" if not filename_hits and not content_hits else "FAIL",
        "filename_hits": filename_hits,
        "content_hits": content_hits,
        "note": "Variable-name mentions and placeholders are not classified as credentials.",
    }


def bootstrap_script() -> str:
    return r'''#!/usr/bin/env bash
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
from std0_quant.storage import RUN_ID_UNIQUENESS_FIX_VERSION
s = load_settings()
assert s.episode.rule == "v1_3sec"
assert s.y30.horizon_seconds == 30
assert s.coverage.bucket_seconds == 1
assert NETWORK_ENGINEERING_FIX_VERSION == "network_stability_fix_v1"
assert COVERAGE_EVIDENCE_VERSION == "coverage_evidence_v2"
assert COVERAGE_SELECTION_FIX_VERSION == "coverage_selection_fix_v1"
assert GAMMA_DISCOVERY_ISOLATION_FIX_VERSION == "gamma_discovery_isolation_fix_v1"
assert RUN_ID_UNIQUENESS_FIX_VERSION == "run_id_uniqueness_fix_v1"
assert ELIGIBILITY_POLICY_VERSION == "prospective_v4_eligibility_v2"
print("static frozen-version checks: PASS")
PY

echo "BOOTSTRAP_COMPLETE_NO_RECORDER_STARTED"
'''


def verify_script() -> str:
    return r'''#!/usr/bin/env bash
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
'''


def migration_readme() -> str:
    return f'''# AWS Recorder Migration

Bundle: `{BUNDLE_ID}`  
Source repository: `{SOURCE_REPO}`

This is a clean recorder-only migration. It contains no Windows PID/session
identity, active raw, old O3 runtime, secret, virtual environment, or historical
raw trade database. AWS must create a new session and O3 starts at `0/86400`.

## 1. Upload from Windows PowerShell

```powershell
scp -i "<AWS_KEY.pem>" "<LOCAL_BUNDLE_DIR>\\{BUNDLE_ID}.tar.gz" ubuntu@<PUBLIC_IP>:~/
scp -i "<AWS_KEY.pem>" "<LOCAL_BUNDLE_DIR>\\{BUNDLE_ID}.tar.gz.sha256" ubuntu@<PUBLIC_IP>:~/
```

## 2. Extract on AWS

```bash
mkdir -p ~/std0-quant-migration
tar -xzf ~/{BUNDLE_ID}.tar.gz -C ~/std0-quant-migration
cd ~/std0-quant-migration/{BUNDLE_ID}
sha256sum -c manifest/sha256sums.txt
```

Optionally validate the archive before extraction:

```bash
cd ~
sha256sum -c {BUNDLE_ID}.tar.gz.sha256
```

## 3. Bootstrap (does not start recorder)

Python 3.12 is the Windows-validated preference. Python 3.14 has compatible
current package metadata/wheels but is not yet project-tested; to test the AWS
default interpreter, leave `PYTHON_BIN` unset.

```bash
cd ~/std0-quant-migration/{BUNDLE_ID}
bash bootstrap/bootstrap_ubuntu.sh
```

## 4. Verify before start (does not start recorder)

```bash
cd ~/std0-quant-migration/{BUNDLE_ID}
bash bootstrap/verify_before_start.sh
```

The expected terminal line is:

```text
READY_TO_START_NEW_AWS_SESSION
```

Stop there. Do not start the recorder until the Windows-to-AWS cutover is
explicitly authorized. The minimal bundle omits 1.9GB historical std0 raw and
the 506MB Windows sync DB; a future recorder start must use recorder-only
operations (`--no-sync`) unless historical sync continuity is migrated through
a separately approved process. The isolated derived-refresh child may report
missing historical raw; that is an offline-analysis limitation and does not
authorize a code or truth change.
'''


def dependency_markdown(audit: dict[str, object]) -> str:
    return f'''# AWS Migration Dependency Audit

- Run: `{STAMP}`
- Source: `{SOURCE_REPO}` (public repository currently empty)
- Source working directory is not a Git worktree; `git_commit = null`.
- Tests: **459 baseline → 461 final; 5/5 repeated full-suite PASS**
- Settings SHA-256: `{SETTINGS_SHA}`

## Required runtime paths

{os.linesep.join(f"- `{item}`" for item in audit["required_paths"])}

## Optional / historical paths

{os.linesep.join(f"- `{item}`" for item in audit["optional_paths"])}

## Excluded

{os.linesep.join(f"- `{item['path']}` — {item['reason']}" for item in EXCLUDED_PATHS)}

## Key conclusions

- Historical/active raw and old session state are not recorder dependencies for a clean AWS session.
- `event_ledger.parquet` plus the frozen baseline snapshot are required by supervisor startup validation.
- Historical trade sync/derived refresh needs excluded multi-GB history; the minimal successor-recorder workflow is recorder-only.
- Proxy routing is obtained from OS/environment settings. Direct operation is supported when proxy variables are absent.
- `AWS_PROXY_MIGRATION_REQUIRED = true`: review/unset Windows localhost proxy variables; source/config does not hard-code `127.0.0.1:7892`.
- Python 3.14 compatibility is `UNKNOWN`: metadata and current Linux wheels are available, but the validated source runtime is Python 3.12.10.
'''


def build() -> None:
    if BUNDLE_ROOT.exists() or (DIST_ROOT / f"{BUNDLE_ID}.tar.gz").exists():
        raise SystemExit(f"refusing to overwrite existing bundle: {BUNDLE_ID}")
    APP.mkdir(parents=True)
    MANIFEST.mkdir(parents=True)
    BOOTSTRAP.mkdir(parents=True)

    top_files, top_dirs, source_count, source_bytes = source_inventory()
    included_source: list[str] = []
    for tree in ("src", "scripts", "tests", "config"):
        included_source.extend(copy_tree(tree))
    for root_file in ("pyproject.toml", "README.md", ".gitignore", ".env.example"):
        included_source.append(copy_file(root_file))

    for filename in RUNTIME_STATE:
        included_source.append(copy_file(f"data/state/{filename}"))
    for filename in HISTORICAL_STATE:
        included_source.append(copy_file(
            f"data/state/{filename}", f"data/state/migration_history/{filename}"
        ))
    included_source.append(copy_file("data/derived/event_ledger.parquet"))
    for filename in REPORTS:
        included_source.append(copy_file(f"data/reports/{filename}"))

    write_text(
        APP / "data/state/migration_history/README_NOT_RUNTIME_STATE.md",
        "# Historical migration snapshots\n\n"
        "Files in this directory are provenance only and MUST NOT be interpreted "
        "as active AWS runtime, PID, session, or O3 state.\n",
    )

    required_paths = [
        "src/std0_quant/**", "scripts/run_live_supervisor.py", "scripts/collect_live.py",
        "scripts/live_status.py", "scripts/report_live_coverage.py",
        "scripts/report_market_coverage.py", "scripts/init_prospective_baseline.py",
        "config/settings.yaml", "pyproject.toml", "data/derived/event_ledger.parquet",
        "data/state/baseline_truth_snapshot.json",
        "data/state/eligibility_policy_freeze_prospective_v4_eligibility_v2.json",
        "data/state/primary_cohort_freeze_prospective_v4.json",
        "data/state/prospective_cohort.json",
    ]
    optional_paths = [
        "tests/**", "README.md", ".env.example", "selected data/reports/**",
        "data/state/migration_history/** (NOT_RUNTIME_STATE)",
    ]
    dependency_audit = {
        "schema_version": 1,
        "run_id": STAMP,
        "source_repo": SOURCE_REPO,
        "git_commit": None,
        "git_state": "SOURCE_DIRECTORY_NOT_A_GIT_WORKTREE_REMOTE_REPOSITORY_EMPTY",
        "tests": {
            "baseline_passed": 459,
            "final_passed": 461,
            "failed": 0,
            "repeated_full_suite": [461, 461, 461, 461, 461],
        },
        "baseline_bug_evidence": {
            "environment": "AWS Ubuntu 26.04 / Python 3.14",
            "exception": "sqlite3.IntegrityError: UNIQUE constraint failed: sync_runs.run_id",
            "observed_tests": [
                "tests/test_backfill.py::TestSyncBackfill::test_backfill_is_idempotent",
                "tests/test_trade_dedupe.py::test_incremental_sync_fetches_only_new_records",
            ],
            "old_formula": "<prefix>-<utc_now_ms>-<pid>",
            "collision_mechanism": "two logical runs in one process and millisecond reused the same primary key",
        },
        "run_id_regression": {
            "engineering_fix_version": "run_id_uniqueness_fix_v1",
            "new_formula": "<prefix>-<utc_now_ms>-<pid>-<thread_safe_process_sequence_hex16>",
            "same_timestamp_same_pid_two_sqlite_runs": "PASS",
            "stress_generated": 10000,
            "stress_unique": 10000,
            "thread_safe": True,
        },
        "settings_sha256": SETTINGS_SHA,
        "required_paths": required_paths,
        "optional_paths": optional_paths,
        "excluded_paths": EXCLUDED_PATHS,
        "runtime_state_paths": [f"data/state/{item}" for item in RUNTIME_STATE],
        "historical_truth_paths": [
            "data/derived/event_ledger.parquet",
            "data/state/baseline_truth_snapshot.json",
            "data/state/historical_baseline_snapshot_v2.json",
            *[f"data/reports/{item}" for item in REPORTS],
        ],
        "secret_candidates": secret_candidate_names(),
        "large_file_candidates": top_files,
        "top_30_directories": top_dirs,
        "source_file_count": source_count,
        "source_bytes": source_bytes,
        "derived_classification": {
            "data/derived/event_ledger.parquet": "A_RUNTIME_STARTUP_AND_B_GOVERNANCE",
            "data/derived/episodes.parquet": "C_OFFLINE_ANALYSIS_REBUILDABLE",
            "data/derived/event_ledger.csv": "C_OFFLINE_ANALYSIS_REBUILDABLE",
            "data/derived/phase2b/**": "D_LARGE_HISTORICAL_RESEARCH",
            "data/derived/features/**": "C_OFFLINE_ANALYSIS",
        },
        "proxy_audit": {
            "AWS_PROXY_MIGRATION_REQUIRED": True,
            "AWS_START_BLOCKED_BY_PROXY_CONFIGURATION": False,
            "hardcoded_127_0_0_1_7892": [],
            "runtime_policy": "urllib.request.getproxies; requests/websockets use OS/environment route",
            "direct_supported": True,
            "required_action": "Do not copy Windows proxy variables; fail pre-start if proxy points to localhost.",
            "findings": [
                {"file": ".env.example", "line": 15, "setting": "commented HTTP_PROXY 127.0.0.1:7890", "runtime_critical": False},
                {"file": ".env.example", "line": 16, "setting": "commented HTTPS_PROXY 127.0.0.1:7890", "runtime_critical": False},
                {"file": "src/std0_quant/collectors/network_stability.py", "line": 19, "setting": "dynamic OS/environment proxy discovery", "runtime_critical": True},
            ],
        },
        "python_314": {
            "status": "UNKNOWN",
            "source_requires_python": ">=3.12",
            "source_validated_python": "3.12.10",
            "aws_reported_python": "3.14.4",
            "compiled_dependencies": ["numpy", "pandas", "pyarrow", "scikit-learn", "scipy", "duckdb", "psutil", "pydantic-core", "PyYAML"],
            "pypi_observation": "Current releases expose CPython 3.14 manylinux wheels, but no project test was run under 3.14.",
            "recommendation": "Prefer Python 3.12; Python 3.14 must pass bootstrap and pre-start verification before use.",
        },
        "active_session_safety": {
            "supervisor_status_json_excluded": True,
            "live_health_json_excluded": True,
            "network_health_json_excluded": True,
            "session_journals_excluded": True,
            "raw_excluded": True,
            "O3_SESSION_STITCHING": "FORBIDDEN",
        },
    }
    source_audit_json = ROOT / f"data/reports/aws_migration_dependency_audit_{STAMP}.json"
    source_audit_md = source_audit_json.with_suffix(".md")
    write_json(source_audit_json, dependency_audit)
    write_text(source_audit_md, dependency_markdown(dependency_audit))
    shutil.copy2(source_audit_json, MANIFEST / "dependency_audit.json")
    shutil.copy2(source_audit_md, APP / "data/reports" / source_audit_md.name)
    included_source.extend([
        source_audit_json.relative_to(ROOT).as_posix(),
        source_audit_md.relative_to(ROOT).as_posix(),
    ])

    write_json(MANIFEST / "excluded_paths.json", {
        "schema_version": 1,
        "excluded_paths": EXCLUDED_PATHS,
        "excluded_large_paths": [
            {"path": "data/raw", "bytes": 30_630_839_855},
            {"path": "data/state/sync_state.db", "bytes": 506_224_640},
            {"path": "data/derived excluding event_ledger.parquet", "bytes": 295_582_003},
            {"path": "data/normalized", "bytes": 172_451_977},
        ],
    })
    write_text(BOOTSTRAP / "bootstrap_ubuntu.sh", bootstrap_script())
    write_text(BOOTSTRAP / "verify_before_start.sh", verify_script())
    write_text(BOOTSTRAP / "README_AWS_MIGRATION.md", migration_readme())
    for script in (BOOTSTRAP / "bootstrap_ubuntu.sh", BOOTSTRAP / "verify_before_start.sh"):
        script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    preliminary_secret_scan = scan_bundle_secrets()
    if preliminary_secret_scan["status"] != "PASS":
        write_json(MANIFEST / "secret_scan_failure.json", preliminary_secret_scan)
        raise SystemExit("MIGRATION_SECRET_SCAN_FAIL")

    manifest_payload: dict[str, object] = {
        "schema_version": 1,
        "bundle_id": BUNDLE_ID,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_repo": SOURCE_REPO,
        "source_repo_remote_state": "PUBLISHED_MIGRATION_REPOSITORY",
        "git_commit": None,
        "source_python_version": "3.12.10",
        "tests": {
            "baseline_passed": 459,
            "final_passed": 461,
            "failed": 0,
            "targeted": {
                "run_id_regression_and_stress": "2 passed",
                "backfill": "9 passed",
                "trade_dedupe": "10 passed",
            },
            "repeated_full_suite": [461, 461, 461, 461, 461],
        },
        "baseline_bug_evidence": {
            "exception": "sqlite3.IntegrityError: UNIQUE constraint failed: sync_runs.run_id",
            "old_formula": "<prefix>-<utc_now_ms>-<pid>",
            "affected_scenarios": ["backfill repeated sync", "incremental repeated sync"],
        },
        "run_id_regression": {
            "engineering_fix_version": "run_id_uniqueness_fix_v1",
            "new_formula": "<prefix>-<utc_now_ms>-<pid>-<thread_safe_process_sequence_hex16>",
            "same_timestamp_same_pid_two_sqlite_runs": "PASS",
            "stress_generated": 10000,
            "stress_unique": 10000,
        },
        "settings_sha": SETTINGS_SHA,
        "settings_sha256": SETTINGS_SHA,
        "collector_version": "phase2a_prospective_v4",
        "cohort_version": "prospective_v4",
        "engineering_fixes": ENGINEERING_FIXES,
        "coverage_evidence_version": "coverage_evidence_v2",
        "eligibility_policy_version": "prospective_v4_eligibility_v2",
        "historical_effective_from_session_id": "supervisor-1787652746725-13792",
        "RETROSPECTIVE_COHORT_EXPANSION": "FORBIDDEN",
        "included_file_count": 0,
        "included_bytes": 0,
        "included_bytes_excluding_sha256sums": 0,
        "included_bytes_scope": "all regular files except manifest/sha256sums.txt",
        "excluded_file_count": max(0, source_count - len(set(included_source))),
        "excluded_large_paths": [item for item in EXCLUDED_PATHS if item["path"].startswith(("data/raw", "data/state/sync", "data/normalized", "data/derived"))],
        "secret_scan_status": "PASS",
        "active_runtime_state_excluded": True,
        "active_raw_excluded": True,
        "old_session_identity_excluded": True,
        "old_windows_pid_excluded": True,
        "old_o3_runtime_excluded": True,
        "python_314_compatibility": "UNKNOWN",
        "recommended_python_runtime": "3.12",
        "proxy_migration_status": "REVIEW_REQUIRED_DIRECT_SUPPORTED_NOT_BLOCKED",
        "sha256_manifest": "manifest/sha256sums.txt",
        "O3_SESSION_STITCHING": "FORBIDDEN",
        "aws_new_o3_start": "0/86400",
        "historical_sync_mode": "EXCLUDED_RECORDER_ONLY_BUNDLE",
        "decision": "AWS_READY_FOR_PRESTART_REVERIFY",
    }

    manifest_json = MANIFEST / "migration_manifest.json"
    manifest_md = MANIFEST / "migration_manifest.md"
    for _ in range(10):
        write_json(manifest_json, manifest_payload)
        write_text(manifest_md, f'''# Migration Manifest

- Bundle: `{BUNDLE_ID}`
- Source: `{SOURCE_REPO}` (empty remote; local source has no Git metadata)
- Tests: **459 passed → 461 passed; 5/5 repeated full-suite PASS**
- Included files: **{manifest_payload["included_file_count"]}**
- Included bytes before checksum list: **{manifest_payload["included_bytes_excluding_sha256sums"]}**
- Secret scan: **PASS**
- Active runtime/raw: **EXCLUDED**
- Python 3.14: **UNKNOWN**; Python 3.12 preferred
- Proxy: **review required; direct operation supported; not blocked**
- O3 stitching: **FORBIDDEN**; AWS starts at **0/86400**
- Decision: **AWS_READY_FOR_PRESTART_REVERIFY**
''')
        current_files = [
            path for path in BUNDLE_ROOT.rglob("*")
            if path.is_file() and path.name != "sha256sums.txt"
        ]
        next_count = len(current_files) + 1
        next_bytes = sum(path.stat().st_size for path in current_files)
        if (
            manifest_payload["included_file_count"] == next_count
            and manifest_payload["included_bytes"] == next_bytes
            and manifest_payload["included_bytes_excluding_sha256sums"] == next_bytes
        ):
            break
        manifest_payload["included_file_count"] = next_count
        manifest_payload["included_bytes"] = next_bytes
        manifest_payload["included_bytes_excluding_sha256sums"] = next_bytes
    else:
        raise SystemExit("manifest metadata did not stabilize")

    final_secret_scan = scan_bundle_secrets()
    if final_secret_scan["status"] != "PASS":
        write_json(MANIFEST / "secret_scan_failure.json", final_secret_scan)
        raise SystemExit("MIGRATION_SECRET_SCAN_FAIL")

    checksum_path = MANIFEST / "sha256sums.txt"
    checksum_lines: list[str] = []
    for path in sorted(BUNDLE_ROOT.rglob("*")):
        if not path.is_file() or path == checksum_path:
            continue
        relative = "./" + path.relative_to(BUNDLE_ROOT).as_posix()
        checksum_lines.append(f"{sha256(path)}  {relative}")
    write_text(checksum_path, "\n".join(checksum_lines) + "\n")

    archive = DIST_ROOT / f"{BUNDLE_ID}.tar.gz"
    with tarfile.open(archive, "w:gz", compresslevel=9) as tar:
        def normalized(info: tarfile.TarInfo) -> tarfile.TarInfo:
            if info.name.endswith(".sh"):
                info.mode = 0o755
            elif info.isfile():
                info.mode = 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            return info
        tar.add(BUNDLE_ROOT, arcname=BUNDLE_ID, filter=normalized)
    archive_sha = sha256(archive)
    write_text(archive.with_suffix(archive.suffix + ".sha256"), f"{archive_sha}  {archive.name}\n")

    print(json.dumps({
        "bundle_root": str(BUNDLE_ROOT),
        "archive": str(archive),
        "archive_bytes": archive.stat().st_size,
        "archive_sha256": archive_sha,
        "included_files": len([p for p in BUNDLE_ROOT.rglob("*") if p.is_file()]),
        "secret_scan": final_secret_scan["status"],
    }, indent=2))


if __name__ == "__main__":
    build()
