import base64

import pytest

from std0_quant.execution.measured_venue_trusted_public_key_policy import (
    ED25519,
    MEASURED_VENUE_TRUSTED_PUBLIC_KEY_RULE_SCHEMA_V1,
    MEASURED_VENUE_TRUSTED_PUBLIC_KEY_POLICY_SCHEMA_V1,
    MeasuredVenueTrustedPublicKeyRule,
    MeasuredVenueTrustedPublicKeyPolicy,
    measured_venue_trusted_public_key_policy_hash,
    resolve_measured_venue_trusted_public_key_rule,
)


def key_b64(byte_value=1):
    return base64.b64encode(
        bytes([byte_value]) * 32
    ).decode("ascii")


def rule(**changes):
    values = {
        "signer_id": "collector-signing-authority",
        "key_id": "collector-key-001",
        "signature_algorithm": ED25519,
        "public_key_b64": key_b64(),
        "collector_id": "collector-a",
        "collector_version": "1",
        "venue_id": "polymarket",
        "acquisition_mode": "read-only-export",
    }
    values.update(changes)
    return MeasuredVenueTrustedPublicKeyRule(**values)


def policy(*rules):
    return MeasuredVenueTrustedPublicKeyPolicy(
        policy_id="trusted-public-key-policy",
        version="1",
        rules=tuple(rules or (rule(),)),
    )


def test_contract_symbols_and_schema():
    assert ED25519 == "ED25519"
    assert (
        MEASURED_VENUE_TRUSTED_PUBLIC_KEY_RULE_SCHEMA_V1
        == "measured_venue_trusted_public_key_rule_v1"
    )
    assert (
        MEASURED_VENUE_TRUSTED_PUBLIC_KEY_POLICY_SCHEMA_V1
        == "measured_venue_trusted_public_key_policy_v1"
    )


def test_rule_accepts_canonical_32_byte_ed25519_public_key():
    r = rule()

    assert r.public_key_b64 == key_b64()
    assert r.signature_algorithm == ED25519


def test_rule_rejects_non_ed25519_algorithm():
    with pytest.raises(ValueError):
        rule(signature_algorithm="HMAC-SHA256")


def test_rule_rejects_noncanonical_base64_public_key():
    # AB== decodes, but is not canonical Base64.
    with pytest.raises(ValueError):
        rule(public_key_b64="AB==")


def test_rule_rejects_wrong_public_key_length():
    for size in (31, 33):
        with pytest.raises(ValueError):
            rule(
                public_key_b64=base64.b64encode(
                    bytes(size)
                ).decode("ascii")
            )


def test_policy_requires_nonempty_rules():
    with pytest.raises(ValueError):
        MeasuredVenueTrustedPublicKeyPolicy(
            policy_id="trusted-public-key-policy",
            version="1",
            rules=(),
        )


def test_policy_rejects_duplicate_exact_rule():
    r = rule()

    with pytest.raises(ValueError):
        policy(r, r)


def test_policy_rejects_same_signer_key_id_with_different_key_material():
    with pytest.raises(ValueError):
        policy(
            rule(),
            rule(
                public_key_b64=key_b64(2),
                collector_id="collector-b",
            ),
        )


def test_policy_allows_same_key_in_multiple_explicit_contexts():
    p = policy(
        rule(),
        rule(
            collector_id="collector-b",
            collector_version="2",
            acquisition_mode="stream-export",
        ),
    )

    assert len(p.rules) == 2


def test_policy_hash_is_rule_order_insensitive():
    first = rule()
    second = rule(
        collector_id="collector-b",
        collector_version="2",
        acquisition_mode="stream-export",
    )

    assert (
        measured_venue_trusted_public_key_policy_hash(
            policy(first, second)
        )
        == measured_venue_trusted_public_key_policy_hash(
            policy(second, first)
        )
    )


def test_policy_hash_changes_when_public_key_changes():
    assert (
        measured_venue_trusted_public_key_policy_hash(
            policy(rule(public_key_b64=key_b64(1)))
        )
        != measured_venue_trusted_public_key_policy_hash(
            policy(rule(public_key_b64=key_b64(2)))
        )
    )


def test_policy_hash_changes_when_allowed_context_changes():
    assert (
        measured_venue_trusted_public_key_policy_hash(
            policy(rule())
        )
        != measured_venue_trusted_public_key_policy_hash(
            policy(
                rule(acquisition_mode="stream-export")
            )
        )
    )


