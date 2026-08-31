"""Deterministic batch orchestration over the hard SHADOW execution boundary v1.

This module adds batching only. It does not add LIVE execution capability,
credentials, venue networking, promotion logic, or alternate execution semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Iterable, Sequence

from std0_quant.execution.clodds_mapping import (
    CLODDS_MAPPING_VERSION_V1,
)
from std0_quant.execution.clodds_shadow_client import (
    CloddsShadowClientError,
)
from std0_quant.execution.clodds_shadow_protocol import (
    AUDITED_CLODDS_COMMIT_V1,
    CLODDS_SHADOW_PROTOCOL_V1,
)
from std0_quant.execution.contracts import (
    OrderEvent,
    OrderIntent,
)
from std0_quant.execution.jsonl_process_transport import (
    JsonlProcessTransportError,
)


BATCH_SHADOW_ARTIFACT_SCHEMA_V1 = "batch_shadow_artifact_v1"
BATCH_SHADOW_RUNNER_VERSION_V1 = "batch_shadow_runner_v1"
SHADOW_MODE = "SHADOW"


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _nonempty(value: Any, name: str) -> str:
    value = str(value).strip()
    if not value:
        raise ValueError(f"{name} must be non-empty")
    return value


@dataclass(frozen=True)
class BatchShadowRequest:
    intent: OrderIntent
    market_condition_id: str
    tokens: tuple[tuple[str, str], ...]
    post_only: bool

    def __post_init__(self) -> None:
        if not isinstance(self.intent, OrderIntent):
            raise TypeError("intent must be OrderIntent")

        object.__setattr__(
            self,
            "market_condition_id",
            _nonempty(
                self.market_condition_id,
                "market_condition_id",
            ),
        )

        tokens = tuple(
            (
                _nonempty(token_id, "token_id"),
                _nonempty(outcome, "token_outcome"),
            )
            for token_id, outcome in self.tokens
        )

        if not tokens:
            raise ValueError("tokens must not be empty")

        object.__setattr__(self, "tokens", tokens)

        if not isinstance(self.post_only, bool):
            raise TypeError("post_only must be bool")

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.to_dict(),
            "market_condition_id": self.market_condition_id,
            "tokens": [
                [token_id, outcome]
                for token_id, outcome in self.tokens
            ],
            "post_only": self.post_only,
        }


@dataclass(frozen=True)
class BatchShadowItemResult:
    index: int
    intent_id: str
    condition_id: str
    status: str
    request: BatchShadowRequest
    event: OrderEvent | None
    error_type: str | None
    error_message: str | None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.index, int)
            or isinstance(self.index, bool)
            or self.index < 0
        ):
            raise ValueError("index must be a nonnegative integer")

        object.__setattr__(
            self,
            "intent_id",
            _nonempty(self.intent_id, "intent_id"),
        )
        object.__setattr__(
            self,
            "condition_id",
            _nonempty(self.condition_id, "condition_id"),
        )

        if self.status not in {"PASS", "FAIL"}:
            raise ValueError("status must be PASS or FAIL")

        if self.status == "PASS":
            if not isinstance(self.event, OrderEvent):
                raise ValueError("PASS requires event")
            if self.error_type is not None or self.error_message is not None:
                raise ValueError("PASS cannot contain error fields")
        else:
            if self.event is not None:
                raise ValueError("FAIL cannot contain event")
            object.__setattr__(
                self,
                "error_type",
                _nonempty(self.error_type, "error_type"),
            )
            object.__setattr__(
                self,
                "error_message",
                _nonempty(self.error_message, "error_message"),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "intent_id": self.intent_id,
            "condition_id": self.condition_id,
            "status": self.status,
            "request": self.request.to_dict(),
            "event": (
                self.event.to_dict()
                if self.event is not None
                else None
            ),
            "error_type": self.error_type,
            "error_message": self.error_message,
        }


@dataclass(frozen=True)
class BatchShadowArtifact:
    run_id: str
    n_total: int
    n_pass: int
    n_fail: int
    items: tuple[BatchShadowItemResult, ...]
    artifact_hash: str
    runner_version: str = BATCH_SHADOW_RUNNER_VERSION_V1
    mode: str = SHADOW_MODE
    protocol_version: str = CLODDS_SHADOW_PROTOCOL_V1
    clodds_commit: str = AUDITED_CLODDS_COMMIT_V1
    mapping_version: str = CLODDS_MAPPING_VERSION_V1
    schema_version: str = BATCH_SHADOW_ARTIFACT_SCHEMA_V1

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "run_id",
            _nonempty(self.run_id, "run_id"),
        )
        object.__setattr__(self, "items", tuple(self.items))
        object.__setattr__(
            self,
            "artifact_hash",
            _nonempty(self.artifact_hash, "artifact_hash"),
        )

        if self.n_total != len(self.items):
            raise ValueError("n_total does not match items")
        if self.n_pass + self.n_fail != self.n_total:
            raise ValueError("PASS/FAIL counts do not sum to n_total")
        if self.n_pass != sum(
            item.status == "PASS"
            for item in self.items
        ):
            raise ValueError("n_pass does not match items")
        if self.n_fail != sum(
            item.status == "FAIL"
            for item in self.items
        ):
            raise ValueError("n_fail does not match items")

        if self.runner_version != BATCH_SHADOW_RUNNER_VERSION_V1:
            raise ValueError("unsupported runner_version")
        if self.mode != SHADOW_MODE:
            raise ValueError("non-SHADOW mode refused")
        if self.protocol_version != CLODDS_SHADOW_PROTOCOL_V1:
            raise ValueError("shadow protocol mismatch")
        if self.clodds_commit != AUDITED_CLODDS_COMMIT_V1:
            raise ValueError("Clodds commit mismatch")
        if self.mapping_version != CLODDS_MAPPING_VERSION_V1:
            raise ValueError("mapping version mismatch")
        if self.schema_version != BATCH_SHADOW_ARTIFACT_SCHEMA_V1:
            raise ValueError("unsupported schema_version")

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "n_total": self.n_total,
            "n_pass": self.n_pass,
            "n_fail": self.n_fail,
            "items": [
                item.to_dict()
                for item in self.items
            ],
            "artifact_hash": self.artifact_hash,
            "runner_version": self.runner_version,
            "mode": self.mode,
            "protocol_version": self.protocol_version,
            "clodds_commit": self.clodds_commit,
            "mapping_version": self.mapping_version,
            "schema_version": self.schema_version,
        }


def _artifact_payload(
    artifact: BatchShadowArtifact,
) -> dict[str, Any]:
    return {
        "n_total": artifact.n_total,
        "n_pass": artifact.n_pass,
        "n_fail": artifact.n_fail,
        "items": [
            item.to_dict()
            for item in artifact.items
        ],
        "runner_version": artifact.runner_version,
        "mode": artifact.mode,
        "protocol_version": artifact.protocol_version,
        "clodds_commit": artifact.clodds_commit,
        "mapping_version": artifact.mapping_version,
        "schema_version": artifact.schema_version,
    }


def batch_shadow_artifact_hash(
    artifact: BatchShadowArtifact,
) -> str:
    return hashlib.sha256(
        _canonical_json(
            _artifact_payload(artifact)
        ).encode("utf-8")
    ).hexdigest()


def _build_artifact(
    *,
    run_id: str,
    items: Sequence[BatchShadowItemResult],
) -> BatchShadowArtifact:
    items = tuple(items)
    n_pass = sum(
        item.status == "PASS"
        for item in items
    )
    n_fail = len(items) - n_pass

    provisional = BatchShadowArtifact(
        run_id=run_id,
        n_total=len(items),
        n_pass=n_pass,
        n_fail=n_fail,
        items=items,
        artifact_hash="PENDING",
    )

    return BatchShadowArtifact(
        run_id=run_id,
        n_total=provisional.n_total,
        n_pass=provisional.n_pass,
        n_fail=provisional.n_fail,
        items=provisional.items,
        artifact_hash=batch_shadow_artifact_hash(
            provisional
        ),
    )


def run_shadow_batch(
    requests: Iterable[BatchShadowRequest],
    *,
    client: Any,
    run_id: str,
) -> BatchShadowArtifact:
    run_id = _nonempty(run_id, "run_id")
    requests = tuple(requests)

    for request in requests:
        if not isinstance(request, BatchShadowRequest):
            raise TypeError(
                "all requests must be BatchShadowRequest"
            )

    intent_ids = [
        request.intent.intent_id
        for request in requests
    ]

    if len(intent_ids) != len(set(intent_ids)):
        raise ValueError("duplicate intent_id in batch")

    items: list[BatchShadowItemResult] = []

    for index, request in enumerate(requests):
        intent = request.intent

        try:
            event = client.submit(
                intent=intent,
                market_condition_id=(
                    request.market_condition_id
                ),
                tokens=request.tokens,
                post_only=request.post_only,
            )
        except (
            ValueError,
            CloddsShadowClientError,
            JsonlProcessTransportError,
        ) as exc:
            items.append(
                BatchShadowItemResult(
                    index=index,
                    intent_id=intent.intent_id,
                    condition_id=intent.condition_id,
                    status="FAIL",
                    request=request,
                    event=None,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
            )
            continue

        if not isinstance(event, OrderEvent):
            raise TypeError(
                "shadow client must return OrderEvent"
            )

        items.append(
            BatchShadowItemResult(
                index=index,
                intent_id=intent.intent_id,
                condition_id=intent.condition_id,
                status="PASS",
                request=request,
                event=event,
                error_type=None,
                error_message=None,
            )
        )

    return _build_artifact(
        run_id=run_id,
        items=items,
    )
