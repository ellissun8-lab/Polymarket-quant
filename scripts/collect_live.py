"""CLI: live collection of the BTC Up/Down 5m order book + BTC ticks.

Runs two collectors concurrently:

* a Polymarket CLOB book recorder that follows the active BTC Up/Down 5m
  market (discovery via the Gamma API; when a market ends, waits for the
  next one, resubscribes, and writes a per-market coverage report);
* a Binance BTC/USDT trade-tick recorder (one continuous session).

Everything is append-only: raw NDJSON under ``data/raw/`` and session
journals under ``data/sessions/``. Ctrl-C stops both collectors cleanly.

NOTE (Phase 1): this is data collection only -- the project never places
orders, signs transactions, or handles keys.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(_PROJECT_SRC))

import requests

from std0_quant.collectors.btc_ticks import TickRecorder
from std0_quant.collectors.polymarket_book import (
    BookRecorder,
    find_active_market,
)
from std0_quant.collectors.std0_trades import default_fetch
from std0_quant.collectors.ws_runner import SessionJournal
from std0_quant.collectors.network_stability import (
    NETWORK_ENGINEERING_FIX_VERSION, classify_network_error,
    probe_proxy, proxy_for_url,
)
from std0_quant.collectors.gamma_discovery import (
    GAMMA_DISCOVERY_ISOLATION_FIX_VERSION,
    EventLoopLagTracker,
    GammaDiscoveryControlPlaneError,
    GammaDiscoveryWorker,
)
from std0_quant.audit.coverage import FileCoverageProvider, write_json_report
from std0_quant.audit.coverage_evidence import (
    COVERAGE_EVIDENCE_VERSION, COVERAGE_SELECTION_FIX_VERSION,
)
from std0_quant.config import load_settings, resolve_path
from std0_quant.logging_setup import setup_logging
from std0_quant.storage import new_run_id
from std0_quant.timeutil import utc_now_ms


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration-minutes", type=float, default=None,
                        help="stop after N minutes (default: run until Ctrl-C)")
    parser.add_argument("--poll-seconds", type=float, default=10.0,
                        help="market discovery poll interval")
    parser.add_argument("--continuous", action="store_true",
                        help="explicitly run until SIGINT/SIGTERM")
    return parser


class LiveCollector:
    def __init__(self, settings) -> None:
        self.settings = settings
        self.sessions_dir = resolve_path(settings, "sessions")
        self.raw_book_dir = resolve_path(settings, "raw_polymarket_book")
        self.raw_btc_dir = resolve_path(settings, "raw_btc_ticks")
        self.reports_dir = resolve_path(settings, "reports")
        self._session = requests.Session()
        self._fetch = default_fetch(
            self._session, settings.polymarket.request_timeout_seconds
        )
        self._stop = asyncio.Event()
        self._btc_recorder: TickRecorder | None = None
        self._book_recorder: BookRecorder | None = None
        self._book_recorders: set[BookRecorder] = set()
        self._network_journal: SessionJournal | None = None
        self._gamma_worker: GammaDiscoveryWorker | None = None
        self._event_loop_lag = EventLoopLagTracker()

    def request_stop(self) -> None:
        self._stop.set()
        if self._btc_recorder is not None:
            self._btc_recorder.stop()
        if self._book_recorder is not None:
            self._book_recorder.stop()
        for recorder in self._book_recorders:
            recorder.stop()

    # -- market discovery ------------------------------------------------------

    def discover_market(self, at_ms: int | None = None):
        book_cfg = self.settings.polymarket.book
        return find_active_market(
            self._fetch,
            self.settings.polymarket.gamma_api_base,
            utc_now_ms() if at_ms is None else at_ms,
            book_cfg.market_slug_prefix,
            window_seconds=book_cfg.market_window_seconds,
        )

    async def _discover_market_isolated(self, target_ms: int):
        if getattr(self, "_gamma_worker", None) is None:
            self._gamma_worker = GammaDiscoveryWorker(
                getattr(self, "_network_journal", None)
            )

        def _blocking_control_plane_call():
            try:
                return self.discover_market(target_ms)
            except requests.RequestException as exc:
                detail = classify_network_error(exc)
                proxy_url = proxy_for_url(self.settings.polymarket.gamma_api_base)
                context = {
                    "stage": "MARKET_DISCOVERY",
                    "error": repr(exc),
                    "proxy_state": probe_proxy(proxy_url),
                    "proxy_configured": bool(proxy_url),
                    **detail,
                }
                raise GammaDiscoveryControlPlaneError(
                    f"market discovery network failure: {detail['reason']}",
                    context=context,
                ) from exc

        result = await self._gamma_worker.discover(
            target_ms, _blocking_control_plane_call
        )
        if result.status == "FAILED":
            error = result.error
            if (self._network_journal is not None
                    and isinstance(error, GammaDiscoveryControlPlaneError)):
                self._network_journal.emit("market_discovery_error", **error.context)
            print(f"market discovery failure: {error!r}; preserving configured "
                  "route and retrying", flush=True)
            return None
        return result.value if result.status == "APPLIED" else None

    async def wait_for_market(self, poll_seconds: float):
        """Poll discovery until a market is found or stop is requested."""
        while not self._stop.is_set():
            now_ms = utc_now_ms()
            window_ms = self.settings.polymarket.book.market_window_seconds * 1000
            target_ms = (now_ms // window_ms) * window_ms
            market = await self._discover_market_isolated(target_ms)
            if market is not None:
                return market
            print("no active BTC 5m market found; retrying "
                  f"in {poll_seconds:.0f}s", flush=True)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=poll_seconds)
            except asyncio.TimeoutError:
                continue
        return None

    async def wait_for_next_market(self, current_market, poll_seconds: float):
        """Retry prediscovery until the next market starts.

        A transient Gamma/proxy failure at the first prediscovery attempt must
        not force a late post-boundary discovery or terminate both collectors.
        """
        while not self._stop.is_set() and utc_now_ms() < current_market.market_end_ms:
            market = await self._discover_market_isolated(
                current_market.market_end_ms
            )
            if market is not None and market.condition_id != current_market.condition_id:
                return market
            remaining = max(0.0, (current_market.market_end_ms-utc_now_ms())/1000)
            if remaining <= 0:
                break
            try:
                await asyncio.wait_for(self._stop.wait(),
                                       timeout=min(poll_seconds, remaining))
            except asyncio.TimeoutError:
                continue
        return None

    # -- sessions -----------------------------------------------------------------

    async def run_btc(self) -> None:
        btc = self.settings.btc
        session_id = new_run_id("btc")
        journal = SessionJournal(self.sessions_dir, session_id, "btc_ticks")
        recorder = TickRecorder(
            ws_url=btc.ws_url,
            source=f"{btc.source}:{btc.symbol}@trade",
            raw_dir=self.raw_btc_dir,
            journal=journal,
            stale_after_seconds=self.settings.live.btc_stale_seconds,
            backoff_base_seconds=btc.reconnect_backoff_base_seconds,
            backoff_max_seconds=min(btc.reconnect_backoff_max_seconds,
                                    self.settings.live.restart_max_backoff_seconds),
            rotation_seconds=self.settings.live.rotation_seconds,
            rotation_max_bytes=self.settings.live.rotation_max_bytes,
            fsync_every_records=self.settings.live.fsync_every_records,
        )
        self._btc_recorder = recorder
        try:
            stats = await recorder.run()
            print(f"btc session ended: {stats.messages} messages, "
                  f"{recorder.rows_written} ticks, {stats.reconnects} connects",
                  flush=True)
        finally:
            journal.close()

    async def run_book_forever(self, poll_seconds: float) -> None:
        book_cfg = self.settings.polymarket.book
        poly = self.settings.polymarket
        current = None
        while not self._stop.is_set():
            if current is None:
                market = await self.wait_for_market(poll_seconds)
                if market is None: break
                current = self._start_book(poly, book_cfg, market)
            recorder, task, journal, market = current
            print(f"recording market {market.slug} "
                  f"({market.condition_id}) until "
                  f"{datetime.fromtimestamp(market.market_end_ms / 1000, tz=timezone.utc).isoformat()}",
                  flush=True)
            await self._wait_until(
                market.market_end_ms
                - int(self.settings.live.market_prediscovery_seconds * 1000)
            )
            next_market = (None if self._stop.is_set() else
                           await self.wait_for_next_market(market, poll_seconds))
            if next_market is not None and next_market.condition_id != market.condition_id:
                journal.emit("market_discovered", market=next_market.condition_id,
                             slug=next_market.slug, role="next")
                await self._wait_until(
                    market.market_end_ms
                    - int(self.settings.live.market_overlap_seconds * 1000)
                )
                next_current = self._start_book(poly, book_cfg, next_market)
                journal.emit("market_rotate", from_market=market.condition_id,
                             to_market=next_market.condition_id,
                             overlap_seconds=self.settings.live.market_overlap_seconds)
            else:
                next_current = None
            await self._wait_until(
                market.market_end_ms
                + int(self.settings.live.market_grace_seconds * 1000)
            )
            recorder.stop()
            try:
                stats = await asyncio.wait_for(task, timeout=30)
            except asyncio.TimeoutError:
                task.cancel()
                stats = recorder.stats
            journal.close()
            self._book_recorders.discard(recorder)
            print(f"market session ended: {market.slug} rows={recorder.rows_written} "
                  f"reconnects={stats.reconnects}", flush=True)
            # JSON decoding in a Python thread still contends for the GIL and
            # previously starved websocket frame handling / the watchdog.
            # Run the non-critical closed-market audit in a below-normal child
            # process; market N+1 is already recording during this await.
            await self.run_market_coverage_report(market)
            current = next_current

    async def run_market_coverage_report(self, market) -> None:
        flags = 0
        if os.name == "nt":
            flags = (getattr(subprocess,"CREATE_NO_WINDOW",0) |
                     getattr(subprocess,"BELOW_NORMAL_PRIORITY_CLASS",0))
        process = await asyncio.create_subprocess_exec(
            sys.executable,str(_PROJECT_ROOT/"scripts"/"report_market_coverage.py"),
            "--condition-id",market.condition_id,"--slug",market.slug,
            cwd=str(_PROJECT_ROOT),stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,creationflags=flags,
        )
        code = await process.wait()
        if self._network_journal is not None:
            self._network_journal.emit(
                "coverage_audit_exit", market=market.condition_id,
                slug=market.slug, exit_code=code, execution="SEPARATE_PROCESS",
            )

    def _start_book(self, poly, book_cfg, market):
        session_id = new_run_id("book")
        journal = SessionJournal(self.sessions_dir, session_id,
                                 "polymarket_book")
        journal.emit("market_discovered", market=market.condition_id,
                     slug=market.slug, role="active")
        recorder = BookRecorder(
            ws_url=poly.ws_url, market=market, raw_dir=self.raw_book_dir,
            journal=journal,
            stale_after_seconds=self.settings.live.book_stale_seconds,
            backoff_base_seconds=book_cfg.reconnect_backoff_base_seconds,
            backoff_max_seconds=min(
                book_cfg.reconnect_backoff_max_seconds,
                self.settings.live.restart_max_backoff_seconds,
            ),
            rotation_seconds=self.settings.live.rotation_seconds,
            rotation_max_bytes=self.settings.live.rotation_max_bytes,
            fsync_every_records=self.settings.live.fsync_every_records,
            writer_queue_batches=self.settings.live.writer_queue_batches,
        )
        self._book_recorder = recorder
        self._book_recorders.add(recorder)
        return recorder, asyncio.create_task(recorder.run()), journal, market

    async def _wait_until(self, target_ms: int) -> None:
        remaining = target_ms - utc_now_ms()
        if remaining <= 0: return
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=remaining / 1000)
        except asyncio.TimeoutError:
            pass

    def write_market_coverage_report(self, market, book_files=None) -> None:
        try:
            provider = FileCoverageProvider(
                book_dir=self.raw_book_dir,
                btc_dir=self.raw_btc_dir,
                sessions_dir=self.sessions_dir,
                bucket_seconds=self.settings.coverage.bucket_seconds,
                gap_threshold_seconds=self.settings.coverage.gap_threshold_seconds,
                book_stale_seconds=self.settings.live.book_stale_seconds,
                book_files=book_files,
                btc_files=list(self._btc_recorder._raw_writer.files) if self._btc_recorder else None,
            )
            report = provider.market_report(
                market.condition_id, market.market_start_ms, market.market_end_ms
            )
            stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            path = write_json_report(
                report, self.reports_dir / "coverage" / f"{market.slug}_{stamp}.json"
            )
            print(f"coverage report: {path}", flush=True)
        except Exception as exc:
            print(f"WARNING: coverage report failed for {market.slug}: {exc!r}",
                  flush=True)

    # -- top level -------------------------------------------------------------------

    async def run(self, duration_minutes: float | None, poll_seconds: float) -> None:
        self._network_journal = SessionJournal(
            self.sessions_dir, new_run_id("network"), "live_collector_network"
        )
        self._network_journal.emit(
            "network_engineering_version",
            engineering_fix_version=NETWORK_ENGINEERING_FIX_VERSION,
            gamma_discovery_isolation_fix_version=(
                GAMMA_DISCOVERY_ISOLATION_FIX_VERSION
            ),
            coverage_evidence_version=COVERAGE_EVIDENCE_VERSION,
            coverage_selection_fix_version=COVERAGE_SELECTION_FIX_VERSION,
        )
        self._gamma_worker = GammaDiscoveryWorker(self._network_journal)
        tasks = [
            asyncio.create_task(self.run_btc()),
            asyncio.create_task(self.run_book_forever(poll_seconds)),
            asyncio.create_task(self._event_loop_lag.run(self._stop)),
        ]
        if duration_minutes is not None:
            async def _timeout() -> None:
                await asyncio.sleep(duration_minutes * 60)
                self.request_stop()
            tasks.append(asyncio.create_task(_timeout()))
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        failure = None
        for task in done:
            if not task.cancelled() and task.exception() is not None:
                print(f"collector task failed: {task.exception()!r}", flush=True)
                failure = task.exception()
        self.request_stop()
        for task in pending:
            try:
                await asyncio.wait_for(task, timeout=30)
            except asyncio.TimeoutError:
                task.cancel()
        # retrieve every pending-task exception: cancelled tasks whose
        # exceptions are never read surface as "Task exception was never
        # retrieved" at interpreter shutdown
        await asyncio.gather(*pending, return_exceptions=True)
        event_loop_lag_ms = self._event_loop_lag.snapshot()
        self._network_journal.close(
            engineering_fix_version=NETWORK_ENGINEERING_FIX_VERSION,
            gamma_discovery_isolation_fix_version=(
                GAMMA_DISCOVERY_ISOLATION_FIX_VERSION
            ),
            coverage_evidence_version=COVERAGE_EVIDENCE_VERSION,
            coverage_selection_fix_version=COVERAGE_SELECTION_FIX_VERSION,
            event_loop_lag_ms=event_loop_lag_ms,
            exit_status="FAILED" if failure is not None else "CLEAN",
        )
        if self._gamma_worker is not None:
            self._gamma_worker.close()
        if failure is not None:
            raise failure


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    if args.continuous and args.duration_minutes is not None:
        raise SystemExit("--continuous and --duration-minutes are mutually exclusive")
    settings = load_settings()
    log_path = setup_logging(resolve_path(settings, "logs"), "collect_live")
    collector = LiveCollector(settings)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _signal(*_args) -> None:
        collector.request_stop()

    handled_signals = [signal.SIGINT, signal.SIGTERM]
    if hasattr(signal, "SIGBREAK"):  # Windows CTRL_BREAK_EVENT
        handled_signals.append(signal.SIGBREAK)
    for sig in handled_signals:
        try:
            loop.add_signal_handler(sig, _signal)
        except NotImplementedError:  # Windows
            signal.signal(sig, lambda *_a: collector.request_stop())

    print(f"trader under study: {settings.trader.name} "
          f"({settings.trader.wallet})", flush=True)
    print(f"book ws:  {settings.polymarket.ws_url}", flush=True)
    print(f"btc ws:   {settings.btc.ws_url}", flush=True)
    print(f"log:      {log_path}", flush=True)
    try:
        loop.run_until_complete(collector.run(args.duration_minutes, args.poll_seconds))
    finally:
        loop.close()
    print("collectors stopped", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
