"""Offline health/status aggregation and atomic state publishing."""
from __future__ import annotations
import json,os,shutil,time
from datetime import datetime,timezone
from pathlib import Path
from statistics import median
from std0_quant.storage import read_ndjson
from std0_quant.timeutil import utc_now_ms

def read_last_line_bounded(path, max_bytes=65536, chunk_size=8192):
    """Return the last COMPLETE line of a growing file without a big-block
    ``read().splitlines()`` allocation spike.

    Reads the tail backwards in small chunks, bounded by ``max_bytes``; a
    torn final line (mid-append) is skipped in favour of the previous
    complete one. Returns the decoded line or None.
    """
    with open(path,"rb") as source:
        size=source.seek(0,2)
        remaining=min(size,max_bytes);tail=b""
        while remaining>0:
            step=min(chunk_size,remaining);remaining-=step
            source.seek(size-remaining-step)
            block=source.read(step)
            if not block:break
            tail=block+tail
            if b"\n" in tail[:-1]:break  # already have a complete line
        lines=tail.split(b"\n")
        truncated_front=size>len(tail)
        if truncated_front and len(lines)>1:lines=lines[1:]   # torn front fragment
        ended_with_newline=bool(lines) and lines[-1]==b""
        if ended_with_newline:lines=lines[:-1]
        if lines and not ended_with_newline:
            # unterminated final fragment: complete only as the sole line of
            # the whole file; otherwise it is mid-append and dropped
            if len(lines)>1 or truncated_front:lines=lines[:-1]
        for candidate in reversed(lines):
            if candidate.strip():
                return candidate.decode("utf-8",errors="replace")
    return None


def process_rss_mb(pid=None):
    """Best-effort RSS telemetry (MB). psutil when available, else None.

    ``pid=None`` reports the calling process (the supervisor when it builds
    health). A child pid reports the live collector process.
    """
    try:
        import psutil
        proc=psutil.Process(pid) if pid is not None else psutil.Process()
        return round(proc.memory_info().rss/1024**2,1)
    except Exception:
        return None


def _raw_stats(root,now_ms,stale_ms):
    count=0;last=None;lat=[];files=[];active_files=0
    if Path(root).is_dir():
        for path in sorted(Path(root).rglob("*.ndjson")):
            files.append(path)
            sidecar=Path(str(path)+".meta.json")
            if sidecar.exists():
                try:
                    meta=json.loads(sidecar.read_text(encoding="utf-8"));count+=int(meta.get("record_count",0));ts=meta.get("last_timestamp_ms");last=max(last or ts,ts) if ts is not None else last
                except (OSError,ValueError,TypeError):continue
            else:
                active_files+=1
                try:
                    line=read_last_line_bounded(path)
                    row=json.loads(line) if line else {};ts=row.get("receive_timestamp_ms");last=max(last or ts,ts) if ts is not None else last
                    if row.get("latency_ms") is not None:lat.append(float(row["latency_ms"]))
                except (OSError,ValueError,IndexError):continue
    lat.sort()
    def pct(q):return lat[min(int((len(lat)-1)*q),len(lat)-1)] if lat else None
    age=now_ms-last if last is not None else None
    return {"status":"NO_DATA" if last is None else "HEALTHY" if age<=stale_ms else "STALE","last_receive_ms":last,"last_age_ms":age,"records":count,"record_count_note":"closed_sidecars_only_while_active","files":len(files),"active_files":active_files,"latency_p50_ms":median(lat) if lat else None,"latency_p95_ms":pct(.95),"latency_p99_ms":pct(.99),"latency_max_ms":max(lat) if lat else None,"negative_latency_rate":sum(x<0 for x in lat)/len(lat) if lat else None}

def build_health(settings,now_ms=None):
    from std0_quant.config import resolve_path
    now_ms=utc_now_ms() if now_ms is None else now_ms;btc=_raw_stats(resolve_path(settings,"raw_btc_ticks"),now_ms,int(settings.live.btc_stale_seconds*1000));book=_raw_stats(resolve_path(settings,"raw_polymarket_book"),now_ms,int(settings.live.book_stale_seconds*1000));free=shutil.disk_usage(resolve_path(settings,"state").parent).free/1024**3
    covered=days=0;features_dir=resolve_path(settings,"derived")/"features";files=sorted(features_dir.glob("pretrade_features_*.parquet")) if features_dir.is_dir() else []
    if files:
        import pyarrow.parquet as pq
        rows=pq.read_table(files[-1],columns=["model_eligible","market_start_ms"]).to_pylist();eligible=[r for r in rows if r["model_eligible"]];covered=len(eligible);days=len({datetime.fromtimestamp(r["market_start_ms"]/1000,timezone.utc).date() for r in eligible})
    return {"generated_at_ms":now_ms,"system_time_ms":int(time.time()*1000),"monotonic_time_ns":time.monotonic_ns(),"estimated_clock_offset_ms":None,"process_rss_mb":process_rss_mb(),"btc_status":btc["status"],"book_status":book["status"],"btc":btc,"book":book,"disk_free_gb":free,"disk_status":"CRITICAL" if free<settings.live.disk_critical_gb else "WARNING" if free<settings.live.disk_warn_gb else "OK","fully_covered_observations":covered,"covered_days":days,"phase2a_gate":"READY_FOR_PHASE2A_REVALIDATION" if covered>=5000 and days>=14 else "ACCUMULATING_LIVE_DATA"}

def atomic_write_json(path,payload):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_name(path.name+f".{os.getpid()}.tmp")
    with open(tmp,"w",encoding="utf-8") as f:json.dump(payload,f,indent=2);f.flush();os.fsync(f.fileno())
    os.replace(tmp,path);return path

def journal_summary(sessions_dir,date=None):
    counts={};sessions=0
    for path in sorted(Path(sessions_dir).glob("*.ndjson")) if Path(sessions_dir).is_dir() else []:
        entries=list(read_ndjson(path));
        if date and not any(datetime.fromtimestamp(e.get("timestamp_ms",0)/1000,timezone.utc).date().isoformat()==date for e in entries):continue
        sessions+=1
        for e in entries:counts[e.get("event","UNKNOWN")]=counts.get(e.get("event","UNKNOWN"),0)+1
    return {"sessions":sessions,"events":counts}
