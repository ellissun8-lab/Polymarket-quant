"""Incremental collector for std0's public Polymarket fills.

Uses the official public trades endpoint of the Polymarket data API
(``GET {data_api_base}/trades``) filtered by wallet.

Design notes
------------
* ``takerOnly`` is always sent explicitly (never relying on API defaults);
  the value used is recorded in every sync run and in the raw page audit
  trail.
* Deduplication uses :func:`trade_identity`: a stable hash of immutable fill
  fields (``transactionHash`` + asset + side + size + price + timestamp +
  outcomeIndex), or an explicit id field when the API provides one. Re-running
  a sync never produces duplicate raw records.
* Raw API response bodies are persisted verbatim (one file per page) under
  ``data/raw/api_pages/<run_id>/``.
* The endpoint is offset-paginated. Deep history is guarded by
  ``sync.max_offset``; callers doing deep backfills should pass
  ``use_time_params=True`` with ``start``/``end`` windows, which the syncer
  VERIFIES on the first page (if the API ignores the window parameters the
  sync aborts with status ``time_params_not_honored`` instead of silently
  fetching everything).
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import requests

from std0_quant.config import Settings
from std0_quant.storage import (
    AppendOnlyNDJSON,
    RawPageStore,
    SqliteState,
    envelope,
    new_run_id,
    sha256_hex,
)
from std0_quant.timeutil import parse_ts_to_ms, utc_now_ms

logger = logging.getLogger(__name__)

# How the fetch transport is injected for tests: (url, params) -> (status_code, body_text)
FetchFn = Callable[[str, dict[str, Any]], tuple[int, str]]


class APIUnavailableError(RuntimeError):
    """Raised when the trades API cannot be reached after all retries."""


class APIBadRequestError(APIUnavailableError):
    """Non-retryable 4xx response. Carries status/body so callers can react
    to API-specific errors (e.g. the offset cap) instead of failing blindly."""

    def __init__(self, message: str, status_code: int = 0, body: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


# Body marker of the live API's hard offset cap (HTTP 400, empirically verified:
# {"error":"max historical trades offset of 10000 exceeded"}).
OFFSET_CAP_MARKER = "max historical trades offset"


def default_fetch(session: requests.Session, timeout: float) -> FetchFn:
    def fetch(url: str, params: dict[str, Any]) -> tuple[int, str]:
        response = session.get(url, params=params, timeout=timeout)
        return response.status_code, response.text

    return fetch


class RetryingClient:
    """HTTP GET client with exponential backoff on network errors / 429 / 5xx."""

    def __init__(
        self,
        base_url: str,
        fetch_fn: FetchFn | None = None,
        max_retries: int = 5,
        backoff_base_seconds: float = 1.5,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._fetch = fetch_fn or default_fetch(requests.Session(), 30.0)
        self.max_retries = max_retries
        self.backoff_base_seconds = backoff_base_seconds
        self._sleep = sleeper

    def get(self, path: str, params: dict[str, Any]) -> tuple[int, str]:
        url = f"{self.base_url}{path}"
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                status, body = self._fetch(url, params)
            except Exception as exc:  # network layer raises many exception types
                last_error = exc
                status, body = -1, ""
            if status == 200:
                return status, body
            retryable = status == -1 or status == 429 or 500 <= status < 600
            if not retryable or attempt == self.max_retries:
                if status != 200:
                    raise APIBadRequestError(
                        f"GET {url} failed: status={status} attempts={attempt + 1} "
                        f"last_error={last_error!r} body={body[:200]!r}",
                        status_code=status,
                        body=body,
                    )
            delay = self.backoff_base_seconds * (2**attempt)
            logger.warning(
                "retryable fetch failure", extra={
                    "url": url, "status": status, "attempt": attempt + 1,
                    "sleep_seconds": delay, "error": repr(last_error),
                },
            )
            self._sleep(delay)
        raise APIUnavailableError(f"GET {url} exhausted retries (unreachable)")  # pragma: no cover


def trade_identity(record: dict[str, Any]) -> str:
    """Stable identity for one fill record.

    Preference order:
    1. an explicit unique id field if the API returns one;
    2. sha256 over the immutable fill tuple
       ``transactionHash|asset|side|size|price|timestamp|outcomeIndex``.

    Known limitation (documented in README): two byte-identical fills inside
    the same transaction that agree on every field above collapse into one
    identity. This cannot be distinguished from an API replay using public
    data alone.
    """
    for key in ("id", "tradeId", "trade_id"):
        value = record.get(key)
        if value not in (None, ""):
            return f"id:{value}"
    parts = [
        str(record.get("transactionHash") or ""),
        str(record.get("asset") or ""),
        str(record.get("side") or ""),
        str(record.get("size") or ""),
        str(record.get("price") or ""),
        str(record.get("timestamp") or ""),
        str(record.get("outcomeIndex") if record.get("outcomeIndex") is not None else ""),
    ]
    return "ck:" + sha256_hex("|".join(parts))


def _to_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _to_int(value: object) -> int | None:
    try:
        if value is None:
            return None
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def normalize_fill(
    record: dict[str, Any], source: str, fetched_at_ms: int
) -> dict[str, Any]:
    """Map a raw API trade record onto the normalized fill schema.

    No record is ever dropped here: fields that cannot be parsed become
    ``None`` and are flagged downstream (``FIELD_INCOMPLETE``).
    """
    return {
        "fill_id": trade_identity(record),
        "proxy_wallet": record.get("proxyWallet"),
        "side": (str(record.get("side")).upper() if record.get("side") else None),
        "asset": record.get("asset"),
        "condition_id": record.get("conditionId"),
        "size": _to_float(record.get("size")),
        "price": _to_float(record.get("price")),
        "timestamp_ms": parse_ts_to_ms(record.get("timestamp")),
        "timestamp_raw": record.get("timestamp"),
        "title": record.get("title"),
        "slug": record.get("slug"),
        "outcome": record.get("outcome"),
        "outcome_index": _to_int(record.get("outcomeIndex")),
        "transaction_hash": record.get("transactionHash"),
        "source": source,
        "fetched_at_ms": fetched_at_ms,
        "raw_json": record,
    }


@dataclass
class SyncResult:
    run_id: str
    status: str  # complete | offset_limit_reached | time_params_not_honored | empty
    pages_fetched: int = 0
    records_fetched: int = 0
    new_trades: int = 0
    duplicates_skipped: int = 0
    # Records collapsed by identical trade_identity WITHIN one page. These are
    # potential API replays OR genuinely distinct same-second fills that public
    # data cannot tell apart (documented limitation; audited, never guessed).
    within_page_identity_collisions: int = 0
    earliest_ts_ms: int | None = None
    latest_ts_ms: int | None = None
    messages: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"status={self.status} pages={self.pages_fetched} records={self.records_fetched} "
            f"new={self.new_trades} dupes={self.duplicates_skipped}"
        )


@dataclass
class BackfillResult:
    """Aggregate over every window-sync of one backfill."""

    status: str = "complete"
    runs: list[SyncResult] = field(default_factory=list)
    pages_fetched: int = 0
    records_fetched: int = 0
    new_trades: int = 0
    duplicates_skipped: int = 0
    within_page_identity_collisions: int = 0
    earliest_ts_ms: int | None = None
    latest_ts_ms: int | None = None
    messages: list[str] = field(default_factory=list)

    @property
    def windows(self) -> int:
        return len(self.runs)

    @property
    def first_run_id(self) -> str:
        return self.runs[0].run_id if self.runs else ""


class Std0TradesSyncer:
    """Incremental, idempotent sync of std0's fills into the raw store."""

    NAMESPACE = "std0_trades"
    CURSOR_KEY = "std0_trades.max_timestamp_ms"
    # Safety margin before trusting an "everything older than cursor" page stop.
    EARLY_STOP_MARGIN_MS = 60_000

    def __init__(
        self,
        settings: Settings,
        state: SqliteState,
        raw_writer: AppendOnlyNDJSON,
        page_store: RawPageStore,
        client: RetryingClient | None = None,
        run_id: str | None = None,
    ) -> None:
        self.settings = settings
        self.state = state
        self.raw_writer = raw_writer
        self.page_store = page_store
        poly = settings.polymarket
        self.client = client or RetryingClient(
            poly.data_api_base,
            max_retries=poly.request_max_retries,
            backoff_base_seconds=poly.request_backoff_base_seconds,
        )
        self.run_id = run_id or new_run_id("sync-trades")
        self._explicit_run_id = run_id
        self.source = f"polymarket:data-api:trades:takerOnly={poly.sync.taker_only}"

    def sync(
        self,
        start_ms: int | None = None,
        end_ms: int | None = None,
        use_time_params: bool = False,
        full: bool = False,
    ) -> SyncResult:
        """Fetch fills and append only previously-unseen ones.

        ``full=True`` pages through the entire history up to ``max_offset``.
        ``full=False`` (incremental) stops early once pages are entirely older
        than the stored cursor and contain no new identities.
        """
        sync_cfg = self.settings.polymarket.sync
        # Each sync call gets its own run id unless one was injected explicitly.
        if not self._explicit_run_id:
            self.run_id = new_run_id("sync-trades")
        params_base: dict[str, Any] = {
            "user": self.settings.trader.wallet,
            "limit": sync_cfg.page_limit,
            "takerOnly": "true" if sync_cfg.taker_only else "false",
        }
        if use_time_params:
            if start_ms is not None:
                params_base["start"] = str(start_ms // 1000)
            if end_ms is not None:
                params_base["end"] = str(end_ms // 1000)

        self.state.start_run(
            self.run_id, "std0_trades_sync",
            {
                "wallet": self.settings.trader.wallet,
                "taker_only": sync_cfg.taker_only,
                "limit": sync_cfg.page_limit,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "use_time_params": use_time_params,
                "full": full,
                "source": self.source,
            },
        )
        cursor_ms = _parse_int(self.state.get_kv(self.CURSOR_KEY)) or 0
        result = SyncResult(run_id=self.run_id, status="complete")

        offset = 0
        page_index = 0
        order_is_desc: bool | None = None
        try:
            while True:
                params = dict(params_base, offset=offset)
                try:
                    status_code, body = self.client.get("/trades", params)
                except APIBadRequestError as exc:
                    if OFFSET_CAP_MARKER in exc.body:
                        # Live API hard cap: offset > 10000 is rejected. This is
                        # a paging boundary, not a failure -- surface it so the
                        # caller can slice the window (sync_backfill does).
                        # Persist the rejected request too (audit trail).
                        self.page_store.save_page(
                            self.run_id, page_index, "/trades", params,
                            exc.status_code, exc.body,
                        )
                        result.status = "offset_limit_reached"
                        result.messages.append(
                            f"API rejected offset={offset}: {exc.body[:200]}"
                        )
                        break
                    raise
                self.page_store.save_page(
                    self.run_id, page_index, "/trades", params, status_code, body
                )
                records = _parse_records(body)
                result.pages_fetched += 1
                result.records_fetched += len(records)

                timestamps = [
                    parse_ts_to_ms(r.get("timestamp")) for r in records
                ]
                valid_ts = [t for t in timestamps if t is not None]
                if valid_ts:
                    result.earliest_ts_ms = (
                        min(result.earliest_ts_ms, min(valid_ts))
                        if result.earliest_ts_ms is not None else min(valid_ts)
                    )
                    result.latest_ts_ms = (
                        max(result.latest_ts_ms, max(valid_ts))
                        if result.latest_ts_ms is not None else max(valid_ts)
                    )
                if order_is_desc is None and len(valid_ts) >= 2:
                    order_is_desc = valid_ts[0] >= valid_ts[-1]
                    logger.info("detected API result order", extra={
                        "descending": order_is_desc})

                if use_time_params and page_index == 0 and records:
                    self._verify_time_window(records, start_ms, end_ms, result)
                    if result.status == "time_params_not_honored":
                        break

                page_ids = [trade_identity(r) for r in records]
                result.within_page_identity_collisions += len(page_ids) - len(set(page_ids))
                new_on_page = self._append_new(records)
                result.new_trades += new_on_page
                result.duplicates_skipped += len(records) - new_on_page
                page_index += 1
                logger.info("page fetched", extra={
                    "page": page_index, "records": len(records), "new": new_on_page})

                if len(records) < sync_cfg.page_limit:
                    break  # exhausted
                offset += sync_cfg.page_limit
                if offset > sync_cfg.max_offset:
                    result.status = "offset_limit_reached"
                    result.messages.append(
                        f"stopped at max_offset={sync_cfg.max_offset}; re-run with "
                        "--start/--end (use_time_params) to slice deep history by time window"
                    )
                    break
                if (
                    not full
                    and order_is_desc
                    and valid_ts
                    and min(valid_ts) < cursor_ms - self.EARLY_STOP_MARGIN_MS
                    and new_on_page == 0
                ):
                    result.messages.append(
                        "incremental early stop: page entirely older than cursor"
                    )
                    break
                time.sleep(sync_cfg.sleep_between_pages_seconds)

            if result.records_fetched == 0:
                result.status = "empty"
        except Exception as exc:
            self.state.finish_run(self.run_id, "failed", {"error": repr(exc)})
            raise
        finally:
            if result.latest_ts_ms and result.latest_ts_ms > cursor_ms:
                self.state.set_kv(self.CURSOR_KEY, str(result.latest_ts_ms))

        self.state.finish_run(self.run_id, result.status, {"result": result.summary()})
        logger.info("sync finished", extra={
            "run_id": self.run_id, "status": result.status,
            "new_trades": result.new_trades})
        return result

    # -- deep-history backfill --------------------------------------------------

    def sync_backfill(
        self,
        start_ms: int,
        end_ms: int,
        max_windows: int = 1000,
        sleep_between_windows_seconds: float = 1.0,
    ) -> "BackfillResult":
        """Fetch all fills in ``[start_ms, end_ms]`` despite the API offset cap.

        Strategy (verified against the live API): page a time window
        ``(start, window_end]`` until exhausted or until the API rejects the
        offset (its hard cap is 10000). Whenever the cap is hit, shrink the
        window to ``end = earliest_ts_seen - 1s`` and page the older slice;
        repeat until a window is fully covered down to ``start_ms`` or a
        window comes back empty (no older history exists).

        Every window is a separate audited sync run; dedupe makes window
        overlaps harmless, so the whole backfill is idempotent.
        """
        if end_ms <= start_ms:
            raise ValueError("backfill requires end_ms > start_ms")
        # Each window MUST be its own audited run: page files are keyed by
        # (run_id, page_index) and sync_runs rows by run_id, so sharing one
        # run_id across windows would collide. Ignore any injected run_id.
        if self._explicit_run_id:
            logger.warning(
                "sync_backfill ignores the injected run_id; every window "
                "gets its own run"
            )
            self._explicit_run_id = None
        aggregate = BackfillResult()
        window_end = end_ms
        for window_index in range(max_windows):
            result = self.sync(
                start_ms=start_ms, end_ms=window_end,
                use_time_params=True, full=True,
            )
            aggregate.runs.append(result)
            aggregate.pages_fetched += result.pages_fetched
            aggregate.records_fetched += result.records_fetched
            aggregate.new_trades += result.new_trades
            aggregate.duplicates_skipped += result.duplicates_skipped
            aggregate.within_page_identity_collisions += (
                result.within_page_identity_collisions
            )
            if result.earliest_ts_ms is not None:
                aggregate.earliest_ts_ms = (
                    min(aggregate.earliest_ts_ms, result.earliest_ts_ms)
                    if aggregate.earliest_ts_ms is not None
                    else result.earliest_ts_ms
                )
            if result.latest_ts_ms is not None:
                aggregate.latest_ts_ms = (
                    max(aggregate.latest_ts_ms, result.latest_ts_ms)
                    if aggregate.latest_ts_ms is not None
                    else result.latest_ts_ms
                )
            aggregate.messages.extend(f"[window {window_index}] {m}" for m in result.messages)

            if result.status == "offset_limit_reached":
                if result.earliest_ts_ms is not None and result.earliest_ts_ms > start_ms:
                    new_end = result.earliest_ts_ms - 1000  # timestamps are second-granular
                    if new_end >= window_end:
                        aggregate.status = "no_progress"
                        aggregate.messages.append(
                            "backfill stopped: window shrink made no progress "
                            f"(earliest_ts={result.earliest_ts_ms}, window_end={window_end})"
                        )
                        break
                    window_end = new_end
                    logger.info("backfill window shrunk", extra={
                        "window_index": window_index, "new_end_ms": window_end,
                        "earliest_ts_ms": result.earliest_ts_ms})
                    if sleep_between_windows_seconds:
                        time.sleep(sleep_between_windows_seconds)
                    continue
                # Paged past start_ms already: the window is fully covered.
                aggregate.status = "complete"
                break
            if result.status == "empty":
                aggregate.status = "complete"
                if window_index == 0 and aggregate.records_fetched == 0:
                    aggregate.messages.append("backfill window empty: no trades in range")
                break
            if result.status == "complete":
                # The window query was exhausted: every trade in
                # (start_ms, window_end] has been fetched.
                aggregate.status = "complete"
                break
            aggregate.status = result.status  # time_params_not_honored / failed
            break
        else:
            aggregate.status = "max_windows_reached"
            aggregate.messages.append(
                f"backfill stopped after max_windows={max_windows}; rerun with a "
                "smaller start/end range to continue"
            )
        if aggregate.status == "complete" and aggregate.earliest_ts_ms is not None \
                and aggregate.earliest_ts_ms > start_ms:
            aggregate.messages.append(
                f"note: earliest trade seen is {aggregate.earliest_ts_ms} > start "
                f"{start_ms}; no trades exist earlier in the requested range"
            )
        logger.info("backfill finished", extra={
            "status": aggregate.status, "windows": len(aggregate.runs),
            "new_trades": aggregate.new_trades})
        return aggregate

    # -- internals -----------------------------------------------------------

    def _append_new(self, records: list[dict[str, Any]]) -> int:
        """Append only records whose identity is new (state + in-run dedupe)."""
        seen_in_run: set[str] = set()
        new_records: list[dict[str, Any]] = []
        identities: list[str] = []
        fetched_at = utc_now_ms()
        for record in records:
            identity = trade_identity(record)
            if identity in seen_in_run:
                continue
            seen_in_run.add(identity)
            identities.append(identity)
            new_records.append(record)
        fresh = self.state.filter_new_keys(self.NAMESPACE, identities)
        fresh_set = set(fresh)
        to_append = [r for r, i in zip(new_records, identities) if i in fresh_set]
        if to_append:
            self.raw_writer.append_many(
                envelope(self.source, record, self.run_id, fetched_at)
                for record in to_append
            )
            self.state.register_keys(self.NAMESPACE, fresh, self.run_id)
        return len(to_append)

    def _verify_time_window(
        self,
        records: list[dict[str, Any]],
        start_ms: int | None,
        end_ms: int | None,
        result: SyncResult,
    ) -> None:
        """Ensure the API actually honored ``start``/``end``; abort otherwise."""
        tolerance_ms = 120_000  # tolerate small boundary slack in API filtering
        for record in records:
            ts = parse_ts_to_ms(record.get("timestamp"))
            if ts is None:
                continue
            if start_ms is not None and ts < start_ms - tolerance_ms:
                result.status = "time_params_not_honored"
                result.messages.append(
                    f"record ts={ts} precedes requested start={start_ms}; "
                    "API appears to ignore time-window parameters"
                )
                return
            if end_ms is not None and ts > end_ms + tolerance_ms:
                result.status = "time_params_not_honored"
                result.messages.append(
                    f"record ts={ts} exceeds requested end={end_ms}; "
                    "API appears to ignore time-window parameters"
                )
                return


def _parse_int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _parse_records(body: str) -> list[dict[str, Any]]:
    parsed = json.loads(body)
    if isinstance(parsed, list):
        return [r for r in parsed if isinstance(r, dict)]
    if isinstance(parsed, dict) and isinstance(parsed.get("data"), list):
        return [r for r in parsed["data"] if isinstance(r, dict)]
    raise ValueError(f"unexpected trades payload shape: {type(parsed)}")
