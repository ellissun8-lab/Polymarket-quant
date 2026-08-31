import pytest

from std0_quant.execution.contracts import OrderIntent
from std0_quant.execution.portfolio import PortfolioState
from std0_quant.execution.risk import RiskContext, RiskDecision, RiskLimits
from std0_quant.execution.strategy_candidate import (
    StrategyOrderCandidate,
    assess_strategy_candidate,
    build_order_intent,
    portfolio_state_hash,
    risk_context_hash,
    risk_limits_hash,
    strategy_candidate_hash,
)


def candidate():
    return StrategyOrderCandidate(
        candidate_id="candidate-1",
        alpha_id="alpha-1",
        alpha_version="1",
        risk_policy_version="risk-v1",
        condition_id="condition-1",
        outcome="Up",
        side="BUY",
        qty=10,
        limit_price=0.5,
        time_in_force="GTC",
        decision_ts_ms=1001,
        market_data_ts_ms=1000,
    )


def portfolio(cash=100):
    return PortfolioState(cash=cash)


def limits(max_order_notional=100):
    return RiskLimits(
        max_order_notional=max_order_notional,
        max_market_exposure=1000,
        max_gross_exposure=1000,
        max_daily_loss=1000,
        max_market_data_age_ms=1000,
    )


def context(kill_switch=False):
    return RiskContext(
        now_ts_ms=1001,
        market_data_ts_ms=1000,
        kill_switch=kill_switch,
    )


def test_allow_assessment_builds_identity_preserving_order_intent():
    c = candidate()
    p = portfolio()
    l = limits()
    ctx = context()

    assessment = assess_strategy_candidate(
        c,
        portfolio=p,
        limits=l,
        context=ctx,
    )

    assert assessment.risk.decision == RiskDecision.ALLOW
    assert assessment.candidate_hash == strategy_candidate_hash(c)
    assert assessment.portfolio_hash == portfolio_state_hash(p)
    assert assessment.risk_limits_hash == risk_limits_hash(l)
    assert assessment.risk_context_hash == risk_context_hash(ctx)

    intent = build_order_intent(
        c,
        assessment,
        portfolio=p,
        limits=l,
        context=ctx,
    )

    assert isinstance(intent, OrderIntent)
    assert intent.intent_id == c.candidate_id
    assert intent.strategy_id == c.alpha_id
    assert intent.strategy_version == c.alpha_version
    assert intent.risk_policy_version == c.risk_policy_version


def test_rejected_risk_cannot_build_order_intent():
    c = candidate()
    p = portfolio()
    l = limits()
    ctx = context(kill_switch=True)

    assessment = assess_strategy_candidate(
        c,
        portfolio=p,
        limits=l,
        context=ctx,
    )

    assert assessment.risk.decision == RiskDecision.REJECT

    with pytest.raises(ValueError, match="ALLOW"):
        build_order_intent(
            c,
            assessment,
            portfolio=p,
            limits=l,
            context=ctx,
        )


@pytest.mark.parametrize("changed", ["portfolio", "limits", "context"])
def test_order_intent_fails_closed_if_risk_provenance_changes(changed):
    c = candidate()
    p = portfolio()
    l = limits()
    ctx = context()

    assessment = assess_strategy_candidate(
        c,
        portfolio=p,
        limits=l,
        context=ctx,
    )

    if changed == "portfolio":
        p = portfolio(cash=99)
    elif changed == "limits":
        l = limits(max_order_notional=99)
    else:
        ctx = RiskContext(
            now_ts_ms=1001,
            market_data_ts_ms=1000,
            estimated_fee_cost=0.01,
        )

    with pytest.raises(ValueError, match="mismatch"):
        build_order_intent(
            c,
            assessment,
            portfolio=p,
            limits=l,
            context=ctx,
        )


def test_assessment_requires_candidate_timestamp_context():
    c = candidate()

    with pytest.raises(ValueError, match="now_ts_ms"):
        assess_strategy_candidate(
            c,
            portfolio=portfolio(),
            limits=limits(),
            context=RiskContext(
                now_ts_ms=1002,
                market_data_ts_ms=1000,
            ),
        )
