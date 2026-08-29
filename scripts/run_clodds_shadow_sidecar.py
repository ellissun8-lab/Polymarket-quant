#!/usr/bin/env python3
"""Reference JSONL SHADOW sidecar.

stdin:
    one JSON object per line

stdout:
    one JSON response per non-empty input line

This process has no live execution capability.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from std0_quant.execution.clodds_shadow_protocol import (  # noqa: E402
    CLODDS_SHADOW_PROTOCOL_V1,
    CloddsShadowProtocolError,
    make_shadow_ack,
)


def emit(payload: dict) -> None:
    sys.stdout.write(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    sys.stdout.flush()


def main() -> int:
    for raw_line in sys.stdin:
        line = raw_line.strip()

        if not line:
            continue

        try:
            payload = json.loads(line)

            response = make_shadow_ack(
                payload=payload,
                receive_ts_ms=time.time_ns() // 1_000_000,
            )

        except (
            json.JSONDecodeError,
            CloddsShadowProtocolError,
            TypeError,
            ValueError,
        ) as exc:
            response = {
                "protocol_version": CLODDS_SHADOW_PROTOCOL_V1,
                "mode": "SHADOW",
                "error": {
                    "type": "PROTOCOL_REJECT",
                    "message": str(exc),
                },
            }

        emit(response)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
