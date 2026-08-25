"""Bounded in-memory recorder audits: validity, sequence, gaps, latency."""
from __future__ import annotations
from dataclasses import dataclass,field
import numpy as np

@dataclass
class Gap:
    gap_start_ms:int;gap_end_ms:int;source:str;reason:str
    @property
    def gap_duration_ms(self):return self.gap_end_ms-self.gap_start_ms
    @property
    def start_ms(self):return self.gap_start_ms
    @property
    def end_ms(self):return self.gap_end_ms
    @property
    def duration_ms(self):return self.gap_duration_ms

@dataclass
class GapTracker:
    source:str;threshold_ms:int;last_ms:int|None=None;gaps:list[Gap]=field(default_factory=list)
    def observe(self,ts):
        if self.last_ms is not None and ts-self.last_ms>self.threshold_ms:self.gaps.append(Gap(self.last_ms,ts,self.source,"WATCHDOG_STALE"))
        self.last_ms=ts

class LatencyTracker:
    def __init__(self,max_samples=10000):self.max_samples=max_samples;self.values=[]
    def add(self,receive,exchange):
        if exchange is not None:
            self.values.append(receive-exchange)
            if len(self.values)>self.max_samples:self.values=self.values[-self.max_samples:]
    def summary(self):
        if not self.values:return {"count":0,"median":None,"p95":None,"p99":None,"max":None,"negative_rate":None,"status":"NO_DATA"}
        a=np.asarray(self.values);negative=float((a<0).mean());return {"count":len(a),"median":float(np.median(a)),"p95":float(np.percentile(a,95)),"p99":float(np.percentile(a,99)),"max":float(a.max()),"negative_rate":negative,"status":"CLOCK_SKEW_WARNING" if negative>.05 else "OK"}

class TradeSequenceAudit:
    def __init__(self):self.last_id=None;self.gaps=[];self.non_monotonic=0
    def observe(self,trade_id):
        if trade_id is None:return False
        gap=False
        if self.last_id is not None:
            if trade_id<=self.last_id:self.non_monotonic+=1
            elif trade_id>self.last_id+1:self.gaps.append((self.last_id,trade_id));gap=True
        self.last_id=trade_id;return gap

class BookValidity:
    UNINITIALIZED="UNINITIALIZED";VALID="VALID";STALE="STALE";DESYNCED="DESYNCED"
    def __init__(self,stale_ms=5000):self.stale_ms=stale_ms;self.state=self.UNINITIALIZED;self.connection_id=None;self.last_ts=None
    def connect(self,connection_id):self.connection_id=connection_id;self.state=self.UNINITIALIZED;self.last_ts=None
    def apply(self,event_type,receive_ts,sane=True):
        if self.last_ts is not None and receive_ts<self.last_ts:self.state=self.DESYNCED
        elif not sane:self.state=self.DESYNCED
        elif event_type=="book":self.state=self.VALID
        elif self.state!=self.VALID:self.state=self.UNINITIALIZED
        self.last_ts=receive_ts;return self.state==self.VALID
    def status_at(self,now):
        if self.state==self.VALID and self.last_ts is not None and now-self.last_ts>self.stale_ms:return self.STALE
        return self.state
