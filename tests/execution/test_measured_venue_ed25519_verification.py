import base64
from dataclasses import replace

from std0_quant.execution.measured_venue_ed25519_verification import (
    BLOCKED_SIGNATURE_VERIFICATION,
    SIGNATURE_VERIFIED,
    MEASURED_VENUE_ED25519_VERIFICATION_SCHEMA_V1,
    MeasuredVenueEd25519Verification,
    measured_venue_ed25519_signing_message,
    measured_venue_ed25519_verification_artifact_hash,
    verify_measured_venue_ed25519_signature,
    verify_measured_venue_ed25519_verification_artifact,
)

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)

from std0_quant.execution.measured_venue_acquisition_attestation import (
    ED25519,
    PENDING_SIGNATURE_VERIFICATION,
    build_measured_venue_acquisition_attestation,
    measured_venue_acquisition_attestation_artifact_hash,
)
from std0_quant.execution.measured_venue_source_qualification import (
    QUALIFIED,
    MeasuredVenueSourceQualification,
    measured_venue_source_qualification_hash,
)
from std0_quant.execution.measured_venue_trusted_public_key_policy import (
    MeasuredVenueTrustedPublicKeyPolicy,
    MeasuredVenueTrustedPublicKeyRule,
)


DOMAIN = (
    b"std0-quant/measured-venue-acquisition-attestation/v1\n"
)


def qualification():
    provisional = MeasuredVenueSourceQualification(
        qualification_run_id="qualification-run-001",
        evidence_run_id="measured-run-001",
        evidence_artifact_hash="a" * 64,
        source_id="venue-export",
        source_version="1",
        source_run_id="source-run-001",
        source_artifact_hash="b" * 64,
        collector_id="collector-a",
        collector_version="1",
        venue_id="polymarket",
        acquisition_mode="read-only-export",
        status=QUALIFIED,
        reasons=(),
        policy_id="source-policy",
        policy_version="1",
        policy_hash="c" * 64,
        artifact_hash="PENDING",
    )
    return replace(
        provisional,
        artifact_hash=measured_venue_source_qualification_hash(
            provisional
        ),
    )


def unsigned_attestation(q):
    return build_measured_venue_acquisition_attestation(
        q,
        attestation_run_id="attestation-run-001",
        signer_id="collector-signing-authority",
        key_id="collector-key-001",
        signature_algorithm=ED25519,
        signature_b64=base64.b64encode(
            bytes(64)
        ).decode("ascii"),
    )


def signed_fixture():
    q = qualification()
    private_key = Ed25519PrivateKey.generate()

    unsigned = unsigned_attestation(q)
    signature = private_key.sign(
        measured_venue_ed25519_signing_message(unsigned)
    )

    with_signature = replace(
        unsigned,
        signature_b64=base64.b64encode(
            signature
        ).decode("ascii"),
        artifact_hash="PENDING",
    )
    attestation = replace(
        with_signature,
        artifact_hash=(
            measured_venue_acquisition_attestation_artifact_hash(
                with_signature
            )
        ),
    )

    public_key_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    rule = MeasuredVenueTrustedPublicKeyRule(
        signer_id=attestation.signer_id,
        key_id=attestation.key_id,
        signature_algorithm=ED25519,
        public_key_b64=base64.b64encode(
            public_key_raw
        ).decode("ascii"),
        collector_id=attestation.collector_id,
        collector_version=attestation.collector_version,
        venue_id=attestation.venue_id,
        acquisition_mode=attestation.acquisition_mode,
    )

    policy = MeasuredVenueTrustedPublicKeyPolicy(
        policy_id="trusted-key-policy",
        version="1",
        rules=(rule,),
    )

    return q, attestation, policy


def test_contract_symbols_and_schema():
    assert SIGNATURE_VERIFIED == "SIGNATURE_VERIFIED"
    assert (
        BLOCKED_SIGNATURE_VERIFICATION
        == "BLOCKED_SIGNATURE_VERIFICATION"
    )
    assert (
        MEASURED_VENUE_ED25519_VERIFICATION_SCHEMA_V1
        == "measured_venue_ed25519_verification_v1"
    )


def test_signing_message_is_domain_separated_payload_hash():
    q = qualification()
    attestation = unsigned_attestation(q)

    assert measured_venue_ed25519_signing_message(
        attestation
    ) == DOMAIN + bytes.fromhex(attestation.payload_hash)


