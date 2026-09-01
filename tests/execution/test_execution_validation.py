from dataclasses import replace

from std0_quant.execution.batch_shadow_runner import batch_shadow_artifact_hash
from std0_quant.execution.contracts import OrderEvent, OrderEventType
from std0_quant.execution.execution_validation import (
    ExecutionValidationArtifact,
    ExecutionValidationTarget,
    build_execution_validation_artifact,
    execution_validation_artifact_hash,
)
from std0_quant.execution.portfolio import PortfolioState
from std0_quant.execution.risk import RiskContext, RiskDecision, RiskLimits
from std0_quant.execution.strategy_candidate import (
    StrategyOrderCandidate,
    strategy_candidate_hash,
)
from std0_quant.execution.strategy_shadow_run import (
    run_strategy_shadow,
    strategy_shadow_run_artifact_hash,
)
from std0_quant.research.alpha_factory import (
    AlphaCandidateSpec,
    AlphaFactorBinding,
    AlphaFactoryArtifact,
    AlphaResearchEvidence,
    BLOCKED,
    READY_FOR_GOVERNANCE_REVIEW,
    alpha_factory_artifact_hash,
)


FACTOR_ID = "factor-a"
FACTOR_VERSION = "1"
DEFINITION_HASH = "a" * 64
ALPHA_ID = "alpha-a"
ALPHA_VERSION = "1"
RISK_POLICY_VERSION = "risk-v1"


def target():
    return ExecutionValidationTarget(
        factor_id=FACTOR_ID,
        factor_version=FACTOR_VERSION,
        definition_hash=DEFINITION_HASH,
        alpha_id=ALPHA_ID,
        alpha_version=ALPHA_VERSION,
        risk_policy_version=RISK_POLICY_VERSION,
    )


def alpha_spec():
    return AlphaCandidateSpec(
        alpha_id=ALPHA_ID,
        alpha_version=ALPHA_VERSION,
        factor_bindings=(
            AlphaFactorBinding(
                factor_id=FACTOR_ID,
                factor_version=FACTOR_VERSION,
                definition_hash=DEFINITION_HASH,
            ),
        ),
        risk_policy_version=RISK_POLICY_VERSION,
        created_by="test",
        created_at="2026-09-01T00:00:00Z",
    )


def alpha_artifact(*, status=READY_FOR_GOVERNANCE_REVIEW, reasons=()):
    provisional = AlphaFactoryArtifact(
        alpha_run_id="alpha-run",
        alpha_id=ALPHA_ID,
        alpha_version=ALPHA_VERSION,
        risk_policy_version=RISK_POLICY_VERSION,
        status=status,
        reasons=tuple(reasons),
        factor_evidence=(
            AlphaResearchEvidence(
                factor_id=FACTOR_ID,
                factor_version=FACTOR_VERSION,
                definition_hash=DEFINITION_HASH,
                registry_status="VALIDATED",
                research_validation_status="PASS",
                temporal_integrity="PASS",
                research_artifact_hash="b" * 64,
                research_run_id="research-run",
                policy_id="research-policy",
                policy_version="1",
                policy_hash="c" * 64,
                validation_evidence_bundle_hash="d" * 64,
            ),
        ),
        shadow_artifact_hash="e" * 64,
        shadow_run_id="alpha-shadow-run",
        n_shadow_total=1,
        n_shadow_pass=1,
        n_shadow_fail=0,
        artifact_hash="PENDING",
    )
    return replace(
        provisional,
        artifact_hash=alpha_factory_artifact_hash(provisional),
    )


def candidate():
    return StrategyOrderCandidate(
        candidate_id="candidate-1",
        alpha_id=ALPHA_ID,
        alpha_version=ALPHA_VERSION,
        risk_policy_version=RISK_POLICY_VERSION,
        condition_id="condition-1",
        outcome="Up",
        side="BUY",
        qty=10,
        limit_price=0.5,
        time_in_force="GTC",
        decision_ts_ms=1001,
        market_data_ts_ms=1000,
    )


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


def shadow(*, fail=False, run_id="strategy-shadow-run", shadow_run_id="batch-shadow-run"):
    return run_strategy_shadow(
        candidate(),
        portfolio=PortfolioState(cash=100),
        limits=RiskLimits(
            max_order_notional=100,
            max_market_exposure=1000,
            max_gross_exposure=1000,
            max_daily_loss=1000,
            max_market_data_age_ms=1000,
        ),
        context=RiskContext(
            now_ts_ms=1001,
            market_data_ts_ms=1000,
        ),
        market_condition_id="condition-1",
        tokens=(
            ("token-up", "Up"),
            ("token-down", "Down"),
        ),
        post_only=True,
        client=FakeClient(fail=fail),
        run_id=run_id,
        shadow_run_id=shadow_run_id,
    )


