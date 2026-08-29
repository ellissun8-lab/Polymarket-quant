"""Strict, prospective-only std0 trade synchronization.

This module deliberately does NOT change the legacy Std0TradesSyncer.

Safety properties:
- every page is checked against the requested public-time interval;
- malformed or out-of-window timestamps are never appended;
- records from an incomplete/capped window are staged, not published;
- offset-cap windows are bisected by whole timestamp seconds;
- a single second that still exceeds the pagination cap fails loudly instead
  of pretending the interval is complete;
- completed time slices are disjoint and additive.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from std0_quant.collectors.std0_trades import (
    APIBadRequestError,
    OFFSET_CAP_MARKER,
    RetryingClient,
    trade_identity,
)
from std0_quant.storage import (
    AppendOnlyNDJSON,
    RawPageStore,
    SqliteState,
    envelope,
    new_run_id,
)
from std0_quant.timeutil import parse_ts_to_ms, utc_now_ms


@dataclass
class WindowResult:
    run_id: str
    status: str
    start_s: int
    end_s: int
    pages_fetched: int = 0
    records_fetched: int = 0
    staged_records: int = 0
    new_trades: int = 0
    duplicates_skipped: int = 0
    messages: list[str] = field(default_factory=list)


@dataclass
class ProspectiveSyncResult:
    status: str = "complete"
    windows: int = 0
    pages_fetched: int = 0
    records_fetched: int = 0
    new_trades: int = 0
    duplicates_skipped: int = 0
    messages: list[str] = field(default_factory=list)


class ProspectiveTradesSyncer:
    """Strict bounded-range syncer for prospective truth only."""

    NAMESPACE = "std0_trades_prospective"

    def __init__(
        self,
        settings,
        state: SqliteState,
        raw_writer: AppendOnlyNDJSON,
        page_store: RawPageStore,
        client: RetryingClient | None = None,
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
        self.source = (
            "polymarket:data-api:trades:"
            f"takerOnly={poly.sync.taker_only}:prospective_strict_v1"
        )

    def sync_range(
        self,
        *,
        start_ms: int,
        end_ms: int,
        max_windows: int = 4096,
        sleep_between_windows_seconds: float = 0.0,
    ) -> ProspectiveSyncResult:
        """Fetch every public fill timestamp inside ``[start_ms, end_ms]``.

        API timestamps are second-granular.  The requested millisecond interval
        is therefore converted to the exact set of whole timestamp seconds
        whose public timestamps lie inside that interval.
        """
        if end_ms < start_ms:
            raise ValueError("prospective sync requires end_ms >= start_ms")

        start_s = (int(start_ms) + 999) // 1000
        end_s = int(end_ms) // 1000

        aggregate = ProspectiveSyncResult()

        if start_s > end_s:
            return aggregate

        # LIFO stack. Push older then newer so newer time is processed first.
        pending: list[tuple[int, int]] = [(start_s, end_s)]

        while pending:
            if aggregate.windows >= max_windows:
                aggregate.status = "max_windows_reached"
                aggregate.messages.append(
                    f"prospective sync exceeded max_windows={max_windows}"
                )
                return aggregate

            lo_s, hi_s = pending.pop()
            result = self._fetch_window(lo_s, hi_s)

            aggregate.windows += 1
            aggregate.pages_fetched += result.pages_fetched
            aggregate.records_fetched += result.records_fetched
            aggregate.new_trades += result.new_trades
            aggregate.duplicates_skipped += result.duplicates_skipped
            aggregate.messages.extend(
                f"[{lo_s},{hi_s}] {message}"
                for message in result.messages
            )

            if result.status in ("complete", "empty"):
                if sleep_between_windows_seconds and pending:
                    time.sleep(sleep_between_windows_seconds)
                continue

            if result.status == "offset_limit_reached":
                if lo_s == hi_s:
                    aggregate.status = "unsplittable_second"
                    aggregate.messages.append(
                        "offset cap reached inside one public timestamp second "
                        f"{lo_s}; completeness cannot be proven"
                    )
                    return aggregate

                mid_s = (lo_s + hi_s) // 2

                # Disjoint inclusive second ranges:
                # older [lo, mid], newer [mid+1, hi].
                pending.append((lo_s, mid_s))
                pending.append((mid_s + 1, hi_s))
                continue

            aggregate.status = result.status
            return aggregate

        aggregate.status = "complete"
        return aggregate

    def _fetch_window(self, start_s: int, end_s: int) -> WindowResult:
        run_id = new_run_id("prospective-trades")
        sync_cfg = self.settings.polymarket.sync

        params_base: dict[str, Any] = {
            "user": self.settings.trader.wallet,
            "limit": sync_cfg.page_limit,
            "takerOnly": "true" if sync_cfg.taker_only else "false",
            "start": str(start_s),
            "end": str(end_s),
        }

        self.state.start_run(
            run_id,
            "std0_trades_prospective_window",
            {
                "start_s": start_s,
                "end_s": end_s,
                "strict_client_boundary": True,
                "source": self.source,
            },
        )

        result = WindowResult(
            run_id=run_id,
            status="complete",
            start_s=start_s,
            end_s=end_s,
        )

        staged: list[dict[str, Any]] = []
        seen_identities: set[str] = set()
        offset = 0
        page_index = 0

        try:
            while True:
                params = dict(params_base, offset=offset)

                try:
                    status_code, body = self.client.get("/trades", params)
                except APIBadRequestError as exc:
                    if OFFSET_CAP_MARKER in exc.body:
                        self.page_store.save_page(
                            run_id,
                            page_index,
                            "/trades",
                            params,
                            exc.status_code,
                            exc.body,
                        )
                        result.status = "offset_limit_reached"
                        result.messages.append(
                            f"API offset cap reached at offset={offset}"
                        )
                        break
                    raise

                self.page_store.save_page(
                    run_id,
                    page_index,
                    "/trades",
                    params,
                    status_code,
                    body,
                )
                page_index += 1

                records = _parse_records(body)
                result.pages_fetched += 1
                result.records_fetched += len(records)

                # STRICT: validate the entire page BEFORE staging any row.
                violation = _strict_page_violation(
                    records,
                    start_s=start_s,
                    end_s=end_s,
                )
                if violation is not None:
                    result.status = violation["status"]
                    result.messages.append(violation["message"])
                    break

                for record in records:
                    identity = trade_identity(record)
                    if identity in seen_identities:
                        continue
                    seen_identities.add(identity)
                    staged.append(record)

                if len(records) < sync_cfg.page_limit:
                    result.status = "empty" if not staged else "complete"
                    break

                offset += sync_cfg.page_limit

                if offset > sync_cfg.max_offset:
                    result.status = "offset_limit_reached"
                    result.messages.append(
                        f"local max_offset={sync_cfg.max_offset} reached"
                    )
                    break

                if sync_cfg.sleep_between_pages_seconds:
                    time.sleep(sync_cfg.sleep_between_pages_seconds)

            # Publication boundary:
            # capped/invalid windows publish NOTHING.
            if result.status in ("complete", "empty") and staged:
                result.staged_records = len(staged)
                result.new_trades = self._append_new(staged)
                result.duplicates_skipped = len(staged) - result.new_trades

            self.state.finish_run(
                run_id,
                result.status,
                {
                    "start_s": start_s,
                    "end_s": end_s,
                    "pages_fetched": result.pages_fetched,
                    "records_fetched": result.records_fetched,
                    "staged_records": len(staged),
                    "published_new_trades": result.new_trades,
                    "messages": result.messages,
                },
            )
            return result

        except Exception as exc:
            self.state.finish_run(
                run_id,
                "failed",
                {
                    "start_s": start_s,
                    "end_s": end_s,
                    "error": repr(exc),
                },
            )
            raise

    def _append_new(self, records: list[dict[str, Any]]) -> int:
        identities = [trade_identity(record) for record in records]
        fresh = self.state.filter_new_keys(self.NAMESPACE, identities)
        fresh_set = set(fresh)

        fetched_at_ms = utc_now_ms()
        to_append = [
            record
            for record, identity in zip(records, identities)
            if identity in fresh_set
        ]

        if to_append:
            self.raw_writer.append_many(
                envelope(
                    self.source,
                    record,
                    "prospective-published",
                    fetched_at_ms,
                )
                for record in to_append
            )
            self.state.register_keys(
                self.NAMESPACE,
                fresh,
                "prospective-published",
            )

        return len(to_append)


def _strict_page_violation(
    records: list[dict[str, Any]],
    *,
    start_s: int,
    end_s: int,
) -> dict[str, str] | None:
    start_ms = start_s * 1000
    end_ms = end_s * 1000

    for record in records:
        ts = parse_ts_to_ms(record.get("timestamp"))

        if ts is None:
            return {
                "status": "timestamp_invalid",
                "message": "record has unparseable public timestamp",
            }

        if ts < start_ms or ts > end_ms:
            return {
                "status": "time_params_not_honored",
                "message": (
                    f"record ts={ts} lies outside strict requested "
                    f"[{start_ms},{end_ms}]"
                ),
            }

    return None


def _parse_records(body: str) -> list[dict[str, Any]]:
    parsed = json.loads(body)

    if isinstance(parsed, list):
        return [row for row in parsed if isinstance(row, dict)]

    if isinstance(parsed, dict) and isinstance(parsed.get("data"), list):
        return [
            row
            for row in parsed["data"]
            if isinstance(row, dict)
        ]

    raise ValueError(
        f"unexpected trades payload shape: {type(parsed)}"
    )