def test_valid_signature_with_trusted_key_is_verified():
    q, attestation, policy = signed_fixture()

    result = verify_measured_venue_ed25519_signature(
        attestation,
        q,
        policy,
        verification_run_id="verification-run-001",
    )

    assert result.status == SIGNATURE_VERIFIED
    assert result.reasons == ()
    assert result.attestation_artifact_hash == attestation.artifact_hash
    assert result.attestation_payload_hash == attestation.payload_hash
    assert result.qualification_artifact_hash == q.artifact_hash
    assert result.signer_id == attestation.signer_id
    assert result.key_id == attestation.key_id
    assert (
        measured_venue_ed25519_verification_artifact_hash(
            result
        )
        == result.artifact_hash
    )


def test_invalid_signature_is_blocked():
    q, attestation, policy = signed_fixture()

    bad = replace(
        attestation,
        signature_b64=base64.b64encode(
            bytes(64)
        ).decode("ascii"),
        artifact_hash="PENDING",
    )
    bad = replace(
        bad,
        artifact_hash=(
            measured_venue_acquisition_attestation_artifact_hash(
                bad
            )
        ),
    )

    result = verify_measured_venue_ed25519_signature(
        bad,
        q,
        policy,
        verification_run_id="verification-run-001",
    )

    assert result.status == BLOCKED_SIGNATURE_VERIFICATION
    assert "ED25519_SIGNATURE_INVALID" in result.reasons


def test_untrusted_key_identity_is_blocked():
    q, attestation, policy = signed_fixture()

    changed = replace(
        attestation,
        key_id="unknown-key",
        artifact_hash="PENDING",
    )
    changed = replace(
        changed,
        artifact_hash=(
            measured_venue_acquisition_attestation_artifact_hash(
                changed
            )
        ),
    )

    result = verify_measured_venue_ed25519_signature(
        changed,
        q,
        policy,
        verification_run_id="verification-run-001",
    )

    assert result.status == BLOCKED_SIGNATURE_VERIFICATION
    assert "TRUSTED_PUBLIC_KEY_NOT_FOUND" in result.reasons


def test_tampered_attestation_cannot_be_verified():
    q, attestation, policy = signed_fixture()

    tampered = replace(
        attestation,
        source_run_id="forged-source-run",
    )

    result = verify_measured_venue_ed25519_signature(
        tampered,
        q,
        policy,
        verification_run_id="verification-run-001",
    )

    assert result.status == BLOCKED_SIGNATURE_VERIFICATION
    assert "ATTESTATION_INVALID" in result.reasons


def test_verification_run_id_is_nonsemantic():
    q, attestation, policy = signed_fixture()

    first = verify_measured_venue_ed25519_signature(
        attestation,
        q,
        policy,
        verification_run_id="verification-run-001",
    )
    second = replace(
        first,
        verification_run_id="verification-run-002",
    )

    assert (
        measured_venue_ed25519_verification_artifact_hash(
            first
        )
        == measured_venue_ed25519_verification_artifact_hash(
            second
        )
    )


def test_independent_verifier_accepts_genuine_verified_artifact():
    q, attestation, policy = signed_fixture()

    result = verify_measured_venue_ed25519_signature(
        attestation,
        q,
        policy,
        verification_run_id="verification-run-001",
    )

    assert (
        verify_measured_venue_ed25519_verification_artifact(
            result,
            attestation,
            q,
            policy,
        )
        == ()
    )


def test_independent_verifier_rejects_rehashed_forged_positive():
    q, attestation, policy = signed_fixture()

    bad_attestation = replace(
        attestation,
        signature_b64=base64.b64encode(
            bytes(64)
        ).decode("ascii"),
        artifact_hash="PENDING",
    )
    bad_attestation = replace(
        bad_attestation,
        artifact_hash=(
            measured_venue_acquisition_attestation_artifact_hash(
                bad_attestation
            )
        ),
    )

    genuine = verify_measured_venue_ed25519_signature(
        attestation,
        q,
        policy,
        verification_run_id="verification-run-001",
    )

    forged = replace(
        genuine,
        attestation_artifact_hash=bad_attestation.artifact_hash,
        status=SIGNATURE_VERIFIED,
        reasons=(),
        artifact_hash="PENDING",
    )
    forged = replace(
        forged,
        artifact_hash=(
            measured_venue_ed25519_verification_artifact_hash(
                forged
            )
        ),
    )

    reasons = verify_measured_venue_ed25519_verification_artifact(
        forged,
        bad_attestation,
        q,
        policy,
    )

    assert "ED25519_SIGNATURE_INVALID" in reasons