def build(
    *,
    t=None,
    spec=None,
    alpha=None,
    cand=None,
    shadow_artifact=None,
    execution_run_id="execution-run",
):
    return build_execution_validation_artifact(
        t or target(),
        spec or alpha_spec(),
        alpha or alpha_artifact(),
        cand or candidate(),
        shadow_artifact or shadow(),
        execution_run_id=execution_run_id,
    )


def test_valid_provenance_is_ready_for_policy_evaluation():
    artifact = build()

    assert isinstance(artifact, ExecutionValidationArtifact)
    assert artifact.status == "READY_FOR_POLICY_EVALUATION"
    assert artifact.reasons == ()
    assert artifact.target == target()
    assert artifact.alpha_factory_artifact_hash == alpha_artifact().artifact_hash
    assert artifact.strategy_candidate_hash == strategy_candidate_hash(candidate())
    assert artifact.strategy_shadow_artifact_hash == shadow().artifact_hash
    assert artifact.protocol_version == shadow().protocol_version
    assert artifact.clodds_commit == shadow().clodds_commit
    assert artifact.mapping_version == shadow().mapping_version
    assert execution_validation_artifact_hash(artifact) == artifact.artifact_hash


def test_target_factor_must_be_bound_in_alpha_spec():
    wrong = replace(target(), factor_id="other-factor")

    artifact = build(t=wrong)

    assert artifact.status == "BLOCKED"
    assert "TARGET_FACTOR_NOT_BOUND" in artifact.reasons


def test_target_definition_hash_must_match_binding():
    wrong = replace(target(), definition_hash="f" * 64)

    artifact = build(t=wrong)

    assert artifact.status == "BLOCKED"
    assert "TARGET_FACTOR_DEFINITION_HASH_MISMATCH" in artifact.reasons


def test_alpha_identity_and_risk_policy_must_match_target():
    wrong_spec = replace(
        alpha_spec(),
        alpha_version="2",
        risk_policy_version="risk-v2",
    )

    artifact = build(spec=wrong_spec)

    assert artifact.status == "BLOCKED"
    assert "ALPHA_VERSION_MISMATCH" in artifact.reasons
    assert "RISK_POLICY_VERSION_MISMATCH" in artifact.reasons


def test_alpha_factory_must_be_ready_and_hash_valid():
    blocked = alpha_artifact(
        status=BLOCKED,
        reasons=("SHADOW_HAS_FAILURES",),
    )

    artifact = build(alpha=blocked)

    assert artifact.status == "BLOCKED"
    assert "ALPHA_FACTORY_NOT_READY" in artifact.reasons

    tampered = replace(alpha_artifact(), artifact_hash="0" * 64)
    artifact = build(alpha=tampered)

    assert artifact.status == "BLOCKED"
    assert "ALPHA_FACTORY_ARTIFACT_HASH_MISMATCH" in artifact.reasons


def test_target_factor_evidence_must_exist_and_match():
    base = alpha_artifact()
    missing = replace(base, factor_evidence=())
    missing = replace(
        missing,
        artifact_hash=alpha_factory_artifact_hash(
            replace(missing, artifact_hash="PENDING")
        ),
    )

    artifact = build(alpha=missing)

    assert artifact.status == "BLOCKED"
    assert "TARGET_FACTOR_EVIDENCE_NOT_FOUND" in artifact.reasons


def test_candidate_and_shadow_must_bind_to_same_alpha():
    wrong_candidate = replace(candidate(), alpha_version="2")

    artifact = build(cand=wrong_candidate)

    assert artifact.status == "BLOCKED"
    assert "CANDIDATE_ALPHA_VERSION_MISMATCH" in artifact.reasons
    assert "STRATEGY_SHADOW_CANDIDATE_HASH_MISMATCH" in artifact.reasons


def test_shadow_must_pass_and_artifact_hash_must_be_valid():
    failed = shadow(fail=True)

    artifact = build(shadow_artifact=failed)

    assert artifact.status == "BLOCKED"
    assert "STRATEGY_SHADOW_NOT_PASS" in artifact.reasons

    valid = shadow()
    tampered = replace(valid, artifact_hash="0" * 64)
    artifact = build(shadow_artifact=tampered)

    assert artifact.status == "BLOCKED"
    assert "STRATEGY_SHADOW_ARTIFACT_HASH_MISMATCH" in artifact.reasons


def test_execution_artifact_hash_ignores_run_ids():
    first = build(
        execution_run_id="execution-run-a",
        shadow_artifact=shadow(
            run_id="strategy-shadow-a",
            shadow_run_id="batch-shadow-a",
        ),
    )
    second = build(
        execution_run_id="execution-run-b",
        shadow_artifact=shadow(
            run_id="strategy-shadow-b",
            shadow_run_id="batch-shadow-b",
        ),
    )

    assert first.execution_run_id != second.execution_run_id
    assert first.strategy_shadow_run_id != second.strategy_shadow_run_id
    assert first.artifact_hash == second.artifact_hash



