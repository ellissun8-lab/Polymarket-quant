"""Build prospective_v4 pretrade features with bounded low-memory raw reads.

Default mode is DRY RUN.  --publish is required to write artifacts.

This connector preserves the frozen Phase-2A feature semantics while avoiding
the historical build_pretrade_features.py full-directory materialization.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pyarrow.parquet as pq

from std0_quant.audit.prospective import verify_baseline_snapshot
from std0_quant.config import load_settings, resolve_path
from std0_quant.events.prospective_merge import atomic_write_parquet
from std0_quant.features.book_features import (
    BOOK_TRANSFORM_VERSION,
    compute_book_features,
)
from std0_quant.features.book_file_features import (
    compute_book_features_from_files,
)
from std0_quant.features.btc_features import (
    BTC_TRANSFORM_VERSION,
    compute_btc_features,
)
from std0_quant.features.btc_file_features import (
    compute_btc_features_from_files,
)
from std0_quant.features.coverage_gate import gate_coverage
from std0_quant.features.pretrade_builder import (
    cutoff_timestamp,
    iso_week,
)
from std0_quant.features.provenance import (
    FeatureProvenance,
    validate_provenance,
)
from std0_quant.features.raw_selection import (
    closed_raw_index,
    files_for_time_window,
)
from std0_quant.features.std0_state_features import build_std0_state


CUTOFF_MODE = "cutoff_1"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_rows(path: Path) -> list[dict]:
    return pq.read_table(path).to_pylist()


def load_online_regimes(derived: Path) -> dict[str, int]:
    files = sorted((derived / "audit").glob("online_regimes_*.parquet"))
    if not files:
        return {}

    rows = pq.read_table(
        files[-1],
        columns=["condition_id", "online_regime_id"],
    ).to_pylist()

    return {
        str(row["condition_id"]): int(row["online_regime_id"])
        for row in rows
    }


def public_file_list(paths) -> list[str]:
    return sorted({str(Path(path)) for path in paths})


def assemble_feature_row(
    row: dict,
    *,
    btc: dict,
    book: dict,
    btc_files: list[str],
    book_files: list[str],
    authoritative_ledger: Path,
    online_regime_id: int,
    btc_threshold: float,
    book_threshold: float,
) -> tuple[dict, list[dict], dict]:

    cid = str(row["condition_id"])
    prediction = int(row["first_opp_end_ms"])
    cutoff = cutoff_timestamp(prediction, CUTOFF_MODE)

    base = build_std0_state(row)
    direction = base["direction_sign"]

    for seconds in (1, 3, 5, 10, 30):
        name = f"btc_ret_{seconds}s"
        if name in btc:
            btc[f"btc_move_toward_opp_{seconds}s"] = (
                direction * btc[name]
                if btc[name] is not None
                else None
            )

    base.update({
        k: v
        for k, v in btc.items()
        if not k.startswith("_")
    })
    base.update({
        k: v
        for k, v in book.items()
        if not k.startswith("_")
    })

    feature_row_id = hashlib.sha256(
        f"{cid}|{prediction}|{CUTOFF_MODE}".encode()
    ).hexdigest()

    base.update({
        "feature_row_id": feature_row_id,
        "condition_id": cid,
        "prediction_ts_ms": prediction,
        "feature_cutoff_ms": cutoff,
        "cutoff_mode": CUTOFF_MODE,
        "market_start_ms": row["market_start_ms"],
        "market_end_ms": row["market_end_ms"],
        "y30": int(row["y30"]),
        "iso_week": iso_week(int(row["market_start_ms"])),
        "online_regime_id": online_regime_id,
        "first_opp_fill_price": row.get("first_opp_vwap"),
    })

    for ref in ("mid", "bid", "ask"):
        if ref in ("bid", "ask"):
            value = book.get(f"opp_best_{ref}")
        else:
            value = book.get("opp_mid")

        base[f"fill_minus_prev_{ref}"] = (
            float(row["first_opp_vwap"]) - value
            if value is not None
            else None
        )

    eligible, reasons = gate_coverage(
        btc.get("btc_pre30_coverage_pct"),
        book.get("book_pre10_coverage_pct"),
        btc_threshold,
        book_threshold,
    )

    base["model_eligible"] = eligible
    base["model_ineligible_reason"] = reasons

    coverage_row = {
        "condition_id": cid,
        "cutoff_mode": CUTOFF_MODE,
        "btc_pre30_coverage_pct":
            btc.get("btc_pre30_coverage_pct"),
        "btc_pre10_coverage_pct":
            btc.get("btc_pre10_coverage_pct"),
        "book_pre30_coverage_pct":
            book.get("book_pre30_coverage_pct"),
        "book_pre10_coverage_pct":
            book.get("book_pre10_coverage_pct"),
        "book_pre5_coverage_pct":
            book.get("book_pre5_coverage_pct"),
        "model_eligible": eligible,
        "missing_reason": reasons,
    }

    source_phase_min = int(row["initial_first_timestamp_ms"])

    metadata = {
        "feature_row_id",
        "condition_id",
        "prediction_ts_ms",
        "feature_cutoff_ms",
        "cutoff_mode",
        "market_start_ms",
        "market_end_ms",
        "y30",
        "iso_week",
        "online_regime_id",
        "model_eligible",
        "model_ineligible_reason",
    }

    provenance: list[dict] = []

    for name, value in base.items():
        if name in metadata:
            continue

        if name.startswith("btc_"):
            source_type = "binance_btc"
            source_min = btc.get("_source_min_ms")
            source_max = btc.get("_source_max_ms")
            version = BTC_TRANSFORM_VERSION
            missing_reason = (
                None
                if value is not None
                else "NO_BTC_DATA_BEFORE_CUTOFF"
            )
            files = btc_files
            event_type = "trade"

        elif name.startswith((
            "opp_best",
            "opp_bid_depth",
            "opp_ask_depth",
            "opp_mid",
            "opp_spread",
            "opp_obi",
            "initial_best",
            "initial_mid",
            "initial_spread",
            "initial_bid_depth",
            "initial_ask_depth",
            "initial_obi",
            "pm_",
            "book_",
            "fill_minus_prev",
        )):
            source_type = "polymarket_book"
            source_min = book.get("_source_min_ms")
            source_max = book.get("_source_max_ms")
            version = BOOK_TRANSFORM_VERSION
            missing_reason = (
                None
                if value is not None
                else "NO_VALID_BOOK_BEFORE_CUTOFF"
            )
            files = book_files
            event_type = "book/price_change"

        else:
            source_type = "phase1_truth"
            source_min = source_phase_min
            source_max = prediction
            version = "phase1_safe_v1"
            missing_reason = (
                None
                if value is not None
                else "PHASE1_VALUE_UNDEFINED"
            )
            files = [str(authoritative_ledger)]
            event_type = "event_ledger"

        provenance.append(
            FeatureProvenance(
                cid,
                name,
                source_type,
                ";".join(files) or None,
                event_type,
                source_min,
                source_max,
                prediction,
                cutoff,
                version,
                missing_reason,
            ).to_dict()
        )

    return base, provenance, coverage_row


def execute(
    *,
    publish: bool,
    run_id: str,
    btc_threshold: float,
    book_threshold: float,
) -> int:

    settings = load_settings()

    derived = resolve_path(settings, "derived")
    normalized = resolve_path(settings, "normalized")
    state = resolve_path(settings, "state")

    authoritative_ledger = derived / "event_ledger.parquet"

    prospective_dir = derived / "prospective_v4"
    prospective_ledger = (
        prospective_dir / "event_ledger.parquet"
    )
    coverage_selection = (
        prospective_dir / "coverage_selection.parquet"
    )

    baseline_path = state / "baseline_truth_snapshot.json"

    btc_dir = resolve_path(settings, "raw_btc_ticks")
    book_dir = resolve_path(settings, "raw_polymarket_book")

    required = (
        authoritative_ledger,
        prospective_ledger,
        coverage_selection,
        baseline_path,
    )

    for path in required:
        if not path.exists():
            raise FileNotFoundError(path)

    ledger_hash_before = sha256(authoritative_ledger)

    authoritative_rows = load_rows(authoritative_ledger)
    prospective_rows = load_rows(prospective_ledger)
    selection_rows = load_rows(coverage_selection)

    baseline = json.loads(
        baseline_path.read_text(encoding="utf-8")
    )

    invariance = verify_baseline_snapshot(
        baseline,
        authoritative_rows,
    )

    if invariance["status"] != "PASS":
        raise RuntimeError(
            "historical baseline invariance failure"
        )

    authoritative_by = {
        str(row["condition_id"]): row
        for row in authoritative_rows
    }

    prospective_ids = {
        str(row["condition_id"])
        for row in prospective_rows
    }

    selection_by = {
        str(row["condition_id"]): row
        for row in selection_rows
    }

    missing_truth = sorted(
        prospective_ids - set(authoritative_by)
    )
    if missing_truth:
        raise RuntimeError(
            "prospective ids missing from authoritative ledger: "
            + ",".join(missing_truth[:5])
        )

    missing_selection = sorted(
        prospective_ids - set(selection_by)
    )
    if missing_selection:
        raise RuntimeError(
            "prospective ids missing coverage selection: "
            + ",".join(missing_selection[:5])
        )

    print("阶段 1/4：加载 bounded raw indexes")

    btc_index = closed_raw_index(btc_dir)
    book_index = closed_raw_index(book_dir)

    print("btc_closed_files =", len(btc_index))
    print("book_closed_files =", len(book_index))

    online_regimes = load_online_regimes(derived)

    stale_after_ms = int(
        float(settings.live.book_stale_seconds) * 1000
    )

    features: list[dict] = []
    provenance: list[dict] = []
    coverage: list[dict] = []

    source_status = Counter()
    skipped_truth = Counter()

    print("\n阶段 2/4：构建 prospective_v4 low-memory features")

    for n, cid in enumerate(sorted(prospective_ids), 1):
        row = authoritative_by[cid]

        if not row.get("clean_flag"):
            skipped_truth["not_clean"] += 1
            continue

        if row.get("first_opp_end_ms") is None:
            skipped_truth["no_first_opp_end"] += 1
            continue

        if not row.get("y30_horizon_eligible"):
            skipped_truth["y30_not_eligible"] += 1
            continue

        selection = selection_by[cid]
        status = str(selection.get("status") or "UNKNOWN")
        source_status[status] += 1

        prediction = int(row["first_opp_end_ms"])
        cutoff = cutoff_timestamp(
            prediction,
            CUTOFF_MODE,
        )

        market_start = int(row["market_start_ms"])

        btc_files = []
        book_files = []

        if status == "ELIGIBLE":
            btc_session_id = selection.get("btc_session_id")
            book_session_id = selection.get(
                "book_session_id"
            )

            if not btc_session_id or not book_session_id:
                raise RuntimeError(
                    f"eligible source selection missing "
                    f"session id: {cid}"
                )

            # Frozen BTC features may need a tick at/before
            # the 30-second anchor.  Stay inside the unique
            # selected recorder session and include exactly
            # one predecessor file.
            btc_floor = min(
                market_start - 1000,
                cutoff - 30_000,
            )

            btc_files = files_for_time_window(
                btc_index,
                btc_floor,
                cutoff,
                session_id=str(btc_session_id),
                include_predecessor=True,
            )

            # Book features use snapshots/coverage through
            # 30 seconds before cutoff.  A same-session
            # predecessor is required for bounded state.
            book_floor = cutoff - 30_000

            book_files = files_for_time_window(
                book_index,
                book_floor,
                cutoff,
                session_id=str(book_session_id),
                include_predecessor=True,
            )

        if btc_files:
            btc = compute_btc_features_from_files(
                btc_files,
                market_start_ms=market_start,
                cutoff_ms=cutoff,
            )
        else:
            # Preserve frozen missing-data schema without
            # materializing any raw directory.
            btc = compute_btc_features(
                [],
                market_start,
                cutoff,
            )

        if book_files:
            book = compute_book_features_from_files(
                book_files,
                condition_id=cid,
                cutoff_ms=cutoff,
                opp_outcome=row.get("first_opp_direction"),
                initial_outcome=row.get(
                    "initial_direction"
                ),
                stale_after_ms=stale_after_ms,
            )
        else:
            book = compute_book_features(
                [],
                cutoff,
                row.get("first_opp_direction"),
                row.get("initial_direction"),
                stale_after_ms=stale_after_ms,
            )

        feature, prov, cov = assemble_feature_row(
            row,
            btc=btc,
            book=book,
            btc_files=public_file_list(btc_files),
            book_files=public_file_list(book_files),
            authoritative_ledger=authoritative_ledger,
            online_regime_id=online_regimes.get(cid, 0),
            btc_threshold=btc_threshold,
            book_threshold=book_threshold,
        )

        features.append(feature)
        provenance.extend(prov)
        coverage.append(cov)

        if n % 100 == 0:
            print(
                f"processed={n}/{len(prospective_ids)} "
                f"features={len(features)} "
                f"eligible="
                f"{sum(r['model_eligible'] for r in coverage)}"
            )

    print("\n阶段 3/4：point-in-time provenance 验证")

    validate_provenance(provenance)

    model_eligible = sum(
        bool(row.get("model_eligible"))
        for row in features
    )

    print("prospective_markets =", len(prospective_ids))
    print("feature_rows =", len(features))
    print("provenance_rows =", len(provenance))
    print("coverage_rows =", len(coverage))
    print("model_eligible =", model_eligible)
    print("source_selection_status =", dict(source_status))
    print("truth_skips =", dict(skipped_truth))
    print(
        "historical_baseline_status =",
        invariance["status"],
    )

    if not features:
        raise RuntimeError(
            "no prospective feature rows produced"
        )

    ledger_hash_after_compute = sha256(
        authoritative_ledger
    )

    if ledger_hash_after_compute != ledger_hash_before:
        raise RuntimeError(
            "authoritative ledger changed during feature build"
        )

    print("\n阶段 4/4：artifact publication")

    if not publish:
        print("publish = NO")
        print("PROSPECTIVE_FEATURE_DRY_RUN_PASS")
        print("AUTHORITATIVE_LEDGER_NOT_MODIFIED")
        return 0

    features_dir = derived / "features"
    features_dir.mkdir(parents=True, exist_ok=True)

    feature_path = (
        features_dir
        / f"pretrade_features_{run_id}.parquet"
    )
    provenance_path = (
        features_dir
        / f"feature_provenance_{run_id}.parquet"
    )
    coverage_path = (
        features_dir
        / f"coverage_audit_{run_id}.parquet"
    )

    atomic_write_parquet(features, feature_path)
    atomic_write_parquet(provenance, provenance_path)
    atomic_write_parquet(coverage, coverage_path)

    ledger_hash_after_write = sha256(
        authoritative_ledger
    )

    if ledger_hash_after_write != ledger_hash_before:
        raise RuntimeError(
            "authoritative ledger changed during publication"
        )

    print("feature_artifact =", feature_path)
    print("provenance_artifact =", provenance_path)
    print("coverage_artifact =", coverage_path)
    print("PROSPECTIVE_FEATURE_PUBLISH_PASS")
    print("AUTHORITATIVE_LEDGER_NOT_MODIFIED")

    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__
    )

    parser.add_argument(
        "--publish",
        action="store_true",
        help="write feature/provenance/coverage artifacts",
    )

    parser.add_argument(
        "--run-id",
        default=(
            "prospective-v4-lowmem-"
            + datetime.now(timezone.utc).strftime(
                "%Y%m%dT%H%M%SZ"
            )
        ),
    )

    parser.add_argument(
        "--btc-coverage-threshold",
        type=float,
        default=0.99,
    )

    parser.add_argument(
        "--book-coverage-threshold",
        type=float,
        default=0.99,
    )

    args = parser.parse_args(argv)

    return execute(
        publish=args.publish,
        run_id=args.run_id,
        btc_threshold=args.btc_coverage_threshold,
        book_threshold=args.book_coverage_threshold,
    )


if __name__ == "__main__":
    raise SystemExit(main())
