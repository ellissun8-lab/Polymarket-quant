from __future__ import annotations

import asyncio
import math
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from statistics import median
from typing import Any, Callable


GAMMA_DISCOVERY_ISOLATION_FIX_VERSION = "gamma_discovery_isolation_fix_v1"


@dataclass(frozen=True)
class GammaDiscoveryResult:
    status: str
    generation: int
    target_ms: int
    value: Any = None
    error: Exception | None = None
    duration_ms: float = 0.0


class GammaDiscoveryControlPlaneError(RuntimeError):
    def __init__(self, message: str, *, context: dict[str, Any]) -> None:
        super().__init__(message)
        self.context = context


class GammaDiscoveryWorker:
    """Serialize blocking Gamma discovery away from the asyncio data plane."""

    def __init__(self, journal: Any = None, *, max_workers: int = 1) -> None:
        if max_workers != 1:
            raise ValueError("Gamma discovery must use exactly one bounded worker")
        self.max_workers = max_workers
        self._journal = journal
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="gamma-discovery",
        )
        self._lock = asyncio.Lock()
        self._target_ms: int | None = None
        self._generation = 0
        self._applied_targets: set[int] = set()
        self._closed = False

    def _emit(self, event: str, **details: Any) -> None:
        if self._journal is not None:
            self._journal.emit(event, **details)

    @staticmethod
    def _invoke(call: Callable[[], Any]) -> tuple[Any, Exception | None, float]:
        started = time.perf_counter()
        try:
            return call(), None, (time.perf_counter() - started) * 1_000.0
        except Exception as exc:  # containment boundary for the recorder data plane
            return None, exc, (time.perf_counter() - started) * 1_000.0

    def _result(
        self,
        status: str,
        generation: int,
        target_ms: int,
        *,
        value: Any = None,
        error: Exception | None = None,
        duration_ms: float = 0.0,
    ) -> GammaDiscoveryResult:
        return GammaDiscoveryResult(
            status=status,
            generation=generation,
            target_ms=target_ms,
            value=value,
            error=error,
            duration_ms=duration_ms,
        )

    async def discover(
        self,
        target_ms: int,
        call: Callable[[], Any],
    ) -> GammaDiscoveryResult:
        if self._closed:
            raise RuntimeError("Gamma discovery worker is closed")

        # This mutation occurs before the first await, so a newer target immediately
        # invalidates any older in-flight or queued result.
        if target_ms != self._target_ms:
            self._target_ms = target_ms
            self._generation += 1
            # Exactly-once state is only needed for the current target; keep
            # this control-plane bookkeeping bounded across an indefinite run.
            self._applied_targets.intersection_update({target_ms})
        generation = self._generation

        async with self._lock:
            if generation != self._generation or target_ms != self._target_ms:
                self._emit(
                    "gamma_discovery_stale_result_discarded",
                    target_ms=target_ms,
                    generation=generation,
                    current_target_ms=self._target_ms,
                    current_generation=self._generation,
                    stage="before_submit",
                )
                return self._result(
                    "STALE_DISCOVERY_DISCARDED", generation, target_ms
                )

            if target_ms in self._applied_targets:
                self._emit(
                    "gamma_discovery_duplicate_result_suppressed",
                    target_ms=target_ms,
                    generation=generation,
                )
                return self._result(
                    "DUPLICATE_RESULT_SUPPRESSED", generation, target_ms
                )

            loop = asyncio.get_running_loop()
            value, error, duration_ms = await loop.run_in_executor(
                self._executor, self._invoke, call
            )

            if generation != self._generation or target_ms != self._target_ms:
                self._emit(
                    "gamma_discovery_stale_result_discarded",
                    target_ms=target_ms,
                    generation=generation,
                    current_target_ms=self._target_ms,
                    current_generation=self._generation,
                    stage="after_completion",
                    duration_ms=duration_ms,
                )
                return self._result(
                    "STALE_DISCOVERY_DISCARDED",
                    generation,
                    target_ms,
                    value=value,
                    error=error,
                    duration_ms=duration_ms,
                )

            if error is not None:
                self._emit(
                    "gamma_discovery_worker_error",
                    target_ms=target_ms,
                    generation=generation,
                    duration_ms=duration_ms,
                    error=repr(error),
                )
                return self._result(
                    "FAILED",
                    generation,
                    target_ms,
                    error=error,
                    duration_ms=duration_ms,
                )

            if value is None:
                return self._result(
                    "NO_RESULT", generation, target_ms, duration_ms=duration_ms
                )

            value_target_ms = getattr(value, "market_start_ms", target_ms)
            if value_target_ms != target_ms:
                self._emit(
                    "gamma_discovery_target_mismatch_discarded",
                    target_ms=target_ms,
                    result_target_ms=value_target_ms,
                    generation=generation,
                    duration_ms=duration_ms,
                )
                return self._result(
                    "TARGET_MISMATCH_DISCARDED",
                    generation,
                    target_ms,
                    value=value,
                    duration_ms=duration_ms,
                )

            self._applied_targets.add(target_ms)
            self._emit(
                "gamma_discovery_result_applied",
                target_ms=target_ms,
                generation=generation,
                duration_ms=duration_ms,
            )
            return self._result(
                "APPLIED",
                generation,
                target_ms,
                value=value,
                duration_ms=duration_ms,
            )

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._executor.shutdown(wait=False, cancel_futures=True)


class EventLoopLagTracker:
    def __init__(self, *, interval_seconds: float = 0.1, max_samples: int = 36_000) -> None:
        self.interval_seconds = interval_seconds
        self._samples_ms: deque[float] = deque(maxlen=max_samples)

    async def run(self, stop: asyncio.Event) -> None:
        loop = asyncio.get_running_loop()
        expected = loop.time() + self.interval_seconds
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                now = loop.time()
                self._samples_ms.append(max(0.0, (now - expected) * 1_000.0))
                expected = now + self.interval_seconds

    @staticmethod
    def _percentile(values: list[float], percentile: float) -> float:
        if not values:
            return 0.0
        rank = max(0, math.ceil(percentile * len(values)) - 1)
        return values[rank]

    def snapshot(self) -> dict[str, float | int]:
        values = sorted(self._samples_ms)
        return {
            "count": len(values),
            "p50": median(values) if values else 0.0,
            "p95": self._percentile(values, 0.95),
            "p99": self._percentile(values, 0.99),
            "max": values[-1] if values else 0.0,
        }
