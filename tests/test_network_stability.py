"""NS1-NS11 recorder network-stability regression gates."""
from __future__ import annotations

import asyncio,importlib.util,inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from std0_quant.audit.network_stability import coverage_exclusion_reasons
from std0_quant.audit.prospective import classify_market_lifecycle
from std0_quant.collectors.live_audit import BookValidity
from std0_quant.collectors.network_stability import (
    ProxyHealthMonitor,RestartStormDetector,classify_network_error,
    is_receive_stale,
)
from std0_quant.collectors.polymarket_book import MarketInfo
from std0_quant.collectors.recorder_reliability import health_step_isolated
from std0_quant.collectors.ws_runner import ReconnectingWsSession
from std0_quant.research.phase2b_stability import is_full_lifecycle_v4_market

_SPEC=importlib.util.spec_from_file_location(
    "network_test_collect_live",Path(__file__).resolve().parents[1]/"scripts"/"collect_live.py")
collect_live=importlib.util.module_from_spec(_SPEC);_SPEC.loader.exec_module(collect_live)


class Journal:
    session_id="network-test"
    def __init__(self):self.events=[]
    def emit(self,event,**details):self.events.append({"event":event,**details})


class FakeWs:
    def __init__(self,messages=()):self.messages=list(messages);self.closed=False
    async def send(self,_):pass
    async def close(self):self.closed=True
    def __aiter__(self):return self
    async def __anext__(self):
        if not self.messages:raise StopAsyncIteration
        return self.messages.pop(0)


def test_ns1_proxy_connection_refused_uses_backoff_then_retries():
    journal=Journal();attempts=0;session=None
    class Context:
        async def __aenter__(self):
            nonlocal attempts
            attempts+=1
            if attempts==1:raise ConnectionRefusedError(10061,"proxy refused")
            return FakeWs(["ok"])
        async def __aexit__(self,*_):return False
    async def on_text(_ws,_text):session.stop()
    async def scenario():
        nonlocal session
        session=ReconnectingWsSession("wss://example",journal,60,
            backoff_base_seconds=0,connect=lambda *_a,**_k:Context())
        return await session.run(lambda _ws:asyncio.sleep(0),on_text)
    stats=asyncio.run(scenario())
    assert attempts==2 and stats.messages==1
    assert any(e["event"]=="reconnect_scheduled" for e in journal.events)
    assert classify_network_error(ConnectionRefusedError())["reason"]=="PROXY_CONNECTION_REFUSED"


def test_ns2_proxy_recovers_to_healthy(monkeypatch):
    states=iter(["PROXY_UNREACHABLE","PROXY_HEALTHY"])
    monkeypatch.setattr("std0_quant.collectors.network_stability.probe_proxy",lambda _url:next(states))
    monitor=ProxyHealthMonitor("https://example",interval_seconds=0)
    monitor.proxy_url="http://127.0.0.1:7892"
    assert monitor.snapshot(now=1,force=True)["state"]=="PROXY_UNREACHABLE"
    assert monitor.snapshot(now=2,force=True)["state"]=="PROXY_HEALTHY"


def test_ns3_restart_storm_detection():
    detector=RestartStormDetector(threshold=3,window_seconds=10)
    assert detector.record(1).state=="NORMAL"
    detector.record(2)
    assert detector.record(3).state=="RESTART_STORM_WARNING"
    assert detector.snapshot(20).state=="NORMAL"


def test_ns4_fresh_receive_old_source_timestamp_is_not_stale():
    # Source time is deliberately irrelevant to this API.
    assert not is_receive_stale(last_receive_ms=9_999,now_ms=10_000,threshold_ms=5_000)


def test_ns5_true_no_receive_beyond_threshold_is_stale():
    assert is_receive_stale(last_receive_ms=1_000,now_ms=6_001,threshold_ms=5_000)


def test_ns6_btc_collector_is_not_recreated_by_market_rotation():
    source=inspect.getsource(collect_live.LiveCollector.run_book_forever)
    assert "run_btc" not in source and "_btc_recorder =" not in source


