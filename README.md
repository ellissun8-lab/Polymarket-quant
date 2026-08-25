# std0-quant AWS Recorder Migration Bundle

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
