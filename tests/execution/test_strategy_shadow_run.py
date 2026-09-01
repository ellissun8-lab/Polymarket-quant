import pytest
from dataclasses import replace

from std0_quant.execution.contracts import OrderEvent, OrderEventType
from std0_quant.execution.portfolio import PortfolioState
from std0_quant.execution.risk import RiskContext, RiskLimits
from std0_quant.execution.strategy_candidate import StrategyOrderCandidate
from std0_quant.execution.strategy_shadow_run import (
    StrategyShadowRunArtifact,
    run_strategy_shadow,
    strategy_shadow_run_artifact_hash,
)


TOKENS = (
    ("token-up", "Up"),
    ("token-down", "Down"),
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


def portfolio():
    return PortfolioState(cash=100)


def limits():
    return RiskLimits(
        max_order_notional=100,
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


class FakeClient:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def submit(self, *, intent, market_condition_id, tokens, post_only):
        self.calls.append(intent.intent_id)

        if self.fail:
            raise ValueError("forced shadow failure")

        return OrderEvent(
            event_id=f"{intent.intent_id}:shadow_ack",
            intent_id=intent.intent_id,
            event_type=OrderEventType.VENUE_ACK,
            receive_ts_ms=2000,
            venue_ts_ms=None,
            venue_order_id=f"shadow:{intent.intent_id}",
            fill_qty=0.0,
            fill_price=None,
            cumulative_filled_qty=0.0,
            remaining_qty=intent.qty,
            reason="SHADOW_SYNTHETIC_ACK",
        )


def run(*, client=None, kill_switch=False, run_id="strategy-run", shadow_run_id="shadow-run"):
    return run_strategy_shadow(
        candidate(),
        portfolio=portfolio(),
        limits=limits(),
        context=context(kill_switch=kill_switch),
        market_condition_id="condition-1",
        tokens=TOKENS,
        post_only=True,
        client=client or FakeClient(),
        run_id=run_id,
        shadow_run_id=shadow_run_id,
    )


def test_risk_rejected_does_not_build_or_submit_shadow_order():
    client = FakeClient()

    artifact = run(
        client=client,
        kill_switch=True,
    )

    assert isinstance(artifact, StrategyShadowRunArtifact)
    assert artifact.status == "RISK_REJECTED"
    assert artifact.order_intent is None
    assert artifact.shadow_artifact is None
    assert client.calls == []


def test_shadow_pass_records_full_auditable_chain():
    client = FakeClient()

    artifact = run(client=client)

    assert artifact.status == "SHADOW_PASS"
    assert artifact.risk_assessment.risk.allowed
    assert artifact.order_intent.intent_id == "candidate-1"
    assert artifact.order_intent.strategy_id == "alpha-1"
    assert artifact.order_intent.strategy_version == "1"
    assert artifact.order_intent.risk_policy_version == "risk-v1"
    assert artifact.shadow_artifact.n_total == 1
    assert artifact.shadow_artifact.n_pass == 1
    assert artifact.shadow_artifact.n_fail == 0
    assert client.calls == ["candidate-1"]
    assert strategy_shadow_run_artifact_hash(artifact) == artifact.artifact_hash


def test_shadow_known_failure_is_artifact_fail_not_exception():
    artifact = run(client=FakeClient(fail=True))

    assert artifact.status == "SHADOW_FAIL"
    assert artifact.order_intent is not None
    assert artifact.shadow_artifact.n_total == 1
    assert artifact.shadow_artifact.n_pass == 0
    assert artifact.shadow_artifact.n_fail == 1


def test_artifact_hash_ignores_strategy_and_shadow_run_ids():
    first = run(
        run_id="strategy-run-a",
        shadow_run_id="shadow-run-a",
    )
    second = run(
        run_id="strategy-run-b",
        shadow_run_id="shadow-run-b",
    )

    assert first.run_id != second.run_id
    assert first.shadow_run_id != second.shadow_run_id
    assert first.artifact_hash == second.artifact_hash


def test_unexpected_client_error_propagates():
    class BuggyClient:
        def submit(self, **kwargs):
            raise RuntimeError("programmer bug")

    with pytest.raises(RuntimeError, match="programmer bug"):
        run(client=BuggyClient())


@pytest.mark.parametrize(
    "field,value,match",
    (
        ("candidate_hash", "0" * 64, "candidate hash mismatch"),
        ("risk_policy_version", "other-risk", "risk policy version mismatch"),
        ("portfolio_hash", "1" * 64, "portfolio hash mismatch"),
        ("risk_limits_hash", "2" * 64, "risk limits hash mismatch"),
        ("risk_context_hash", "3" * 64, "risk context hash mismatch"),
    ),
)
def test_artifact_rejects_top_level_risk_provenance_mismatch(field, value, match):
    artifact = run()

    with pytest.raises(ValueError, match=match):
        replace(
            artifact,
            **{
                field: value,
                "artifact_hash": "PENDING",
            },
        )