def test_ns7_next_market_prediscovery_retries(monkeypatch):
    collector=object.__new__(collect_live.LiveCollector);collector._stop=asyncio.Event()
    current=MarketInfo("a","btc-updown-5m-1",1_000,100_000,[("u","Up"),("d","Down")])
    nxt=MarketInfo("b","btc-updown-5m-100",100_000,400_000,[("u2","Up"),("d2","Down")])
    outcomes=iter([None,nxt]);collector.discover_market=lambda _at:next(outcomes)
    ticks=iter([10_000,10_000,20_000]);monkeypatch.setattr(collect_live,"utc_now_ms",lambda:next(ticks,20_000))
    found=asyncio.run(collector.wait_for_next_market(current,0))
    assert found is nxt


def test_ns8_first_valid_snapshot_readiness_requires_snapshot():
    validity=BookValidity(5000);validity.connect("c")
    assert not validity.apply("price_change",1000)
    assert validity.apply("book",1001)


def test_ns9_coverage_exclusion_reason_attribution():
    row={"market_start_ms":1000,"lifecycle":"PARTIAL_SESSION_MARKET",
         "collector_ready_before_start":False,"collector_continued_through_end":False,
         "btc_coverage_pct":.98,"book_coverage_pct":.97,
         "book_first_valid_receive_ms":3000,"network_gap_count":1,
         "market_discovery_ms":1500,"rotation_gap_ms":2000,"proxy_outage":True}
    reasons=coverage_exclusion_reasons(row)
    assert reasons[:4]==["PARTIAL_SESSION_START","SESSION_ENDED_EARLY",
                         "BTC_COVERAGE_LT_99","BOOK_COVERAGE_LT_99"]
    assert "PROXY_OUTAGE" in reasons


def test_ns10_health_failure_does_not_mutate_raw(tmp_path):
    raw=tmp_path/"raw.ndjson";raw.write_bytes(b'{"x":1}\n');before=raw.read_bytes()
    result=health_step_isolated(lambda:(_ for _ in ()).throw(MemoryError()),lambda _:None)
    assert result["status"]=="HEALTH_REPORT_FAILURE" and raw.read_bytes()==before


def test_ns11_phase2a_frozen_gate_unchanged():
    base={"collector_version":"phase2a_prospective_v4",
          "lifecycle":"FULL_LIFECYCLE_MARKET","btc_coverage_pct":.99,
          "book_coverage_pct":.99}
    assert is_full_lifecycle_v4_market(base)
    assert not is_full_lifecycle_v4_market({**base,"btc_coverage_pct":.989999})


def test_ns12_coverage_scan_is_out_of_process_not_gil_thread():
    source=inspect.getsource(collect_live.LiveCollector.run_book_forever)
    assert "run_market_coverage_report" in source
    assert "asyncio.to_thread(self.write_market_coverage_report" not in source


def test_ns13_midmarket_reconnect_does_not_change_endpoint_lifecycle():
    start=1_000_000;end=start+300_000;cid="c"
    book=[{"event":"connected","timestamp_ms":start-10_000},
          {"event":"subscribed","market":cid,"timestamp_ms":start-9_000},
          {"event":"connection_error","timestamp_ms":start+100_000},
          {"event":"connected","timestamp_ms":start+101_000},
          {"event":"subscribed","market":cid,"timestamp_ms":start+101_001},
          {"event":"session_end","timestamp_ms":end+1_000}]
    btc=[{"event":"connected","timestamp_ms":start-10_000},
         {"event":"connection_error","timestamp_ms":start+150_000},
         {"event":"connected","timestamp_ms":start+151_000},
         {"event":"session_end","timestamp_ms":end+1_000}]
    result=classify_market_lifecycle(cid,start,end,[book],[btc])
    assert result["lifecycle"]=="FULL_LIFECYCLE_MARKET"
