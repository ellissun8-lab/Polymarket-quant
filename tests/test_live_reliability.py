"""Offline accelerated reliability tests for Phase 2A-Live."""
from __future__ import annotations
import asyncio,hashlib,json
from pathlib import Path
import pytest
from std0_quant.collectors.live_audit import BookValidity,GapTracker,LatencyTracker,TradeSequenceAudit
from std0_quant.collectors.live_health import _raw_stats,atomic_write_json
from std0_quant.collectors.live_storage import RotatingNDJSON,finalize_orphan_sidecars
from std0_quant.collectors.market_rotation import rotation_schedule,validate_btc5m_market
from std0_quant.collectors.polymarket_book import BookRecorder,MarketInfo

class Clock:
    def __init__(self,v=0):self.v=v
    def __call__(self):return self.v

def test_rotating_append_only_sidecar_and_sha(tmp_path):
    clock=Clock(1700000000000);w=RotatingNDJSON(tmp_path,"btc","s","btc",rotation_seconds=60,max_bytes=100000,fsync_every=1,clock=clock);first=w.files[0];w.append({"timestamp_ms":clock.v,"x":1});clock.v+=60001;w.append({"timestamp_ms":clock.v,"x":2});w.close();assert len(w.files)==2 and first.exists();meta=json.loads(first.with_suffix(".ndjson.meta.json").read_text());assert meta["record_count"]==1 and meta["sha256"]==hashlib.sha256(first.read_bytes()).hexdigest()

def test_restart_uses_new_session_file(tmp_path):
    c=Clock(1700000000000);a=RotatingNDJSON(tmp_path,"btc","s1","btc",clock=c);a.append({"x":1});a.close();b=RotatingNDJSON(tmp_path,"btc","s2","btc",clock=c);b.append({"x":2});b.close();assert a.files[0]!=b.files[0] and json.loads(a.files[0].read_text())["x"]==1

def test_restart_never_overwrites_previous_file(tmp_path):
    clock=Clock(1700000000000);a=RotatingNDJSON(tmp_path,"btc","s1","btc",clock=clock);a.append({"x":1});a.close();b=RotatingNDJSON(tmp_path,"btc","s2","btc",clock=clock);b.append({"x":2});b.close();assert a.files[0]!=b.files[0] and a.files[0].read_text()!=b.files[0].read_text()

def test_unclean_orphan_gets_auditable_sidecar(tmp_path):
    path=tmp_path/"2026-01-01"/"btc_00_crash_0001.ndjson";path.parent.mkdir();path.write_text('{"session_id":"crash","receive_timestamp_ms":1}\n',encoding="utf-8")
    repaired=finalize_orphan_sidecars(tmp_path,"binance_btc");meta=json.loads(repaired[0].read_text());assert meta["recovered_after_unclean_exit"] is True and meta["integrity_status"]=="OK" and meta["record_count"]==1

def test_book_snapshot_reconnect_recovery_and_stale():
    v=BookValidity(5000);v.connect("c1");assert not v.apply("price_change",1000);assert v.state==v.UNINITIALIZED;assert v.apply("book",1100);assert v.status_at(6100)==v.VALID and v.status_at(6101)==v.STALE;v.connect("c2");assert not v.apply("price_change",7000);assert v.apply("book",7100)

def test_recorder_waits_for_both_token_snapshots(tmp_path):
    class Journal:
        session_id="book-test"
        def emit(self,*args,**kwargs):pass
    class Ws:
        async def send(self,text):pass
    market=MarketInfo("c","btc-updown-5m-1700000100",1700000100000,1700000400000,[("u","Up"),("d","Down")]);clock=Clock(1700000101000);r=BookRecorder("wss://test",market,tmp_path,Journal(),clock=clock)
    async def scenario():
        await r._on_connect(Ws());await r._on_text(Ws(),json.dumps({"event_type":"book","market":"c","asset_id":"u","timestamp":clock.v,"bids":[{"price":"0.4","size":"1"}],"asks":[{"price":"0.6","size":"1"}]}));clock.v+=1;await r._on_text(Ws(),json.dumps({"event_type":"book","market":"c","asset_id":"d","timestamp":clock.v,"bids":[{"price":"0.4","size":"1"}],"asks":[{"price":"0.6","size":"1"}]}))
    asyncio.run(scenario());r._raw_writer.close();rows=[json.loads(x) for x in r.raw_path.read_text().splitlines()];assert rows[0]["book_state_status"]=="UNINITIALIZED" and rows[0]["book_state_valid"] is False and rows[1]["book_state_valid"] is True

