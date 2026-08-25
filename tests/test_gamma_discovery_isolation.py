"""GI gates: Gamma control-plane work must not starve Book data-plane work."""
from __future__ import annotations

import asyncio
import importlib
import importlib.util
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from std0_quant.audit.coverage_evidence import (
    COVERAGE_EVIDENCE_VERSION, coverage_bucket_gate,
)
from std0_quant.collectors.network_stability import is_receive_stale
from std0_quant.collectors.polymarket_book import BookState, MarketInfo


_SPEC = importlib.util.spec_from_file_location(
    "gamma_test_collect_live",
    Path(__file__).resolve().parents[1] / "scripts" / "collect_live.py",
)
collect_live = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(collect_live)


class Journal:
    session_id = "gamma-test"

    def __init__(self):
        self.events = []

    def emit(self, event, **details):
        self.events.append({"event": event, **details})


def _gamma_module():
    return importlib.import_module("std0_quant.collectors.gamma_discovery")


def _market(cid: str, start_ms: int) -> MarketInfo:
    return MarketInfo(cid, f"btc-updown-5m-{start_ms // 1000}", start_ms,
                      start_ms + 300_000, [(f"{cid}-up", "Up"),
                                           (f"{cid}-down", "Down")])


def _collector(monkeypatch, outcomes, now_ms=1_000_000):
    collector = object.__new__(collect_live.LiveCollector)
    collector._stop = asyncio.Event()
    collector._network_journal = Journal()
    collector._gamma_worker = _gamma_module().GammaDiscoveryWorker(
        collector._network_journal
    )
    iterator = iter(outcomes)
    collector.discover_market = lambda _at=None: next(iterator)
    monkeypatch.setattr(collect_live, "utc_now_ms", lambda: now_ms)
    return collector


async def _ticker(duration: float, interval: float = 0.1):
    loop = asyncio.get_running_loop()
    stamps = [loop.time()]
    deadline = stamps[0] + duration
    while loop.time() < deadline:
        await asyncio.sleep(interval)
        stamps.append(loop.time())
    return [b - a for a, b in zip(stamps, stamps[1:])]


def test_gi1_six_second_gamma_does_not_starve_book_frames(monkeypatch):
    current = _market("n", 1_000_000)
    nxt = _market("n1", current.market_end_ms)
    collector = _collector(monkeypatch, [])

    def slow(_at=None):
        time.sleep(6.0)
        return nxt

    collector.discover_market = slow

    async def scenario():
        ticker = asyncio.create_task(_ticker(6.3))
        await asyncio.sleep(0)
        found = await collector.wait_for_next_market(current, 0)
        gaps = await ticker
        collector._gamma_worker.close()
        return found, max(gaps)

    found, max_gap = asyncio.run(scenario())
    assert found is nxt
    assert max_gap < 0.35


