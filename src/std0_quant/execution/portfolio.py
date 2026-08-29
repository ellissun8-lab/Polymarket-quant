"""Deterministic portfolio state v1.

Research/simulation only. No live order submission.

Scope:
- cash;
- reserved cash for outstanding BUY orders;
- long prediction-market share positions;
- realized fee/rebate impact through actual fills;
- gross position cost exposure;
- explicit fail-closed accounting invariants.

No leverage, borrowing, short inventory, or implicit negative positions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math


_EPS = 1e-12


@dataclass
class Position:
    condition_id: str
    outcome: str
    qty: float = 0.0
    cost_basis: float = 0.0

    @property
    def average_cost(self) -> float | None:
        if self.qty <= _EPS:
            return None
        return self.cost_basis / self.qty


@dataclass
class PortfolioState:
    cash: float
    reserved_cash: float = 0.0
    realized_pnl: float = 0.0
    positions: dict[tuple[str, str], Position] = field(
        default_factory=dict
    )
    reserved_positions: dict[tuple[str, str], float] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        self.cash = _nonnegative(
            self.cash,
            "cash",
        )
        self.reserved_cash = _nonnegative(
            self.reserved_cash,
            "reserved_cash",
        )
        self.realized_pnl = _finite(
            self.realized_pnl,
            "realized_pnl",
        )

        if self.reserved_cash > self.cash + _EPS:
            raise ValueError(
                "reserved_cash cannot exceed cash"
            )

    @property
    def available_cash(self) -> float:
        return max(
            0.0,
            self.cash - self.reserved_cash,
        )

    @property
    def gross_cost_exposure(self) -> float:
        return sum(
            position.cost_basis
            for position in self.positions.values()
        )

    def reserved_position_qty(
        self,
        condition_id: str,
        outcome: str,
    ) -> float:
        key = _key(condition_id, outcome)
        return self.reserved_positions.get(key, 0.0)

    def available_position_qty(
        self,
        condition_id: str,
        outcome: str,
    ) -> float:
        key = _key(condition_id, outcome)
        position = self.positions.get(key)

        held = (
            position.qty
            if position is not None
            else 0.0
        )

        reserved = self.reserved_positions.get(
            key,
            0.0,
        )

        return max(0.0, held - reserved)

    def reserve_sell_qty(
        self,
        condition_id: str,
        outcome: str,
        qty: float,
    ) -> None:
        qty = _positive(qty, "qty")
        key = _key(condition_id, outcome)

        if (
            qty
            > self.available_position_qty(
                condition_id,
                outcome,
            )
            + _EPS
        ):
            raise ValueError(
                "insufficient available position"
            )

        self.reserved_positions[key] = (
            self.reserved_positions.get(key, 0.0)
            + qty
        )

    def release_reserved_sell_qty(
        self,
        condition_id: str,
        outcome: str,
        qty: float,
    ) -> None:
        qty = _positive(qty, "qty")
        key = _key(condition_id, outcome)

        reserved = self.reserved_positions.get(
            key,
            0.0,
        )

        if qty > reserved + _EPS:
            raise ValueError(
                "cannot release more than reserved position"
            )

        remaining = reserved - qty

        if remaining <= _EPS:
            self.reserved_positions.pop(
                key,
                None,
            )
        else:
            self.reserved_positions[key] = remaining

    def position(
        self,
        condition_id: str,
        outcome: str,
    ) -> Position:
        key = _key(condition_id, outcome)

        if key not in self.positions:
            self.positions[key] = Position(
                condition_id=key[0],
                outcome=key[1],
            )

        return self.positions[key]

    def reserve_buy_cash(
        self,
        amount: float,
    ) -> None:
        amount = _positive(
            amount,
            "amount",
        )

        if amount > self.available_cash + _EPS:
            raise ValueError(
                "insufficient available cash"
            )

        self.reserved_cash += amount
        self._normalize()

    def release_reserved_cash(
        self,
        amount: float,
    ) -> None:
        amount = _positive(
            amount,
            "amount",
        )

        if amount > self.reserved_cash + _EPS:
            raise ValueError(
                "cannot release more than reserved cash"
            )

        self.reserved_cash -= amount
        self._normalize()

    def apply_buy_fill(
        self,
        *,
        condition_id: str,
        outcome: str,
        qty: float,
        price: float,
        fee_cost: float = 0.0,
        rebate_credit: float = 0.0,
        consume_reserved_cash: bool = False,
    ) -> None:
        qty = _positive(qty, "qty")
        price = _positive(price, "price")
        fee_cost = _nonnegative(
            fee_cost,
            "fee_cost",
        )
        rebate_credit = _nonnegative(
            rebate_credit,
            "rebate_credit",
        )

        gross = qty * price
        net_cash_cost = gross + fee_cost - rebate_credit

        if net_cash_cost < -_EPS:
            raise ValueError(
                "rebate cannot create negative buy cash cost"
            )

        if consume_reserved_cash:
            reserved_used = min(
                self.reserved_cash,
                net_cash_cost,
            )
            self.reserved_cash -= reserved_used

        if net_cash_cost > self.cash + _EPS:
            raise ValueError(
                "insufficient cash for buy fill"
            )

        self.cash -= net_cash_cost

        position = self.position(
            condition_id,
            outcome,
        )
        position.qty += qty
        position.cost_basis += gross

        self.realized_pnl -= fee_cost
        self.realized_pnl += rebate_credit

        self._normalize_position(position)
        self._normalize()

    def apply_sell_fill(
        self,
        *,
        condition_id: str,
        outcome: str,
        qty: float,
        price: float,
        fee_cost: float = 0.0,
        rebate_credit: float = 0.0,
    ) -> None:
        qty = _positive(qty, "qty")
        price = _positive(price, "price")
        fee_cost = _nonnegative(
            fee_cost,
            "fee_cost",
        )
        rebate_credit = _nonnegative(
            rebate_credit,
            "rebate_credit",
        )

        position = self.position(
            condition_id,
            outcome,
        )

        if qty > position.qty + _EPS:
            raise ValueError(
                "cannot sell more shares than held"
            )

        average_cost = position.average_cost
        if average_cost is None:
            raise ValueError(
                "cannot sell empty position"
            )

        removed_cost = average_cost * qty
        gross_proceeds = price * qty
        net_proceeds = (
            gross_proceeds
            - fee_cost
            + rebate_credit
        )

        self.cash += net_proceeds

        position.qty -= qty
        position.cost_basis -= removed_cost

        self.realized_pnl += (
            gross_proceeds
            - removed_cost
            - fee_cost
            + rebate_credit
        )

        self._normalize_position(position)
        self._normalize()

    def _normalize_position(
        self,
        position: Position,
    ) -> None:
        if abs(position.qty) <= _EPS:
            position.qty = 0.0
            position.cost_basis = 0.0

        if position.qty < -_EPS:
            raise AssertionError(
                "negative position invariant violated"
            )

        if position.cost_basis < -_EPS:
            raise AssertionError(
                "negative cost basis invariant violated"
            )

    def _normalize(self) -> None:
        if abs(self.cash) <= _EPS:
            self.cash = 0.0

        if abs(self.reserved_cash) <= _EPS:
            self.reserved_cash = 0.0

        if self.cash < -_EPS:
            raise AssertionError(
                "negative cash invariant violated"
            )

        if self.reserved_cash > self.cash + _EPS:
            raise AssertionError(
                "reserved cash exceeds cash"
            )


def _key(
    condition_id: str,
    outcome: str,
) -> tuple[str, str]:
    condition_id = str(condition_id).strip()
    outcome = str(outcome).strip()

    if not condition_id:
        raise ValueError(
            "condition_id must be non-empty"
        )

    if not outcome:
        raise ValueError(
            "outcome must be non-empty"
        )

    return condition_id, outcome


def _finite(
    value: float,
    name: str,
) -> float:
    value = float(value)

    if not math.isfinite(value):
        raise ValueError(
            f"{name} must be finite"
        )

    return value


def _nonnegative(
    value: float,
    name: str,
) -> float:
    value = _finite(value, name)

    if value < 0:
        raise ValueError(
            f"{name} must be >= 0"
        )

    return value


def _positive(
    value: float,
    name: str,
) -> float:
    value = _finite(value, name)

    if value <= 0:
        raise ValueError(
            f"{name} must be > 0"
        )

    return value