def test_adversarial_untrusted_key_does_not_invent_public_key_material():
    q, attestation, policy = signed_fixture()

    changed = replace(
        attestation,
        key_id="unknown-key",
        artifact_hash="PENDING",
    )
    changed = replace(
        changed,
        artifact_hash=(
            measured_venue_acquisition_attestation_artifact_hash(
                changed
            )
        ),
    )

    result = verify_measured_venue_ed25519_signature(
        changed,
        q,
        policy,
        verification_run_id="verification-run-adv-001",
    )

    assert result.status == BLOCKED_SIGNATURE_VERIFICATION
    assert "TRUSTED_PUBLIC_KEY_NOT_FOUND" in result.reasons
    assert result.public_key_b64 is None


def test_adversarial_wrong_trusted_public_key_blocks_signature():
    q, attestation, policy = signed_fixture()

    wrong_private_key = Ed25519PrivateKey.generate()
    wrong_public_key_raw = (
        wrong_private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )

    wrong_rule = replace(
        policy.rules[0],
        public_key_b64=base64.b64encode(
            wrong_public_key_raw
        ).decode("ascii"),
    )
    wrong_policy = MeasuredVenueTrustedPublicKeyPolicy(
        policy_id=policy.policy_id,
        version=policy.version,
        rules=(wrong_rule,),
    )

    result = verify_measured_venue_ed25519_signature(
        attestation,
        q,
        wrong_policy,
        verification_run_id="verification-run-adv-002",
    )

    assert result.status == BLOCKED_SIGNATURE_VERIFICATION
    assert "ED25519_SIGNATURE_INVALID" in result.reasons


def test_adversarial_trusted_key_resolution_has_no_context_fallback():
    q, attestation, policy = signed_fixture()

    mismatched_rule = replace(
        policy.rules[0],
        venue_id="different-venue",
    )
    mismatched_policy = MeasuredVenueTrustedPublicKeyPolicy(
        policy_id=policy.policy_id,
        version=policy.version,
        rules=(mismatched_rule,),
    )

    result = verify_measured_venue_ed25519_signature(
        attestation,
        q,
        mismatched_policy,
        verification_run_id="verification-run-adv-003",
    )

    assert result.status == BLOCKED_SIGNATURE_VERIFICATION
    assert "TRUSTED_PUBLIC_KEY_NOT_FOUND" in result.reasons


def test_adversarial_blocked_attestation_cannot_be_laundered():
    from std0_quant.execution.measured_venue_acquisition_attestation import (
        BLOCKED_ATTESTATION,
    )

    q, attestation, policy = signed_fixture()

    blocked = replace(
        attestation,
        status=BLOCKED_ATTESTATION,
        reasons=("UPSTREAM_ATTESTATION_BLOCKED",),
        artifact_hash="PENDING",
    )
    blocked = replace(
        blocked,
        artifact_hash=(
            measured_venue_acquisition_attestation_artifact_hash(
                blocked
            )
        ),
    )

    result = verify_measured_venue_ed25519_signature(
        blocked,
        q,
        policy,
        verification_run_id="verification-run-adv-004",
    )

    assert result.status == BLOCKED_SIGNATURE_VERIFICATION
    assert "ATTESTATION_INVALID" in result.reasons
    assert "ATTESTATION_BLOCKED" in result.reasons
    assert "UPSTREAM_ATTESTATION_BLOCKED" in result.reasons


def test_adversarial_independent_verifier_detects_policy_swap():
    q, attestation, policy = signed_fixture()

    result = verify_measured_venue_ed25519_signature(
        attestation,
        q,
        policy,
        verification_run_id="verification-run-adv-005",
    )

    changed_policy = MeasuredVenueTrustedPublicKeyPolicy(
        policy_id=policy.policy_id,
        version="2",
        rules=policy.rules,
    )

    reasons = verify_measured_venue_ed25519_verification_artifact(
        result,
        attestation,
        q,
        changed_policy,
    )

    assert "VERIFICATION_TRUSTED_KEY_POLICY_VERSION_MISMATCH" in reasons
    assert "VERIFICATION_TRUSTED_KEY_POLICY_HASH_MISMATCH" in reasons