def test_gi2_gamma_timeout_does_not_stop_book_collector(monkeypatch):
    current = _market("n", 1_000_000)
    nxt = _market("n1", current.market_end_ms)
    collector = _collector(monkeypatch, [])
    calls = 0

    def timeout_then_success(_at=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise requests.Timeout("synthetic timeout")
        return nxt

    collector.discover_market = timeout_then_success
    found = asyncio.run(collector.wait_for_next_market(current, 0))
    collector._gamma_worker.close()
    assert found is nxt and not collector._stop.is_set()
    assert any(e["event"] == "gamma_discovery_worker_error"
               for e in collector._network_journal.events)


def test_gi3_proxy_failure_retries_outside_book_critical_path(monkeypatch):
    current = _market("n", 1_000_000)
    nxt = _market("n1", current.market_end_ms)
    collector = _collector(monkeypatch, [])
    calls = 0

    def proxy_then_success(_at=None):
        nonlocal calls
        calls += 1
        time.sleep(0.2)
        if calls == 1:
            raise requests.ProxyError("proxy refused")
        return nxt

    collector.discover_market = proxy_then_success

    async def scenario():
        ticker = asyncio.create_task(_ticker(0.55, 0.05))
        found = await collector.wait_for_next_market(current, 0)
        gaps = await ticker
        return found, max(gaps)

    found, max_gap = asyncio.run(scenario())
    collector._gamma_worker.close()
    assert found is nxt and max_gap < 0.15


def test_gi4_retry_backoff_is_nonblocking(monkeypatch):
    current = _market("n", 1_000_000)
    nxt = _market("n1", current.market_end_ms)
    collector = _collector(monkeypatch, [None, nxt])

    async def scenario():
        ticker = asyncio.create_task(_ticker(0.45, 0.05))
        found = await collector.wait_for_next_market(current, 0.2)
        gaps = await ticker
        return found, max(gaps)

    found, max_gap = asyncio.run(scenario())
    collector._gamma_worker.close()
    assert found is nxt and max_gap < 0.15


def test_gi5_stale_discovery_result_is_discarded():
    module = _gamma_module()
    worker = module.GammaDiscoveryWorker(Journal())

    async def scenario():
        old = _market("old", 1_300_000)
        new = _market("new", 1_600_000)

        def slow_old():
            time.sleep(0.15)
            return old

        first = asyncio.create_task(worker.discover(1_300_000, slow_old))
        await asyncio.sleep(0.02)
        second = asyncio.create_task(worker.discover(1_600_000, lambda: new))
        return await first, await second

    first, second = asyncio.run(scenario())
    worker.close()
    assert first.status == "STALE_DISCOVERY_DISCARDED"
    assert second.status == "APPLIED" and second.value.condition_id == "new"


def test_gi6_correct_target_result_applies_exactly_once():
    module = _gamma_module()
    worker = module.GammaDiscoveryWorker(Journal())
    target = 1_300_000
    market = _market("n1", target)

    async def scenario():
        return await asyncio.gather(
            worker.discover(target, lambda: market),
            worker.discover(target, lambda: market),
        )

    results = asyncio.run(scenario())
    worker.close()
    assert sorted(result.status for result in results) == [
        "APPLIED", "DUPLICATE_RESULT_SUPPRESSED",
    ]


def test_gi7_discovery_worker_concurrency_is_bounded_to_one():
    module = _gamma_module()
    worker = module.GammaDiscoveryWorker(Journal())
    active = 0
    maximum = 0

    def call(target):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        time.sleep(0.03)
        active -= 1
        return _market(str(target), target)

    async def scenario():
        return await asyncio.gather(*[
            worker.discover(target, lambda target=target: call(target))
            for target in (1_300_000, 1_600_000, 1_900_000)
        ])

    asyncio.run(scenario())
    worker.close()
    assert maximum == 1 and worker.max_workers == 1


def test_gi8_market_rotation_token_mapping_is_unchanged():
    module = _gamma_module()
    worker = module.GammaDiscoveryWorker(Journal())
    market = _market("mapped", 1_300_000)
    result = asyncio.run(worker.discover(1_300_000, lambda: market))
    worker.close()
    assert result.status == "APPLIED"
    assert result.value.tokens == [("mapped-up", "Up"), ("mapped-down", "Down")]


def test_gi9_worker_failure_does_not_stop_recorders(monkeypatch):
    current = _market("n", 1_000_000)
    nxt = _market("n1", current.market_end_ms)
    collector = _collector(monkeypatch, [])
    calls = 0

    def fail_then_recover(_at=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("worker failure")
        return nxt

    collector.discover_market = fail_then_recover
    found = asyncio.run(collector.wait_for_next_market(current, 0))
    collector._gamma_worker.close()
    assert found is nxt and not collector._stop.is_set()


def test_gi10_watchdog_remains_responsive_during_slow_gamma(monkeypatch):
    current = _market("n", 1_000_000)
    nxt = _market("n1", current.market_end_ms)
    collector = _collector(monkeypatch, [])

    def slow(_at=None):
        time.sleep(0.5)
        return nxt

    collector.discover_market = slow

    async def scenario():
        ticker = asyncio.create_task(_ticker(0.65, 0.05))
        await collector.wait_for_next_market(current, 0)
        return max(await ticker)

    max_gap = asyncio.run(scenario())
    collector._gamma_worker.close()
    assert max_gap < 0.15
    assert is_receive_stale(1_000, 6_001, 5_000)


def test_gi11_99_percent_gate_is_unchanged():
    assert coverage_bucket_gate(297, 300, 0.99)
    assert not coverage_bucket_gate(989, 1000, 0.99)


def test_gi12_coverage_evidence_semantics_are_unchanged():
    module = importlib.import_module("std0_quant.audit.coverage_evidence")
    assert COVERAGE_EVIDENCE_VERSION == "coverage_evidence_v2"
    assert module.PENDING_ACTIVE_SOURCE_FILE != module.FINAL_PASS_OR_FAIL


def test_gi13_raw_receive_timestamp_semantics_are_unchanged():
    state = BookState("c", [("up", "Up"), ("down", "Down")])
    row = state.apply({"event_type": "book", "asset_id": "up",
                       "bids": [], "asks": [], "timestamp": "1000"}, 1234)
    assert row["receive_timestamp_ms"] == 1234


def test_gi14_multi_rotation_soak_keeps_latest_target_and_one_worker():
    module = _gamma_module()
    worker = module.GammaDiscoveryWorker(Journal())
    active = 0
    maximum = 0

    def outcome(target, delay, failure=False):
        def call():
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            time.sleep(delay)
            active -= 1
            if failure:
                raise requests.ProxyError("synthetic")
            return _market(str(target), target)
        return call

    async def scenario():
        tasks = []
        for target, delay, failure in [
            (1_300_000, 0.01, False), (1_600_000, 0.03, True),
            (1_900_000, 0.06, False), (2_200_000, 0.01, False),
        ]:
            tasks.append(asyncio.create_task(
                worker.discover(target, outcome(target, delay, failure))
            ))
            await asyncio.sleep(0.005)
        return await asyncio.gather(*tasks)

    results = asyncio.run(scenario())
    worker.close()
    assert maximum == 1
    assert results[-1].status == "APPLIED"
    assert results[-1].value.market_start_ms == 2_200_000
