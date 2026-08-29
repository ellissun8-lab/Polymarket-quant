"""Pure market identifier resolution for execution boundaries.

This module deliberately:
- performs no network access;
- does not call Gamma;
- does not depend on CloddsBot;
- does not mutate collector state;
- fails closed on ambiguous or inconsistent mappings.

The caller supplies already-discovered market metadata.
"""

from __future__ import annotations

from collections.abc import Iterable


class MarketIdentifierError(ValueError):
    """Raised when an execution identifier cannot be resolved safely."""


def resolve_token_id(
    *,
    intent_condition_id: str,
    intent_outcome: str,
    market_condition_id: str,
    tokens: Iterable[tuple[str, str]],
) -> str:
    """Resolve an execution token_id from an already-known market mapping.

    Matching is exact. No case-folding, fuzzy matching, or fallback guessing
    is allowed at the execution boundary.
    """

    _require_nonempty_string(
        intent_condition_id,
        "intent_condition_id",
    )
    _require_nonempty_string(
        intent_outcome,
        "intent_outcome",
    )
    _require_nonempty_string(
        market_condition_id,
        "market_condition_id",
    )

    if intent_condition_id != market_condition_id:
        raise MarketIdentifierError(
            "intent condition_id does not match market condition_id"
        )

    pairs = list(tokens)

    if not pairs:
        raise MarketIdentifierError(
            "market token mapping is empty"
        )

    outcome_to_token: dict[str, str] = {}
    seen_token_ids: set[str] = set()

    for pair in pairs:
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            raise MarketIdentifierError(
                "each token mapping must be (token_id, outcome)"
            )

        token_id, outcome = pair

        _require_nonempty_string(
            token_id,
            "token_id",
        )
        _require_nonempty_string(
            outcome,
            "outcome",
        )

        if outcome in outcome_to_token:
            raise MarketIdentifierError(
                f"duplicate outcome mapping: {outcome}"
            )

        if token_id in seen_token_ids:
            raise MarketIdentifierError(
                f"duplicate token_id mapping: {token_id}"
            )

        outcome_to_token[outcome] = token_id
        seen_token_ids.add(token_id)

    if intent_outcome not in outcome_to_token:
        raise MarketIdentifierError(
            f"outcome not found in market mapping: {intent_outcome}"
        )

    return outcome_to_token[intent_outcome]


def _require_nonempty_string(
    value: object,
    name: str,
) -> None:
    if not isinstance(value, str) or not value.strip():
        raise MarketIdentifierError(
            f"{name} must be a non-empty string"
        )
