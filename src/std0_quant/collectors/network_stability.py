"""Network dependency diagnostics for the live recorder.

Engineering-only helpers: these functions don't change raw schemas, timestamp
semantics, book reconstruction, coverage rules, or any research definition.
"""
from __future__ import annotations

import socket
import time
import urllib.request
from collections import deque
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

NETWORK_ENGINEERING_FIX_VERSION = "network_stability_fix_v1"


def proxy_for_url(url: str) -> str | None:
    """Return the configured OS/environment proxy for *url*, if any.

    Python ``requests`` and websockets 15 both use this proxy policy on the
    current Windows runtime. No direct fallback is attempted here.
    """
    scheme = urlparse(url).scheme.lower()
    proxy_scheme = "https" if scheme in {"https", "wss"} else "http"
    return urllib.request.getproxies().get(proxy_scheme)


def sanitized_proxy(proxy_url: str | None) -> dict[str, Any]:
    if not proxy_url:
        return {"configured": False, "host": None, "port": None,
                "source": "SYSTEM_OR_ENVIRONMENT_PROXY_POLICY"}
    parsed = urlparse(proxy_url)
    return {"configured": True, "host": parsed.hostname,
            "port": parsed.port, "source": "SYSTEM_OR_ENVIRONMENT_PROXY_POLICY"}


def probe_proxy(proxy_url: str | None, timeout_seconds: float = 0.25) -> str:
    """Low-cost TCP dependency probe; never changes the requested route."""
    if not proxy_url:
        return "PROXY_NOT_CONFIGURED"
    parsed = urlparse(proxy_url)
    if not parsed.hostname or not parsed.port:
        return "PROXY_UNREACHABLE"
    try:
        with socket.create_connection((parsed.hostname, parsed.port),
                                      timeout=timeout_seconds):
            return "PROXY_HEALTHY"
    except OSError:
        return "PROXY_UNREACHABLE"


def classify_network_error(error: BaseException | str) -> dict[str, str]:
    text = repr(error) if isinstance(error, BaseException) else str(error)
    lowered = text.lower()
    exception_class = (type(error).__name__ if isinstance(error, BaseException)
                       else text.split("(", 1)[0] or "UnknownError")
    if "connectionrefusederror" in lowered or "winerror 10061" in lowered:
        reason = "PROXY_CONNECTION_REFUSED"
    elif "proxyerror" in lowered and ("timeout" in lowered or "timed out" in lowered):
        reason = "PROXY_TIMEOUT"
    elif "proxyerror" in lowered:
        reason = "PROXY_CONNECTION_REFUSED"
    elif "ping timeout" in lowered:
        reason = "PING_TIMEOUT"
    elif "connectionclosed" in lowered:
        reason = "REMOTE_CLOSE"
    elif "gaierror" in lowered or "name or service" in lowered:
        reason = "DNS"
    elif any(token in lowered for token in ("ssl", "tls", "certificate")):
        reason = "TLS"
    elif "timeout" in lowered:
        reason = "PROXY_TIMEOUT"
    else:
        reason = "OTHER"
    return {"exception_class": exception_class, "reason": reason}


def is_receive_stale(last_receive_ms: int, now_ms: int,
                     threshold_ms: int) -> bool:
    """Watchdog invariant: only local receive activity determines staleness."""
    return now_ms - last_receive_ms > threshold_ms


@dataclass(frozen=True)
class RestartStormSnapshot:
    state: str
    restart_count_in_window: int
    threshold: int
    window_seconds: float


class RestartStormDetector:
    def __init__(self, threshold: int = 5, window_seconds: float = 300.0) -> None:
        self.threshold = int(threshold)
        self.window_seconds = float(window_seconds)
        self._timestamps: deque[float] = deque()

    def record(self, timestamp: float | None = None) -> RestartStormSnapshot:
        now = time.time() if timestamp is None else float(timestamp)
        self._timestamps.append(now)
        return self.snapshot(now)

    def snapshot(self, timestamp: float | None = None) -> RestartStormSnapshot:
        now = time.time() if timestamp is None else float(timestamp)
        floor = now - self.window_seconds
        while self._timestamps and self._timestamps[0] < floor:
            self._timestamps.popleft()
        count = len(self._timestamps)
        return RestartStormSnapshot(
            state="RESTART_STORM_WARNING" if count >= self.threshold else "NORMAL",
            restart_count_in_window=count,
            threshold=self.threshold,
            window_seconds=self.window_seconds,
        )


class ProxyHealthMonitor:
    """Rate-limited proxy probe with flapping state."""
    def __init__(self, url: str, interval_seconds: float = 30.0) -> None:
        self.url = url
        self.interval_seconds = float(interval_seconds)
        self.proxy_url = proxy_for_url(url)
        self._last_probe_at = 0.0
        self._last_state: str | None = None
        self._transitions: deque[float] = deque()

    def snapshot(self, now: float | None = None, force: bool = False) -> dict[str, Any]:
        current = time.time() if now is None else float(now)
        if force or current - self._last_probe_at >= self.interval_seconds:
            observed = probe_proxy(self.proxy_url)
            if self._last_state is not None and observed != self._last_state:
                self._transitions.append(current)
            self._last_state = observed
            self._last_probe_at = current
        floor = current - 300.0
        while self._transitions and self._transitions[0] < floor:
            self._transitions.popleft()
        state = self._last_state or "PROXY_NOT_CHECKED"
        if len(self._transitions) >= 3:
            state = "PROXY_FLAPPING"
        return {**sanitized_proxy(self.proxy_url), "state": state,
                "transitions_in_5m": len(self._transitions),
                "checked_at_epoch_seconds": self._last_probe_at or None}