def test_resolver_returns_exact_allowed_context():
    p = policy(
        rule(),
        rule(
            collector_id="collector-b",
            collector_version="2",
            acquisition_mode="stream-export",
        ),
    )

    resolved = resolve_measured_venue_trusted_public_key_rule(
        p,
        signer_id="collector-signing-authority",
        key_id="collector-key-001",
        signature_algorithm=ED25519,
        collector_id="collector-b",
        collector_version="2",
        venue_id="polymarket",
        acquisition_mode="stream-export",
    )

    assert resolved == p.rules[1]


def test_resolver_rejects_unallowed_context():
    p = policy(rule())

    assert (
        resolve_measured_venue_trusted_public_key_rule(
            p,
            signer_id="collector-signing-authority",
            key_id="collector-key-001",
            signature_algorithm=ED25519,
            collector_id="collector-x",
            collector_version="1",
            venue_id="polymarket",
            acquisition_mode="read-only-export",
        )
        is None
    )


def test_public_key_rejects_surrounding_whitespace():
    with pytest.raises(ValueError):
        rule(public_key_b64=" " + key_b64() + " ")


def test_policy_hash_binds_policy_identity():
    r = rule()

    p1 = MeasuredVenueTrustedPublicKeyPolicy(
        policy_id="trusted-public-key-policy",
        version="1",
        rules=(r,),
    )
    p2 = MeasuredVenueTrustedPublicKeyPolicy(
        policy_id="different-policy",
        version="1",
        rules=(r,),
    )
    p3 = MeasuredVenueTrustedPublicKeyPolicy(
        policy_id="trusted-public-key-policy",
        version="2",
        rules=(r,),
    )

    h1 = measured_venue_trusted_public_key_policy_hash(p1)

    assert (
        h1
        != measured_venue_trusted_public_key_policy_hash(p2)
    )
    assert (
        h1
        != measured_venue_trusted_public_key_policy_hash(p3)
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("signer_id", "different-signer"),
        ("key_id", "different-key"),
        ("signature_algorithm", "DIFFERENT"),
        ("collector_id", "different-collector"),
        ("collector_version", "different-version"),
        ("venue_id", "different-venue"),
        ("acquisition_mode", "different-mode"),
    ],
)
def test_resolver_requires_exact_identity_and_context(field, value):
    p = policy(rule())

    kwargs = {
        "signer_id": "collector-signing-authority",
        "key_id": "collector-key-001",
        "signature_algorithm": ED25519,
        "collector_id": "collector-a",
        "collector_version": "1",
        "venue_id": "polymarket",
        "acquisition_mode": "read-only-export",
    }
    kwargs[field] = value

    assert (
        resolve_measured_venue_trusted_public_key_rule(
            p,
            **kwargs,
        )
        is None
    )


def test_resolver_rejects_non_policy_object():
    with pytest.raises(TypeError):
        resolve_measured_venue_trusted_public_key_rule(
            object(),
            signer_id="collector-signing-authority",
            key_id="collector-key-001",
            signature_algorithm=ED25519,
            collector_id="collector-a",
            collector_version="1",
            venue_id="polymarket",
            acquisition_mode="read-only-export",
        )


def test_policy_rejects_public_key_aliasing_across_logical_identities():
    with pytest.raises(ValueError):
        policy(
            rule(),
            rule(
                signer_id="different-signer",
                key_id="different-key",
                collector_id="collector-b",
            ),
        )


def test_policy_allows_same_logical_key_across_explicit_contexts():
    p = policy(
        rule(),
        rule(
            collector_id="collector-b",
            collector_version="2",
            acquisition_mode="stream-export",
        ),
    )

    assert len(p.rules) == 2
    assert p.rules[0].signer_id == p.rules[1].signer_id
    assert p.rules[0].key_id == p.rules[1].key_id
    assert p.rules[0].public_key_b64 == p.rules[1].public_key_b64


def test_policy_allows_key_rotation_with_distinct_key_id_and_material():
    p = policy(
        rule(),
        rule(
            key_id="collector-key-002",
            public_key_b64=key_b64(2),
        ),
    )

    assert len(p.rules) == 2


def test_resolver_does_not_fallback_between_contexts_for_same_key():
    p = policy(
        rule(),
        rule(
            collector_id="collector-b",
            collector_version="2",
            acquisition_mode="stream-export",
        ),
    )

    assert (
        resolve_measured_venue_trusted_public_key_rule(
            p,
            signer_id="collector-signing-authority",
            key_id="collector-key-001",
            signature_algorithm=ED25519,
            collector_id="collector-b",
            collector_version="1",
            venue_id="polymarket",
            acquisition_mode="stream-export",
        )
        is None
    )
