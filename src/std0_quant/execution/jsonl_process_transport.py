"""Fail-closed JSONL subprocess transport.

This is a generic process boundary transport.

Safety properties:
- no shell=True;
- caller supplies an explicit command;
- one JSON object per request/response line;
- bounded response timeout;
- malformed/non-object responses fail closed;
- timeout terminates the child;
- no credentials or execution semantics exist in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import selectors
import subprocess
from typing import Any, Sequence


class JsonlProcessTransportError(RuntimeError):
    """Fail-closed subprocess transport error."""


@dataclass(frozen=True)
class JsonlProcessTransportConfig:
    command: tuple[str, ...]
    cwd: str | Path | None = None
    timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if not self.command:
            raise ValueError("command must not be empty")

        for part in self.command:
            if not isinstance(part, str) or not part:
                raise ValueError(
                    "command entries must be non-empty strings"
                )

        timeout = float(self.timeout_seconds)

        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError(
                "timeout_seconds must be finite and > 0"
            )

        object.__setattr__(
            self,
            "timeout_seconds",
            timeout,
        )


class JsonlProcessTransport:
    """Persistent request/response JSONL subprocess transport."""

    def __init__(
        self,
        config: JsonlProcessTransportConfig,
    ) -> None:
        self._config = config
        self._closed = False

        try:
            self._proc = subprocess.Popen(
                list(config.command),
                cwd=(
                    str(config.cwd)
                    if config.cwd is not None
                    else None
                ),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env={
                    "PYTHONUNBUFFERED": "1",
                },
            )
        except OSError as exc:
            raise JsonlProcessTransportError(
                f"failed to start sidecar: {exc}"
            ) from exc

        if (
            self._proc.stdin is None
            or self._proc.stdout is None
            or self._proc.stderr is None
        ):
            self.close()
            raise JsonlProcessTransportError(
                "sidecar pipes were not created"
            )

        self._selector = selectors.DefaultSelector()
        self._selector.register(
            self._proc.stdout,
            selectors.EVENT_READ,
        )

    def submit(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if self._closed:
            raise JsonlProcessTransportError(
                "transport is closed"
            )

        if not isinstance(payload, dict):
            raise JsonlProcessTransportError(
                "payload must be a JSON object"
            )

        if self._proc.poll() is not None:
            raise JsonlProcessTransportError(
                self._terminated_message()
            )

        try:
            encoded = json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as exc:
            raise JsonlProcessTransportError(
                f"payload is not JSON serializable: {exc}"
            ) from exc

        try:
            assert self._proc.stdin is not None
            self._proc.stdin.write(encoded + "\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self._terminate_fail_closed()
            raise JsonlProcessTransportError(
                f"failed to write sidecar request: {exc}"
            ) from exc

        ready = self._selector.select(
            timeout=self._config.timeout_seconds
        )

        if not ready:
            self._terminate_fail_closed()
            raise JsonlProcessTransportError(
                "sidecar response timeout"
            )

        assert self._proc.stdout is not None
        line = self._proc.stdout.readline()

        if line == "":
            raise JsonlProcessTransportError(
                self._terminated_message()
            )

        try:
            response = json.loads(line)
        except json.JSONDecodeError as exc:
            self._terminate_fail_closed()
            raise JsonlProcessTransportError(
                f"sidecar returned invalid JSON: {exc}"
            ) from exc

        if not isinstance(response, dict):
            self._terminate_fail_closed()
            raise JsonlProcessTransportError(
                "sidecar response must be a JSON object"
            )

        return response

    def close(self) -> None:
        if self._closed:
            return

        self._closed = True

        selector = getattr(
            self,
            "_selector",
            None,
        )

        if selector is not None:
            try:
                selector.close()
            except Exception:
                pass

        proc = getattr(
            self,
            "_proc",
            None,
        )

        if proc is None:
            return

        if proc.stdin is not None:
            try:
                proc.stdin.close()
            except OSError:
                pass

        if proc.poll() is None:
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                proc.terminate()

                try:
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=1.0)

    def _terminate_fail_closed(self) -> None:
        if self._proc.poll() is None:
            self._proc.terminate()

            try:
                self._proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=1.0)

    def _terminated_message(self) -> str:
        code = self._proc.poll()

        stderr = ""

        if code is not None and self._proc.stderr is not None:
            try:
                stderr = self._proc.stderr.read().strip()
            except OSError:
                stderr = ""

        suffix = f"; stderr={stderr}" if stderr else ""

        return (
            f"sidecar terminated"
            f" with return code {code}{suffix}"
        )

    def __enter__(self) -> "JsonlProcessTransport":
        return self

    def __exit__(
        self,
        exc_type,
        exc,
        tb,
    ) -> None:
        self.close()


def make_transport_config(
    command: Sequence[str],
    *,
    cwd: str | Path | None = None,
    timeout_seconds: float = 5.0,
) -> JsonlProcessTransportConfig:
    return JsonlProcessTransportConfig(
        command=tuple(command),
        cwd=cwd,
        timeout_seconds=timeout_seconds,
    )
