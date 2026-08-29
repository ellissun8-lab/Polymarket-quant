"""Versioned execution boundary contracts v1.

These contracts form the deterministic boundary between std0-quant
decision/risk code and an external execution adapter.

Research/shadow only at this stage. No live order submission.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import json
import math
from typing import Any


ORDER_INTENT_SCHEMA_V1 = "order_intent_v1"
ORDER_EVENT_SCHEMA_V1 = "order_event_v1"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class TimeInForce(str, Enum):
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"


class OrderEventType(str, Enum):
    SUBMITTED = "SUBMITTED"
    VENUE_ACK = "VENUE_ACK"
    PARTIAL_FILL = "PARTIAL_FILL"
    FILLED = "FILLED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class OrderIntent:
    """Auditable instruction crossing into the execution adapter."""

    intent_id: str
    condition_id: str
    outcome: str
    side: OrderSide | str
    qty: float
    limit_price: float
    time_in_force: TimeInForce | str
    decision_ts_ms: float
    market_data_ts_ms: float
    strategy_id: str
    strategy_version: str
    risk_policy_version: str
    schema_version: str = ORDER_INTENT_SCHEMA_V1

    def __post_init__(self) -> None:
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
        object.__setattr__(
            self,
            "outcome",
            _nonempty(self.outcome, "outcome"),
        )
        object.__setattr__(
            self,
            "strategy_id",
            _nonempty(self.strategy_id, "strategy_id"),
        )
        object.__setattr__(
            self,
            "strategy_version",
            _nonempty(
                self.strategy_version,
                "strategy_version",
            ),
        )
        object.__setattr__(
            self,
            "risk_policy_version",
            _nonempty(
                self.risk_policy_version,
                "risk_policy_version",
            ),
        )

        try:
            side = OrderSide(self.side)
        except ValueError as exc:
            raise ValueError(
                "side must be BUY or SELL"
            ) from exc

        try:
            tif = TimeInForce(self.time_in_force)
        except ValueError as exc:
            raise ValueError(
                "time_in_force must be GTC, IOC or FOK"
            ) from exc

        object.__setattr__(self, "side", side)
        object.__setattr__(self, "time_in_force", tif)

        object.__setattr__(
            self,
            "qty",
            _positive(self.qty, "qty"),
        )
        object.__setattr__(
            self,
            "limit_price",
            _positive(self.limit_price, "limit_price"),
        )
        object.__setattr__(
            self,
            "decision_ts_ms",
            _nonnegative(
                self.decision_ts_ms,
                "decision_ts_ms",
            ),
        )
        object.__setattr__(
            self,
            "market_data_ts_ms",
            _nonnegative(
                self.market_data_ts_ms,
                "market_data_ts_ms",
            ),
        )

        if (
            self.market_data_ts_ms
            > self.decision_ts_ms
        ):
            raise ValueError(
                "market_data_ts_ms cannot be after decision_ts_ms"
            )

        if self.schema_version != ORDER_INTENT_SCHEMA_V1:
            raise ValueError(
                "unsupported OrderIntent schema_version"
            )

    @property
    def order_notional(self) -> float:
        return self.qty * self.limit_price

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["side"] = self.side.value
        row["time_in_force"] = self.time_in_force.value
        return row

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def from_dict(
        cls,
        row: dict[str, Any],
    ) -> "OrderIntent":
        return cls(**row)

    @classmethod
    def from_json(
        cls,
        payload: str,
    ) -> "OrderIntent":
        return cls.from_dict(json.loads(payload))


@dataclass(frozen=True)
class OrderEvent:
    """Auditable response emitted by an execution adapter."""

    event_id: str
    intent_id: str
    event_type: OrderEventType | str
    receive_ts_ms: float
    venue_ts_ms: float | None = None
    venue_order_id: str | None = None
    fill_qty: float = 0.0
    fill_price: float | None = None
    cumulative_filled_qty: float = 0.0
    remaining_qty: float | None = None
    reason: str | None = None
    schema_version: str = ORDER_EVENT_SCHEMA_V1

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "event_id",
            _nonempty(self.event_id, "event_id"),
        )
        object.__setattr__(
            self,
            "intent_id",
            _nonempty(self.intent_id, "intent_id"),
        )

        try:
            event_type = OrderEventType(
                self.event_type
            )
        except ValueError as exc:
            raise ValueError(
                "unsupported order event type"
            ) from exc

        object.__setattr__(
            self,
            "event_type",
            event_type,
        )

        object.__setattr__(
            self,
            "receive_ts_ms",
            _nonnegative(
                self.receive_ts_ms,
                "receive_ts_ms",
            ),
        )

        if self.venue_ts_ms is not None:
            object.__setattr__(
                self,
                "venue_ts_ms",
                _nonnegative(
                    self.venue_ts_ms,
                    "venue_ts_ms",
                ),
            )

        object.__setattr__(
            self,
            "fill_qty",
            _nonnegative(
                self.fill_qty,
                "fill_qty",
            ),
        )

        if self.fill_price is not None:
            object.__setattr__(
                self,
                "fill_price",
                _positive(
                    self.fill_price,
                    "fill_price",
                ),
            )

        object.__setattr__(
            self,
            "cumulative_filled_qty",
            _nonnegative(
                self.cumulative_filled_qty,
                "cumulative_filled_qty",
            ),
        )

        if self.remaining_qty is not None:
            object.__setattr__(
                self,
                "remaining_qty",
                _nonnegative(
                    self.remaining_qty,
                    "remaining_qty",
                ),
            )

        fill_event = event_type in {
            OrderEventType.PARTIAL_FILL,
            OrderEventType.FILLED,
        }

        if fill_event:
            if self.fill_qty <= 0:
                raise ValueError(
                    "fill event requires fill_qty > 0"
                )

            if self.fill_price is None:
                raise ValueError(
                    "fill event requires fill_price"
                )
        else:
            if self.fill_qty != 0:
                raise ValueError(
                    "non-fill event cannot contain fill_qty"
                )

            if self.fill_price is not None:
                raise ValueError(
                    "non-fill event cannot contain fill_price"
                )

        if (
            event_type == OrderEventType.FILLED
            and self.remaining_qty is not None
            and self.remaining_qty != 0
        ):
            raise ValueError(
                "FILLED event requires remaining_qty == 0"
            )

        if (
            event_type == OrderEventType.PARTIAL_FILL
            and self.remaining_qty is not None
            and self.remaining_qty <= 0
        ):
            raise ValueError(
                "PARTIAL_FILL requires remaining_qty > 0"
            )

        if self.schema_version != ORDER_EVENT_SCHEMA_V1:
            raise ValueError(
                "unsupported OrderEvent schema_version"
            )

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["event_type"] = self.event_type.value
        return row

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def from_dict(
        cls,
        row: dict[str, Any],
    ) -> "OrderEvent":
        return cls(**row)

    @classmethod
    def from_json(
        cls,
        payload: str,
    ) -> "OrderEvent":
        return cls.from_dict(json.loads(payload))


def _nonempty(value: str, name: str) -> str:
    value = str(value).strip()

    if not value:
        raise ValueError(
            f"{name} must be non-empty"
        )

    return value


def _positive(value: float, name: str) -> float:
    value = float(value)

    if not math.isfinite(value) or value <= 0:
        raise ValueError(
            f"{name} must be finite and > 0"
        )

    return value


def _nonnegative(
    value: float,
    name: str,
) -> float:
    value = float(value)

    if not math.isfinite(value) or value < 0:
        raise ValueError(
            f"{name} must be finite and >= 0"
        )

    return value
