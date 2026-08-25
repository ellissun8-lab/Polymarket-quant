"""UTC timestamp utilities.

All internal timestamps are epoch milliseconds in UTC (integers).
The only place where seconds/ISO strings are accepted is at the API boundary
(:func:`parse_ts_to_ms`), and every parse failure is surfaced as ``None`` so
callers can flag records instead of silently guessing.
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timezone

# Heuristics separating unix-seconds from unix-millis values.
_MIN_EPOCH_S = 946684800  # 2000-01-01T00:00:00Z
_MAX_EPOCH_S = 4102444800  # 2100-01-01T00:00:00Z


def utc_now_ms() -> int:
    return int(time.time() * 1000)


def ms_to_iso(ts_ms: int | float | None) -> str | None:
    if ts_ms is None:
        return None
    return datetime.fromtimestamp(int(ts_ms) / 1000.0, tz=timezone.utc).isoformat()


def iso_to_ms(value: str) -> int | None:
    try:
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except (ValueError, TypeError, OverflowError):
        return None


def _numeric_to_ms(value: float) -> int | None:
    if isinstance(value, bool) or math.isnan(value) or math.isinf(value):
        return None
    # Unix-seconds for 2000..2100, or unix-milliseconds for >= 2000.
    # Values in the gap (year >2100 as seconds / <2000 as ms) are rejected.
    if _MIN_EPOCH_S <= value < _MAX_EPOCH_S:
        return int(value * 1000)
    if value >= _MIN_EPOCH_S * 1000:
        return int(value)
    return None


def parse_ts_to_ms(value: object) -> int | None:
    """Best-effort, order-free parse of an API timestamp into epoch ms (UTC).

    Accepts: unix seconds, unix milliseconds, numeric strings of either,
    ISO-8601 strings (with or without ``Z``). Returns ``None`` when the value
    cannot be interpreted; callers must treat ``None`` as invalid data.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return _numeric_to_ms(float(value))
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return _numeric_to_ms(float(text))
        except ValueError:
            return iso_to_ms(text)
    return None


def format_duration_ms(duration_ms: int) -> str:
    seconds = duration_ms / 1000.0
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = seconds / 60.0
    if minutes < 60:
        return f"{minutes:.1f}m"
    return f"{minutes / 60.0:.1f}h"
