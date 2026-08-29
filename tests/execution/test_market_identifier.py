import pytest

from std0_quant.execution.market_identifier import (
    MarketIdentifierError,
    resolve_token_id,
)


TOKENS = [
    ("token-up", "Up"),
    ("token-down", "Down"),
]


def test_resolves_up_token():
    assert resolve_token_id(
        intent_condition_id="cond-1",
        intent_outcome="Up",
        market_condition_id="cond-1",
        tokens=TOKENS,
    ) == "token-up"


def test_resolves_down_token():
    assert resolve_token_id(
        intent_condition_id="cond-1",
        intent_outcome="Down",
        market_condition_id="cond-1",
        tokens=TOKENS,
    ) == "token-down"


def test_condition_id_mismatch_fails_closed():
    with pytest.raises(
        MarketIdentifierError,
        match="condition_id",
    ):
        resolve_token_id(
            intent_condition_id="cond-intent",
            intent_outcome="Up",
            market_condition_id="cond-market",
            tokens=TOKENS,
        )


def test_unknown_outcome_fails_closed():
    with pytest.raises(
        MarketIdentifierError,
        match="outcome not found",
    ):
        resolve_token_id(
            intent_condition_id="cond-1",
            intent_outcome="YES",
            market_condition_id="cond-1",
            tokens=TOKENS,
        )


def test_matching_is_exact_no_case_guessing():
    with pytest.raises(MarketIdentifierError):
        resolve_token_id(
            intent_condition_id="cond-1",
            intent_outcome="up",
            market_condition_id="cond-1",
            tokens=TOKENS,
        )


def test_duplicate_outcome_mapping_fails_closed():
    with pytest.raises(
        MarketIdentifierError,
        match="duplicate outcome",
    ):
        resolve_token_id(
            intent_condition_id="cond-1",
            intent_outcome="Up",
            market_condition_id="cond-1",
            tokens=[
                ("token-a", "Up"),
                ("token-b", "Up"),
            ],
        )


def test_duplicate_token_id_mapping_fails_closed():
    with pytest.raises(
        MarketIdentifierError,
        match="duplicate token_id",
    ):
        resolve_token_id(
            intent_condition_id="cond-1",
            intent_outcome="Up",
            market_condition_id="cond-1",
            tokens=[
                ("same-token", "Up"),
                ("same-token", "Down"),
            ],
        )


def test_empty_mapping_fails_closed():
    with pytest.raises(
        MarketIdentifierError,
        match="empty",
    ):
        resolve_token_id(
            intent_condition_id="cond-1",
            intent_outcome="Up",
            market_condition_id="cond-1",
            tokens=[],
        )


def test_blank_token_id_fails_closed():
    with pytest.raises(MarketIdentifierError):
        resolve_token_id(
            intent_condition_id="cond-1",
            intent_outcome="Up",
            market_condition_id="cond-1",
            tokens=[
                ("", "Up"),
                ("token-down", "Down"),
            ],
        )


def test_malformed_mapping_fails_closed():
    with pytest.raises(
        MarketIdentifierError,
        match="must be",
    ):
        resolve_token_id(
            intent_condition_id="cond-1",
            intent_outcome="Up",
            market_condition_id="cond-1",
            tokens=[("token-up",)],  # type: ignore[list-item]
        )