def test_target_factor_evidence_semantics_must_remain_valid():
    base = alpha_artifact()
    bad_evidence = replace(
        base.factor_evidence[0],
        registry_status="REJECTED",
        research_validation_status="FAIL",
        temporal_integrity="FAIL",
        validation_evidence_bundle_hash=None,
    )
    tampered = replace(
        base,
        factor_evidence=(bad_evidence,),
        artifact_hash="PENDING",
    )
    tampered = replace(
        tampered,
        artifact_hash=alpha_factory_artifact_hash(tampered),
    )

    artifact = build(alpha=tampered)

    assert artifact.status == "BLOCKED"
    assert "TARGET_FACTOR_REGISTRY_NOT_VALIDATED" in artifact.reasons
    assert "TARGET_FACTOR_RESEARCH_NOT_PASS" in artifact.reasons
    assert "TARGET_FACTOR_TEMPORAL_NOT_PASS" in artifact.reasons
    assert "TARGET_FACTOR_EVIDENCE_BUNDLE_MISSING" in artifact.reasons


def test_alpha_factory_shadow_summary_must_remain_valid():
    base = alpha_artifact()
    tampered = replace(
        base,
        n_shadow_pass=0,
        n_shadow_fail=1,
        artifact_hash="PENDING",
    )
    tampered = replace(
        tampered,
        artifact_hash=alpha_factory_artifact_hash(tampered),
    )

    artifact = build(alpha=tampered)

    assert artifact.status == "BLOCKED"
    assert "ALPHA_FACTORY_SHADOW_HAS_FAILURES" in artifact.reasons


def test_nested_batch_shadow_artifact_hash_must_be_valid():
    valid = shadow()
    nested = replace(
        valid.shadow_artifact,
        artifact_hash="0" * 64,
    )
    tampered = replace(
        valid,
        shadow_artifact=nested,
        artifact_hash="PENDING",
    )
    tampered = replace(
        tampered,
        artifact_hash=strategy_shadow_run_artifact_hash(tampered),
    )

    artifact = build(shadow_artifact=tampered)

    assert artifact.status == "BLOCKED"
    assert "BATCH_SHADOW_ARTIFACT_HASH_MISMATCH" in artifact.reasons



import pytest


@pytest.mark.parametrize(
    "field,value",
    (
        ("intent_id", "other-intent"),
        ("condition_id", "other-condition"),
        ("outcome", "Down"),
        ("qty", 11.0),
        ("limit_price", 0.6),
        ("decision_ts_ms", 1002),
        ("market_data_ts_ms", 999),
    ),
)
def test_order_intent_must_be_exact_candidate_projection(field, value):
    valid = shadow()
    tampered_intent = replace(
        valid.order_intent,
        **{field: value},
    )
    tampered = replace(
        valid,
        order_intent=tampered_intent,
        artifact_hash="PENDING",
    )
    tampered = replace(
        tampered,
        artifact_hash=strategy_shadow_run_artifact_hash(tampered),
    )

    artifact = build(shadow_artifact=tampered)

    assert artifact.status == "BLOCKED"
    assert "ORDER_INTENT_CANDIDATE_PROJECTION_MISMATCH" in artifact.reasons


def test_shadow_pass_must_still_bind_to_risk_allow():
    valid = shadow()
    rejected_risk = replace(
        valid.risk_assessment.risk,
        decision=RiskDecision.REJECT,
        reasons=("forced-reject",),
    )
    assessment = replace(
        valid.risk_assessment,
        risk=rejected_risk,
    )
    tampered = replace(
        valid,
        risk_assessment=assessment,
        artifact_hash="PENDING",
    )
    tampered = replace(
        tampered,
        artifact_hash=strategy_shadow_run_artifact_hash(tampered),
    )

    artifact = build(shadow_artifact=tampered)

    assert artifact.status == "BLOCKED"
    assert "STRATEGY_SHADOW_RISK_NOT_ALLOW" in artifact.reasons


def test_nested_batch_request_intent_must_match_outer_order_intent():
    valid = shadow()
    inner_intent = replace(
        valid.order_intent,
        qty=11.0,
    )
    request = replace(
        valid.shadow_artifact.items[0].request,
        intent=inner_intent,
    )
    item = replace(
        valid.shadow_artifact.items[0],
        request=request,
    )
    nested = replace(
        valid.shadow_artifact,
        items=(item,),
        artifact_hash="PENDING",
    )
    nested = replace(
        nested,
        artifact_hash=batch_shadow_artifact_hash(nested),
    )
    tampered = replace(
        valid,
        shadow_artifact=nested,
        artifact_hash="PENDING",
    )
    tampered = replace(
        tampered,
        artifact_hash=strategy_shadow_run_artifact_hash(tampered),
    )

    artifact = build(shadow_artifact=tampered)

    assert artifact.status == "BLOCKED"
    assert "BATCH_SHADOW_ORDER_INTENT_MISMATCH" in artifact.reasons
