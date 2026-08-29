"""Deterministic fill model v1.

Research/simulation only.  No live order submission.

Passive fills:
- only confirmed same-price traded quantity is eligible;
- FIFOQueueModel determines queue depletion and our own fill;
- our passive fill occurs at the resting order price.

Aggressive fills:
- sweep currently visible opposite-side liquidity best-to-worst;
- no hidden liquidity or future replenishment is assumed;
- insufficient visible liquidity produces an explicit partial fill.
"""

from __future__ import annotations

from dataclasses import dataclass

from std0_quant.execution.queue_model import FIFOQueueModel


_EPS = 1e-12


@dataclass(frozen=True)
class Fill:
    price: float
    qty: float
    liquidity: str

    @property
    def notional(self) -> float:
        return self.price * self.qty


@dataclass(frozen=True)
class FillResult:
    requested_qty: float
    filled_qty: float
    remaining_qty: float
    fills: tuple[Fill, ...]

    @property
    def is_fully_filled(self) -> bool:
        return self.remaining_qty <= _EPS

    @property
    def average_price(self) -> float | None:
        if self.filled_qty <= _EPS:
            return None
        return (
            sum(fill.notional for fill in self.fills)
            / self.filled_qty
        )


def passive_fill_from_confirmed_trade(
    *,
    queue: FIFOQueueModel,
    order_price: float,
    traded_qty: float,
) -> FillResult:
    """Apply confirmed same-price trade quantity to a resting order."""

    order_price = _positive_finite(order_price, "order_price")
    traded_qty = _nonnegative_finite(traded_qty, "traded_qty")

    remaining_before = queue.remaining_qty
    queue_fill = queue.on_confirmed_trade(traded_qty)

    fills: tuple[Fill, ...]
    if queue_fill.own_fill_qty > _EPS:
        fills = (
            Fill(
                price=order_price,
                qty=queue_fill.own_fill_qty,
                liquidity="maker",
            ),
        )
    else:
        fills = ()

    return FillResult(
        requested_qty=remaining_before,
        filled_qty=queue_fill.own_fill_qty,
        remaining_qty=queue.remaining_qty,
        fills=fills,
    )


def aggressive_sweep(
    *,
    side: str,
    order_qty: float,
    levels: list[tuple[float, float]],
) -> FillResult:
    """Sweep visible opposite-side book liquidity.

    BUY expects asks ordered from lowest price to highest.
    SELL expects bids ordered from highest price to lowest.

    The function deliberately fails closed if levels are not supplied in
    best-to-worst order instead of silently sorting them.
    """

    side = str(side).upper()
    if side not in {"BUY", "SELL"}:
        raise ValueError("side must be BUY or SELL")

    order_qty = _positive_finite(order_qty, "order_qty")

    checked: list[tuple[float, float]] = []
    for index, level in enumerate(levels):
        if len(level) != 2:
            raise ValueError(
                f"level {index} must be (price, qty)"
            )

        price = _positive_finite(
            level[0],
            f"levels[{index}].price",
        )
        qty = _nonnegative_finite(
            level[1],
            f"levels[{index}].qty",
        )
        checked.append((price, qty))

    _validate_best_to_worst(side, checked)

    remaining = order_qty
    fills: list[Fill] = []

    for price, available_qty in checked:
        if remaining <= _EPS:
            break

        if available_qty <= _EPS:
            continue

        qty = min(remaining, available_qty)

        fills.append(
            Fill(
                price=price,
                qty=qty,
                liquidity="taker",
            )
        )

        remaining -= qty

    if abs(remaining) <= _EPS:
        remaining = 0.0

    filled = order_qty - remaining

    return FillResult(
        requested_qty=order_qty,
        filled_qty=filled,
        remaining_qty=remaining,
        fills=tuple(fills),
    )


def _validate_best_to_worst(
    side: str,
    levels: list[tuple[float, float]],
) -> None:
    prices = [price for price, _ in levels]

    if side == "BUY":
        if any(
            later + _EPS < earlier
            for earlier, later in zip(prices, prices[1:])
        ):
            raise ValueError(
                "BUY sweep asks must be ordered low-to-high"
            )
    else:
        if any(
            later > earlier + _EPS
            for earlier, later in zip(prices, prices[1:])
        ):
            raise ValueError(
                "SELL sweep bids must be ordered high-to-low"
            )


def _positive_finite(value: float, name: str) -> float:
    value = float(value)

    if not _is_finite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and > 0")

    return value


def _nonnegative_finite(value: float, name: str) -> float:
    value = float(value)

    if not _is_finite(value) or value < 0:
        raise ValueError(f"{name} must be finite and >= 0")

    return value


def _is_finite(value: float) -> bool:
    return (
        value == value
        and value not in (float("inf"), float("-inf"))
    )
