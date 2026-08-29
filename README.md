# std0-quant

Auditable reconstruction and execution-reality research for std0 on Polymarket Bitcoin Up or Down 5-minute markets.

## Current Engineering Status

Checkpoint: `f25e9af0402cb3f5dd71f929fc019f5220a49e8c`

- Prospective plumbing: `CLOSED`
- Execution Reality v1: `CLOSED` in Research / Simulator scope
- Portfolio / Risk Layer v1: `CLOSED` in Research / Simulator scope
- Execution Shadow Integration v1: `CLOSED`
- Real CloddsBot runtime integration: `NOT_YET_BUILT`
- Live execution: `NOT_AUTHORIZED`

Current execution code includes tested queue/fill, latency, order-state, simulator, portfolio/risk, and hard-SHADOW integration components. This does not authorize production trading. The current Clodds boundary is SHADOW-only and does not load trading credentials or private keys or place real orders.

Frozen Episode / FirstOpposite / Y30 definitions and temporal-integrity rules remain unchanged.

The section below is retained as historical AWS migration provenance and is not the current project capability statement.

## Historical AWS Recorder Migration Bundle

This repository publishes the audited migration-only bundle
`std0-quant-aws-20260825T170402Z` and the source/tests needed to audit the
run-id uniqueness fix.

- Decision: `AWS_READY_FOR_PRESTART_REVERIFY`
- Archive SHA256: `b67b952de2abf579a2a8e8970621f9505bdbdd42fce444aa83f46403b6b48770`
- Tests: 459 baseline; 461 final; 5/5 repeated full-suite passes
- Engineering provenance: `run_id_uniqueness_fix_v1`
- Run-id stress: 10,000 generated / 10,000 unique at a frozen timestamp and PID
- Windows Python: 3.12.10
- Python 3.14 compatibility: `UNKNOWN`; prefer Python 3.12
- Old session/PID/O3/active raw: not included
- O3 session stitching: forbidden

The archive is the authoritative AWS upload artifact. `manifest/` and
`bootstrap/` are duplicated outside it for GitHub review. `src/`, `scripts/`,
`tests/`, `config/`, and `pyproject.toml` contain the auditable application
source; they contain no raw data or active runtime state. The prior
`std0-quant-aws-20260825T162639Z` release remains immutable in `release/`.

## Download and verify on Ubuntu

```bash
git clone https://github.com/ellissun8-lab/Polymarket-quant.git
cd Polymarket-quant/release
sha256sum -c std0-quant-aws-20260825T170402Z.tar.gz.sha256

mkdir -p ~/std0-quant-migration
tar -xzf std0-quant-aws-20260825T170402Z.tar.gz \
  -C ~/std0-quant-migration

cd ~/std0-quant-migration/std0-quant-aws-20260825T170402Z
sha256sum -c manifest/sha256sums.txt
bash bootstrap/bootstrap_ubuntu.sh
bash bootstrap/verify_before_start.sh
```

Stop at `READY_TO_START_NEW_AWS_SESSION`. This repository does not authorize
starting a recorder or continuing the old Windows session.
