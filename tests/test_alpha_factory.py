import pytest

from std0_quant.execution.batch_shadow_runner import BatchShadowRequest, run_shadow_batch
from std0_quant.execution.contracts import OrderEvent, OrderEventType, OrderIntent
from std0_quant.research.alpha_factory import (
    AlphaCandidateSpec,
    AlphaFactorBinding,
    alpha_factory_artifact_hash,
    build_alpha_factory_artifact,
)
from std0_quant.research.factors.contracts import FactorStatus, ValidationStatus
from std0_quant.research.factors.registry import FactorRegistryRecord
from std0_quant.research.factors.validator import ValidationDecision


class FakeClient:
    def __init__(self, fail=False):
        self.fail = fail

    def submit(self, *, intent, market_condition_id, tokens, post_only):
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


def make_spec(alpha_id="alpha-1", alpha_version="1", risk_policy_version="risk-v1"):
    return AlphaCandidateSpec(
        alpha_id=alpha_id,
        alpha_version=alpha_version,
        factor_bindings=(AlphaFactorBinding("factor-1", "1", "definition-hash"),),
        risk_policy_version=risk_policy_version,
        created_by="human",
        created_at="2026-08-31T00:00:00+00:00",
    )


def make_record(status=FactorStatus.VALIDATED, definition_hash="definition-hash"):
    return FactorRegistryRecord(
        factor_id="factor-1",
        factor_version="1",
        definition_hash=definition_hash,
        status=status,
        created_by="human",
        created_at="2026-08-31T00:00:00+00:00",
    )


def make_decision(
    research_status=ValidationStatus.PASS,
    temporal=ValidationStatus.PASS,
    bundle_hash="bundle-hash",
):
    return ValidationDecision(
        factor_id="factor-1",
        factor_version="1",
        research_validation_status=research_status,
        temporal_integrity=temporal,
        reasons=(),
        research_artifact_hash="research-hash",
        research_run_id="research-run",
        policy_id="policy",
        policy_version="1",
        policy_hash="policy-hash",
        validation_evidence_bundle_hash=bundle_hash,
    )


def make_shadow(
    strategy_id="alpha-1",
    strategy_version="1",
    risk_policy_version="risk-v1",
    fail=False,
    run_id="shadow-run",
):
    intent = OrderIntent(
        intent_id="intent-1",
        condition_id="condition-1",
        outcome="Up",
        side="BUY",
        qty=10,
        limit_price=0.5,
        time_in_force="GTC",
        decision_ts_ms=1001,
        market_data_ts_ms=1000,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        risk_policy_version=risk_policy_version,
    )
    request = BatchShadowRequest(
        intent=intent,
        market_condition_id="condition-1",
        tokens=(("token-up", "Up"), ("token-down", "Down")),
        post_only=True,
    )
    return run_shadow_batch((request,), client=FakeClient(fail=fail), run_id=run_id)


def build(spec=None, record=None, decision=None, shadow=None, run_id="alpha-run"):
    return build_alpha_factory_artifact(
        spec or make_spec(),
        (record or make_record(),),
        (decision or make_decision(),),
        shadow or make_shadow(),
        alpha_run_id=run_id,
    )


def test_alpha_factory_ready_for_governance_review():
    artifact = build()

    assert artifact.status == "READY_FOR_GOVERNANCE_REVIEW"
    assert artifact.reasons == ()
    assert artifact.alpha_id == "alpha-1"
    assert artifact.n_shadow_total == 1
    assert artifact.n_shadow_pass == 1
    assert artifact.n_shadow_fail == 0
    assert artifact.factor_evidence[0].research_artifact_hash == "research-hash"
    assert artifact.factor_evidence[0].validation_evidence_bundle_hash == "bundle-hash"
    assert alpha_factory_artifact_hash(artifact) == artifact.artifact_hash


def test_alpha_factory_blocks_shadow_strategy_identity_mismatch():
    artifact = build(shadow=make_shadow(strategy_id="wrong-alpha"))

    assert artifact.status == "BLOCKED"
    assert "SHADOW_STRATEGY_ID_MISMATCH" in artifact.reasons


def test_alpha_factory_blocks_shadow_strategy_version_and_risk_policy_mismatch():
    artifact = build(
        shadow=make_shadow(
            strategy_version="wrong-version",
            risk_policy_version="wrong-risk",
        )
    )

    assert artifact.status == "BLOCKED"
    assert "SHADOW_STRATEGY_VERSION_MISMATCH" in artifact.reasons
    assert "SHADOW_RISK_POLICY_VERSION_MISMATCH" in artifact.reasons


@pytest.mark.parametrize(
    "record,decision,reason",
    [
        (make_record(status=FactorStatus.CANDIDATE), make_decision(), "FACTOR_STATUS_NOT_VALIDATED"),
        (make_record(definition_hash="wrong-hash"), make_decision(), "FACTOR_DEFINITION_HASH_MISMATCH"),
        (make_record(), make_decision(research_status=ValidationStatus.FAIL), "RESEARCH_VALIDATION_NOT_PASS"),
        (make_record(), make_decision(temporal=ValidationStatus.FAIL), "TEMPORAL_INTEGRITY_NOT_PASS"),
        (make_record(), make_decision(bundle_hash=None), "VALIDATION_EVIDENCE_BUNDLE_HASH_MISSING"),
    ],
)
def test_alpha_factory_fail_closed_on_research_evidence(record, decision, reason):
    artifact = build(record=record, decision=decision)

    assert artifact.status == "BLOCKED"
    assert reason in artifact.reasons


def test_alpha_factory_blocks_shadow_failure():
    artifact = build(shadow=make_shadow(fail=True))

    assert artifact.status == "BLOCKED"
    assert "SHADOW_HAS_FAILURES" in artifact.reasons


def test_alpha_factory_hash_ignores_alpha_run_id():
    first = build(run_id="alpha-run-a")
    second = build(run_id="alpha-run-b")

    assert first.alpha_run_id != second.alpha_run_id
    assert first.artifact_hash == second.artifact_hash


def test_alpha_factory_hash_binds_alpha_identity():
    first = build()

    second = build_alpha_factory_artifact(
        make_spec(alpha_id="alpha-2"),
        (make_record(),),
        (make_decision(),),
        make_shadow(strategy_id="alpha-2"),
        alpha_run_id="alpha-run",
    )

    assert first.artifact_hash != second.artifact_hash


def test_duplicate_factor_binding_fails_closed():
    binding = AlphaFactorBinding("factor-1", "1", "definition-hash")

    with pytest.raises(ValueError, match="duplicate factor binding"):
        AlphaCandidateSpec(
            alpha_id="alpha-1",
            alpha_version="1",
            factor_bindings=(binding, binding),
            risk_policy_version="risk-v1",
            created_by="human",
            created_at="2026-08-31T00:00:00+00:00",
        )