def test_adversarial_rehashed_forged_public_key_binding_is_rejected():
    q, attestation, policy = signed_fixture()

    genuine = verify_measured_venue_ed25519_signature(
        attestation,
        q,
        policy,
        verification_run_id="verification-run-adv-006",
    )

    forged = replace(
        genuine,
        public_key_b64=base64.b64encode(
            bytes(32)
        ).decode("ascii"),
        artifact_hash="PENDING",
    )
    forged = replace(
        forged,
        artifact_hash=(
            measured_venue_ed25519_verification_artifact_hash(
                forged
            )
        ),
    )

    reasons = verify_measured_venue_ed25519_verification_artifact(
        forged,
        attestation,
        q,
        policy,
    )

    assert "VERIFICATION_TRUSTED_PUBLIC_KEY_MISMATCH" in reasons


def test_adversarial_tampered_payload_hash_cannot_be_laundered():
    q, attestation, policy = signed_fixture()

    tampered = replace(
        attestation,
        payload_hash="c" * 64,
        artifact_hash="PENDING",
    )
    tampered = replace(
        tampered,
        artifact_hash=(
            measured_venue_acquisition_attestation_artifact_hash(
                tampered
            )
        ),
    )

    result = verify_measured_venue_ed25519_signature(
        tampered,
        q,
        policy,
        verification_run_id="verification-run-adv-007",
    )

    assert result.status == BLOCKED_SIGNATURE_VERIFICATION
    assert "ATTESTATION_INVALID" in result.reasons
    assert "ATTESTATION_PAYLOAD_HASH_MISMATCH" in result.reasons


def test_adversarial_independent_verifier_detects_artifact_hash_tamper():
    q, attestation, policy = signed_fixture()

    genuine = verify_measured_venue_ed25519_signature(
        attestation,
        q,
        policy,
        verification_run_id="verification-run-adv-008",
    )

    tampered = replace(
        genuine,
        artifact_hash="d" * 64,
    )

    reasons = verify_measured_venue_ed25519_verification_artifact(
        tampered,
        attestation,
        q,
        policy,
    )

    assert "ED25519_VERIFICATION_ARTIFACT_HASH_MISMATCH" in reasons


def test_adversarial_verified_artifact_requires_resolved_public_key():
    import pytest

    q, attestation, policy = signed_fixture()

    genuine = verify_measured_venue_ed25519_signature(
        attestation,
        q,
        policy,
        verification_run_id="verification-run-adv2-001",
    )

    assert genuine.status == SIGNATURE_VERIFIED

    with pytest.raises(ValueError):
        replace(
            genuine,
            public_key_b64=None,
        )


def test_adversarial_qualification_run_id_change_is_detected_even_when_hash_is_same():
    q, attestation, policy = signed_fixture()

    changed_q = replace(
        q,
        qualification_run_id="qualification-run-other",
    )

    assert (
        measured_venue_source_qualification_hash(changed_q)
        == q.artifact_hash
    )
    assert changed_q.artifact_hash == q.artifact_hash

    result = verify_measured_venue_ed25519_signature(
        attestation,
        changed_q,
        policy,
        verification_run_id="verification-run-adv2-002",
    )

    assert result.status == BLOCKED_SIGNATURE_VERIFICATION
    assert "ATTESTATION_INVALID" in result.reasons
    assert (
        "ATTESTATION_QUALIFICATION_RUN_ID_MISMATCH"
        in result.reasons
    )


def test_adversarial_signer_identity_is_cryptographically_bound():
    from std0_quant.execution.measured_venue_acquisition_attestation import (
        measured_venue_acquisition_attestation_payload_hash,
    )

    q, attestation, policy = signed_fixture()

    changed = replace(
        attestation,
        signer_id="different-signing-authority",
        payload_hash="PENDING",
        artifact_hash="PENDING",
    )
    changed = replace(
        changed,
        payload_hash=(
            measured_venue_acquisition_attestation_payload_hash(
                changed
            )
        ),
    )
    changed = replace(
        changed,
        artifact_hash=(
            measured_venue_acquisition_attestation_artifact_hash(
                changed
            )
        ),
    )

    changed_rule = replace(
        policy.rules[0],
        signer_id=changed.signer_id,
    )
    changed_policy = MeasuredVenueTrustedPublicKeyPolicy(
        policy_id=policy.policy_id,
        version=policy.version,
        rules=(changed_rule,),
    )

    result = verify_measured_venue_ed25519_signature(
        changed,
        q,
        changed_policy,
        verification_run_id="verification-run-adv2-003",
    )

    assert result.status == BLOCKED_SIGNATURE_VERIFICATION
    assert "ED25519_SIGNATURE_INVALID" in result.reasons
    assert "TRUSTED_PUBLIC_KEY_NOT_FOUND" not in result.reasons


