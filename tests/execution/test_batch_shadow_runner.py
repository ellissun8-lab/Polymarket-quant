import pytest

from std0_quant.execution.batch_shadow_runner import (
    BatchShadowRequest,
    batch_shadow_artifact_hash,
    run_shadow_batch,
)
from std0_quant.execution.clodds_mapping import (
    CLODDS_MAPPING_VERSION_V1,
)
from std0_quant.execution.clodds_shadow_protocol import (
    AUDITED_CLODDS_COMMIT_V1,
    CLODDS_SHADOW_PROTOCOL_V1,
)
from std0_quant.execution.contracts import (
    OrderEvent,
    OrderEventType,
    OrderIntent,
)


TOKENS = (
    ("token-up", "Up"),
    ("token-down", "Down"),
)


def make_intent(intent_id="intent-1", qty=10):
    return OrderIntent(
        intent_id=intent_id,
        condition_id="condition-1",
        outcome="Up",
        side="BUY",
        qty=qty,
        limit_price=0.50,
        time_in_force="GTC",
        decision_ts_ms=1001,
        market_data_ts_ms=1000,
        strategy_id="std0_candidate",
        strategy_version="v1",
        risk_policy_version="risk_v1",
    )


def request(intent_id="intent-1", qty=10):
    return BatchShadowRequest(
        intent=make_intent(intent_id, qty=qty),
        market_condition_id="condition-1",
        tokens=TOKENS,
        post_only=True,
    )


class FakeClient:
    def __init__(self, fail_ids=()):
        self.fail_ids = set(fail_ids)
        self.calls = []

    def submit(
        self,
        *,
        intent,
        market_condition_id,
        tokens,
        post_only,
    ):
        self.calls.append(
            (
                intent.intent_id,
                market_condition_id,
                tuple(tokens),
                post_only,
            )
        )

        if intent.intent_id in self.fail_ids:
            raise ValueError("forced validation failure")

        return OrderEvent(
            event_id=f"{intent.intent_id}:shadow_ack",
            intent_id=intent.intent_id,
            event_type=OrderEventType.VENUE_ACK,
            receive_ts_ms=2000 + len(self.calls),
            venue_ts_ms=None,
            venue_order_id=f"shadow:{intent.intent_id}",
            fill_qty=0.0,
            fill_price=None,
            cumulative_filled_qty=0.0,
            remaining_qty=intent.qty,
            reason="SHADOW_SYNTHETIC_ACK",
        )


def test_batch_preserves_order_and_records_pass_fail():
    client = FakeClient(fail_ids={"intent-2"})

    artifact = run_shadow_batch(
        [
            request("intent-1"),
            request("intent-2"),
            request("intent-3"),
        ],
        client=client,
        run_id="run-1",
    )

    assert artifact.schema_version == "batch_shadow_artifact_v1"
    assert artifact.runner_version == "batch_shadow_runner_v1"
    assert artifact.mode == "SHADOW"
    assert artifact.protocol_version == CLODDS_SHADOW_PROTOCOL_V1
    assert artifact.clodds_commit == AUDITED_CLODDS_COMMIT_V1
    assert artifact.mapping_version == CLODDS_MAPPING_VERSION_V1

    assert artifact.n_total == 3
    assert artifact.n_pass == 2
    assert artifact.n_fail == 1

    assert [item.intent_id for item in artifact.items] == [
        "intent-1",
        "intent-2",
        "intent-3",
    ]
    assert [item.status for item in artifact.items] == [
        "PASS",
        "FAIL",
        "PASS",
    ]

    assert artifact.items[0].event.intent_id == "intent-1"
    assert artifact.items[1].event is None
    assert artifact.items[1].error_type == "ValueError"
    assert artifact.items[1].error_message == "forced validation failure"

    assert [row[0] for row in client.calls] == [
        "intent-1",
        "intent-2",
        "intent-3",
    ]


def test_artifact_hash_ignores_run_id():
    first = run_shadow_batch(
        [request()],
        client=FakeClient(),
        run_id="run-a",
    )
    second = run_shadow_batch(
        [request()],
        client=FakeClient(),
        run_id="run-b",
    )

    assert first.run_id != second.run_id
    assert first.artifact_hash == second.artifact_hash
    assert batch_shadow_artifact_hash(first) == first.artifact_hash
    assert batch_shadow_artifact_hash(second) == second.artifact_hash


def test_artifact_hash_binds_request_content():
    first = run_shadow_batch(
        [request(qty=10)],
        client=FakeClient(),
        run_id="run-a",
    )
    second = run_shadow_batch(
        [request(qty=11)],
        client=FakeClient(),
        run_id="run-a",
    )

    assert first.artifact_hash != second.artifact_hash


def test_duplicate_intent_id_fails_before_submission():
    client = FakeClient()

    with pytest.raises(ValueError, match="duplicate intent_id"):
        run_shadow_batch(
            [
                request("same"),
                request("same"),
            ],
            client=client,
            run_id="run-1",
        )

    assert client.calls == []


def test_empty_run_id_fails_before_submission():
    client = FakeClient()

    with pytest.raises(ValueError, match="run_id"):
        run_shadow_batch(
            [request()],
            client=client,
            run_id=" ",
        )

    assert client.calls == []


def test_unexpected_programmer_error_is_not_swallowed():
    class BuggyClient:
        def submit(self, **kwargs):
            raise RuntimeError("programmer bug")

    with pytest.raises(RuntimeError, match="programmer bug"):
        run_shadow_batch(
            [request()],
            client=BuggyClient(),
            run_id="run-1",
        )
