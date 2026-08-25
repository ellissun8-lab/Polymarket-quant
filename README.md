# std0-quant AWS Recorder Migration Bundle

This repository publishes the audited migration-only bundle
`std0-quant-aws-20260825T162639Z`.

- Decision: `AWS_MIGRATION_BUNDLE_READY`
- Archive SHA256: `bd300a890ed0ca54898cc7646dbec620255049aad43eaeb4bfc1adb6365ea330`
- Tests: 459 passed before and after packaging
- Windows Python: 3.12.10
- Python 3.14 compatibility: `UNKNOWN`; prefer Python 3.12
- Old session/PID/O3/active raw: not included
- O3 session stitching: forbidden

The archive is the authoritative upload artifact. `manifest/` and `bootstrap/`
are duplicated outside the archive only so they can be audited on GitHub.

## Download and verify on Ubuntu

```bash
git clone https://github.com/ellissun8-lab/Polymarket-quant.git
cd Polymarket-quant/release
sha256sum -c std0-quant-aws-20260825T162639Z.tar.gz.sha256

mkdir -p ~/std0-quant-migration
tar -xzf std0-quant-aws-20260825T162639Z.tar.gz \
  -C ~/std0-quant-migration

cd ~/std0-quant-migration/std0-quant-aws-20260825T162639Z
sha256sum -c manifest/sha256sums.txt
bash bootstrap/bootstrap_ubuntu.sh
bash bootstrap/verify_before_start.sh
```

Stop at `READY_TO_START_NEW_AWS_SESSION`. This repository does not authorize
starting a recorder or continuing the old Windows session.