def test_adversarial_acquisition_context_is_cryptographically_bound():
    from std0_quant.execution.measured_venue_acquisition_attestation import (
        measured_venue_acquisition_attestation_payload_hash,
    )

    q, attestation, policy = signed_fixture()

    changed_q = replace(
        q,
        venue_id="different-venue",
        artifact_hash="PENDING",
    )
    changed_q = replace(
        changed_q,
        artifact_hash=(
            measured_venue_source_qualification_hash(
                changed_q
            )
        ),
    )

    changed = replace(
        attestation,
        qualification_artifact_hash=changed_q.artifact_hash,
        venue_id=changed_q.venue_id,
        payload_hash="PENDING",
        artifact_hash="PENDING",
    )
    changed = replace(
        changed,
        payload_hash=(
            measured_venue_acquisition_attestation_payload_hash(
                changed
            )
        ),
    )
    changed = replace(
        changed,
        artifact_hash=(
            measured_venue_acquisition_attestation_artifact_hash(
                changed
            )
        ),
    )

    changed_rule = replace(
        policy.rules[0],
        venue_id=changed_q.venue_id,
    )
    changed_policy = MeasuredVenueTrustedPublicKeyPolicy(
        policy_id=policy.policy_id,
        version=policy.version,
        rules=(changed_rule,),
    )

    result = verify_measured_venue_ed25519_signature(
        changed,
        changed_q,
        changed_policy,
        verification_run_id="verification-run-adv2-004",
    )

    assert result.status == BLOCKED_SIGNATURE_VERIFICATION
    assert "ATTESTATION_INVALID" not in result.reasons
    assert "ED25519_SIGNATURE_INVALID" in result.reasons


def test_adversarial_independent_verifier_detects_nonsemantic_qualification_run_swap():
    q, attestation, policy = signed_fixture()

    genuine = verify_measured_venue_ed25519_signature(
        attestation,
        q,
        policy,
        verification_run_id="verification-run-adv2-005",
    )

    changed_q = replace(
        q,
        qualification_run_id="qualification-run-other",
    )

    assert changed_q.artifact_hash == q.artifact_hash

    reasons = verify_measured_venue_ed25519_verification_artifact(
        genuine,
        attestation,
        changed_q,
        policy,
    )

    assert (
        "ATTESTATION_QUALIFICATION_RUN_ID_MISMATCH"
        in reasons
    )
    assert "VERIFICATION_STATUS_MISMATCH" in reasons


def test_adversarial_corrupted_trusted_public_key_fails_closed():
    q, attestation, policy = signed_fixture()

    corrupted_rule = replace(policy.rules[0])
    object.__setattr__(
        corrupted_rule,
        "public_key_b64",
        "not-valid-base64!!!",
    )

    corrupted_policy = MeasuredVenueTrustedPublicKeyPolicy(
        policy_id=policy.policy_id,
        version=policy.version,
        rules=(corrupted_rule,),
    )

    result = verify_measured_venue_ed25519_signature(
        attestation,
        q,
        corrupted_policy,
        verification_run_id="verification-run-adv3-001",
    )

    assert result.status == BLOCKED_SIGNATURE_VERIFICATION
    assert "ED25519_KEY_OR_SIGNATURE_INVALID" in result.reasons
    assert result.public_key_b64 is None


def test_adversarial_corrupted_signature_encoding_fails_closed():
    q, attestation, policy = signed_fixture()

    corrupted = replace(attestation)
    object.__setattr__(
        corrupted,
        "signature_b64",
        "not-valid-base64!!!",
    )
    object.__setattr__(
        corrupted,
        "artifact_hash",
        measured_venue_acquisition_attestation_artifact_hash(
            corrupted
        ),
    )

    result = verify_measured_venue_ed25519_signature(
        corrupted,
        q,
        policy,
        verification_run_id="verification-run-adv3-002",
    )

    assert result.status == BLOCKED_SIGNATURE_VERIFICATION
    assert "ED25519_KEY_OR_SIGNATURE_INVALID" in result.reasons


