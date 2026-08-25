"""Crash-safe, append-only rotating raw storage with close sidecars."""
from __future__ import annotations
import hashlib,json,os,time
from datetime import datetime,timezone
from pathlib import Path
from typing import Any
from std0_quant.storage import canonical_json
from std0_quant.timeutil import utc_now_ms


class RawWriteError(RuntimeError):
    """A raw NDJSON append failed (disk full, closed handle, ...)."""


class SidecarFinalizationError(RuntimeError):
    """Hashing or sidecar publication failed for a CLOSED raw file.

    Kept distinct from RawWriteFailure: raw rows may already be durable on
    disk while the .meta.json sidecar is missing, so recovery differs.
    """


def streaming_sha256(path:Path)->str:
    """Low-allocation streaming SHA-256 (preallocated readinto buffer).

    Never loads the file into memory: hashing the event ledger or a large
    raw rotation must not spike RSS inside the supervisor loop.
    """
    digest=hashlib.sha256();buf=bytearray(1024*1024);view=memoryview(buf)
    with open(path,"rb") as source:
        while True:
            n=source.readinto(buf)
            if not n:break
            digest.update(view[:n])
    return digest.hexdigest()

# historic private alias (imported by older tests/tooling)
_sha256=streaming_sha256

def _atomic_sidecar(path:Path,payload:dict[str,Any])->Path:
    target=path.with_suffix(path.suffix+".meta.json");tmp=target.with_name(target.name+f".{os.getpid()}.tmp")
    with open(tmp,"w",encoding="utf-8") as f:json.dump(payload,f,indent=2);f.flush();os.fsync(f.fileno())
    os.replace(tmp,target);return target

def finalize_orphan_sidecars(root:Path|str,source:str,skip_newer_than_seconds:float=0.0)->list[Path]:
    """Audit raw files left without sidecars after an unclean process exit.

    ``skip_newer_than_seconds`` is the startup-recovery grace: files whose
    mtime is that recent are still plausibly owned by a surviving writer and
    are left alone (they will be finalized on the next unclean-exit sweep).
    """
    now=time.time();repaired=[]
    for path in sorted(Path(root).rglob("*.ndjson")) if Path(root).is_dir() else []:
        sidecar=path.with_suffix(path.suffix+".meta.json")
        if sidecar.exists():continue
        if skip_newer_than_seconds>0 and now-path.stat().st_mtime<skip_newer_than_seconds:continue
        count=0;first=last=None;parse_errors=0;session_id=None
        with open(path,"r",encoding="utf-8") as f:
            for line in f:
                if not line.strip():continue
                try:row=json.loads(line)
                except json.JSONDecodeError:parse_errors+=1;continue
                count+=1;session_id=session_id or row.get("session_id");ts=row.get("receive_timestamp_ms") or row.get("timestamp_ms");first=ts if first is None else first;last=ts
        stat=path.stat();payload={"file":str(path),"source":source,"opened_at_ms":int(stat.st_ctime*1000),"closed_at_ms":int(stat.st_mtime*1000),"record_count":count,"first_timestamp_ms":first,"last_timestamp_ms":last,"sha256":_sha256(path),"session_id":session_id,"recovered_after_unclean_exit":True,"parse_errors":parse_errors,"integrity_status":"OK" if parse_errors==0 else "PARTIAL_OR_INVALID_LINES"}
        repaired.append(_atomic_sidecar(path,payload))
    return repaired

class RotatingNDJSON:
    def __init__(self,root,source,session_id,prefix,rotation_seconds=3600,max_bytes=268435456,fsync_every=100,clock=utc_now_ms,journal=None):
        self.root=Path(root);self.source=source;self.session_id=session_id;self.prefix=prefix;self.rotation_ms=rotation_seconds*1000;self.max_bytes=max_bytes;self.fsync_every=fsync_every;self.clock=clock;self.journal=journal;self.files=[];self._fh=None;self._opened=0;self._count=0;self._first=None;self._last=None;self._sequence=0;self._open()
    @property
    def path(self):return self.files[-1]
    def _open(self):
        now=self.clock();dt=datetime.fromtimestamp(now/1000,timezone.utc);directory=self.root/dt.strftime("%Y-%m-%d");directory.mkdir(parents=True,exist_ok=True);self._sequence+=1;path=directory/f"{self.prefix}_{dt.strftime('%H')}_{self.session_id}_{self._sequence:04d}.ndjson";self._fh=open(path,"xb");self.files.append(path);self._opened=now;self._count=0;self._first=None;self._last=None
        if self.journal:self.journal.emit("file_open",source=self.source,file=str(path))
    def append(self,row:dict[str,Any]):
        now=self.clock()
        if self._count and (now-self._opened>=self.rotation_ms or self._fh.tell()>=self.max_bytes):self._close_current();self._open()
        payload=(canonical_json(row)+"\n").encode()
        try:self._fh.write(payload)
        except BaseException as exc:raise RawWriteError(f"raw write failed for {self.files[-1]}") from exc
        ts=row.get("receive_timestamp_ms") or row.get("timestamp_ms") or now;self._first=ts if self._first is None else self._first;self._last=ts;self._count+=1
        if self._count%self.fsync_every==0:self.flush()
    def flush(self):self._fh.flush();os.fsync(self._fh.fileno())
    def _close_current(self):
        if self._fh is None:return
        self.flush();self._fh.close();path=self.files[-1]
        meta={"file":str(path),"source":self.source,"opened_at_ms":self._opened,"closed_at_ms":self.clock(),"record_count":self._count,"first_timestamp_ms":self._first,"last_timestamp_ms":self._last,"sha256":None,"session_id":self.session_id,"recovered_after_unclean_exit":False,"parse_errors":0,"integrity_status":"OK"}
        try:
            meta["sha256"]=streaming_sha256(path)
            _atomic_sidecar(path,meta)
        except BaseException as exc:
            raise SidecarFinalizationError(f"sidecar finalization failed for {path}") from exc
        if self.journal:self.journal.emit("file_close",source=self.source,file=str(path),record_count=self._count,sha256=meta["sha256"])
        self._fh=None
    def close(self):self._close_current()