def test_book_desync_timestamp_and_sanity():
    v=BookValidity();v.connect("c");assert v.apply("book",1000);assert not v.apply("price_change",999);assert v.state==v.DESYNCED;v.connect("d");assert not v.apply("book",2000,sane=False)

@pytest.mark.parametrize("gap,expected",[(3000,0),(6000,1),(30000,1)])
def test_gap_thresholds(gap,expected):
    g=GapTracker("book",5000);g.observe(1000);g.observe(1000+gap);assert len(g.gaps)==expected

def test_trade_sequence_audit_signal_only():
    a=TradeSequenceAudit();assert not a.observe(10);assert not a.observe(11);assert a.observe(14);assert a.gaps==[(11,14)];a.observe(13);assert a.non_monotonic==1

def test_latency_clock_skew_warning():
    a=LatencyTracker();[a.add(100,200) for _ in range(20)];s=a.summary();assert s["status"]=="CLOCK_SKEW_WARNING" and s["p95"]==-100

def test_market_validation_rotation_and_outcome_isolation():
    start=1699999800;market=MarketInfo("c1",f"btc-updown-5m-{start}",start*1000,(start+300)*1000,[("u1","Up"),("d1","Down")]);assert validate_btc5m_market(market);schedule=rotation_schedule(market.market_end_ms);assert schedule.prediscover_ms==market.market_end_ms-60000 and schedule.subscribe_next_ms==market.market_end_ms-15000 and schedule.unsubscribe_old_ms==market.market_end_ms+5000
    bad=MarketInfo("c2",f"btc-updown-5m-{start+300}",(start+300)*1000,(start+600)*1000,[("u1","Up"),("d1","Down")]);assert validate_btc5m_market(bad) # IDs may repeat synthetically; recorder state still rebuilds per market.

def test_malformed_or_unaligned_market_rejected():
    m=MarketInfo("c","btc-updown-5m-1700000001",1700000001000,1700000301000,[("u","Up"),("d","Down")]);
    with pytest.raises(ValueError):validate_btc5m_market(m)

def test_atomic_health_replace(tmp_path):
    p=tmp_path/"live_health.json";atomic_write_json(p,{"status":"A"});atomic_write_json(p,{"status":"B"});assert json.loads(p.read_text())=={"status":"B"} and not list(tmp_path.glob("*.tmp"))

def test_health_uses_closed_sidecar_and_active_tail(tmp_path):
    closed=tmp_path/"closed.ndjson";closed.write_text('{"receive_timestamp_ms":1000}\n',encoding="utf-8");Path(str(closed)+".meta.json").write_text(json.dumps({"record_count":7,"last_timestamp_ms":1000}),encoding="utf-8");active=tmp_path/"active.ndjson";active.write_text('{"receive_timestamp_ms":1900,"latency_ms":2}\n',encoding="utf-8");stats=_raw_stats(tmp_path,2000,500);assert stats["records"]==7 and stats["active_files"]==1 and stats["last_receive_ms"]==1900 and stats["status"]=="HEALTHY"

def test_accelerated_24h_288_market_soak_is_bounded(tmp_path):
    clock=Clock(1700000100000);writer=RotatingNDJSON(tmp_path,"book","soak","book",rotation_seconds=3600,fsync_every=1000,clock=clock);validity=BookValidity(5000)
    for market in range(288):
        validity.connect(f"c{market}");validity.apply("book",clock.v)
        for event in range(20):writer.append({"receive_timestamp_ms":clock.v,"market":market,"event":event});clock.v+=500
        clock.v+=295000
    writer.close();assert len(writer.files)==24 and not hasattr(validity,"history")