def test_adversarial_wrong_signature_length_fails_closed():
    q, attestation, policy = signed_fixture()

    corrupted = replace(attestation)
    object.__setattr__(
        corrupted,
        "signature_b64",
        base64.b64encode(bytes(63)).decode("ascii"),
    )
    object.__setattr__(
        corrupted,
        "artifact_hash",
        measured_venue_acquisition_attestation_artifact_hash(
            corrupted
        ),
    )

    result = verify_measured_venue_ed25519_signature(
        corrupted,
        q,
        policy,
        verification_run_id="verification-run-adv3-003",
    )

    assert result.status == BLOCKED_SIGNATURE_VERIFICATION
    assert "ED25519_SIGNATURE_INVALID" in result.reasons


def test_adversarial_genuine_untrusted_key_blocked_artifact_verifies_cleanly():
    from std0_quant.execution.measured_venue_acquisition_attestation import (
        measured_venue_acquisition_attestation_payload_hash,
    )

    q, _, policy = signed_fixture()
    private_key = Ed25519PrivateKey.generate()

    changed = unsigned_attestation(q)
    changed = replace(
        changed,
        key_id="unknown-key",
        payload_hash="PENDING",
        artifact_hash="PENDING",
    )
    changed = replace(
        changed,
        payload_hash=(
            measured_venue_acquisition_attestation_payload_hash(
                changed
            )
        ),
    )

    signature = private_key.sign(
        measured_venue_ed25519_signing_message(changed)
    )
    changed = replace(
        changed,
        signature_b64=base64.b64encode(
            signature
        ).decode("ascii"),
        artifact_hash="PENDING",
    )
    changed = replace(
        changed,
        artifact_hash=(
            measured_venue_acquisition_attestation_artifact_hash(
                changed
            )
        ),
    )

    blocked = verify_measured_venue_ed25519_signature(
        changed,
        q,
        policy,
        verification_run_id="verification-run-adv3-004",
    )

    assert blocked.status == BLOCKED_SIGNATURE_VERIFICATION
    assert blocked.reasons == (
        "TRUSTED_PUBLIC_KEY_NOT_FOUND",
    )
    assert blocked.public_key_b64 is None

    assert (
        verify_measured_venue_ed25519_verification_artifact(
            blocked,
            changed,
            q,
            policy,
        )
        == ()
    )


def test_adversarial_rehashed_forged_status_and_reasons_are_rejected():
    q, attestation, policy = signed_fixture()

    genuine = verify_measured_venue_ed25519_signature(
        attestation,
        q,
        policy,
        verification_run_id="verification-run-adv3-005",
    )

    forged = replace(
        genuine,
        status=BLOCKED_SIGNATURE_VERIFICATION,
        reasons=("FORGED_BLOCKED_RESULT",),
        artifact_hash="PENDING",
    )
    forged = replace(
        forged,
        artifact_hash=(
            measured_venue_ed25519_verification_artifact_hash(
                forged
            )
        ),
    )

    reasons = verify_measured_venue_ed25519_verification_artifact(
        forged,
        attestation,
        q,
        policy,
    )

    assert "VERIFICATION_STATUS_MISMATCH" in reasons
    assert "VERIFICATION_REASONS_MISMATCH" in reasons


def test_adversarial_same_policy_identity_with_changed_key_material_is_detected():
    q, attestation, policy = signed_fixture()

    genuine = verify_measured_venue_ed25519_signature(
        attestation,
        q,
        policy,
        verification_run_id="verification-run-adv3-006",
    )

    wrong_private_key = Ed25519PrivateKey.generate()
    wrong_public_key_raw = (
        wrong_private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )

    changed_rule = replace(
        policy.rules[0],
        public_key_b64=base64.b64encode(
            wrong_public_key_raw
        ).decode("ascii"),
    )
    changed_policy = MeasuredVenueTrustedPublicKeyPolicy(
        policy_id=policy.policy_id,
        version=policy.version,
        rules=(changed_rule,),
    )

    reasons = verify_measured_venue_ed25519_verification_artifact(
        genuine,
        attestation,
        q,
        changed_policy,
    )

    assert "VERIFICATION_TRUSTED_KEY_POLICY_HASH_MISMATCH" in reasons
    assert "VERIFICATION_TRUSTED_PUBLIC_KEY_MISMATCH" in reasons
    assert "ED25519_SIGNATURE_INVALID" in reasons
    assert "VERIFICATION_STATUS_MISMATCH" in reasons
