# AWS Recorder Migration

Bundle: `std0-quant-aws-20260825T170402Z`  
Source repository: `https://github.com/ellissun8-lab/Polymarket-quant.git`

This is a clean recorder-only migration. It contains no Windows PID/session
identity, active raw, old O3 runtime, secret, virtual environment, or historical
raw trade database. AWS must create a new session and O3 starts at `0/86400`.

## 1. Upload from Windows PowerShell

```powershell
scp -i "<AWS_KEY.pem>" "<LOCAL_BUNDLE_DIR>\std0-quant-aws-20260825T170402Z.tar.gz" ubuntu@<PUBLIC_IP>:~/
scp -i "<AWS_KEY.pem>" "<LOCAL_BUNDLE_DIR>\std0-quant-aws-20260825T170402Z.tar.gz.sha256" ubuntu@<PUBLIC_IP>:~/
```

## 2. Extract on AWS

```bash
mkdir -p ~/std0-quant-migration
tar -xzf ~/std0-quant-aws-20260825T170402Z.tar.gz -C ~/std0-quant-migration
cd ~/std0-quant-migration/std0-quant-aws-20260825T170402Z
sha256sum -c manifest/sha256sums.txt
```

Optionally validate the archive before extraction:

```bash
cd ~
sha256sum -c std0-quant-aws-20260825T170402Z.tar.gz.sha256
```

## 3. Bootstrap (does not start recorder)

Python 3.12 is the Windows-validated preference. Python 3.14 has compatible
current package metadata/wheels but is not yet project-tested; to test the AWS
default interpreter, leave `PYTHON_BIN` unset.

```bash
cd ~/std0-quant-migration/std0-quant-aws-20260825T170402Z
bash bootstrap/bootstrap_ubuntu.sh
```

## 4. Verify before start (does not start recorder)

```bash
cd ~/std0-quant-migration/std0-quant-aws-20260825T170402Z
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
