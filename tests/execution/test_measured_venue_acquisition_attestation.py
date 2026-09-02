import base64
from dataclasses import replace

import pytest

from std0_quant.execution.execution_validation import BLOCKED
from std0_quant.execution.measured_venue_source_qualification import (
    QUALIFIED,
    MeasuredVenueSourceQualification,
    measured_venue_source_qualification_hash,
)
from std0_quant.execution.measured_venue_acquisition_attestation import (
    BLOCKED_ATTESTATION,
    ED25519,
    PENDING_SIGNATURE_VERIFICATION,
    VERIFIED,
    MEASURED_VENUE_ACQUISITION_ATTESTATION_SCHEMA_V1,
    MeasuredVenueAcquisitionAttestation,
    build_measured_venue_acquisition_attestation,
    measured_venue_acquisition_attestation_artifact_hash,
    measured_venue_acquisition_attestation_payload_hash,
)


def qualification(**changes):
    values = {
        "qualification_run_id": "qualification-run-001",
        "evidence_run_id": "measured-run-001",
        "evidence_artifact_hash": "a" * 64,
        "source_id": "venue-export",
        "source_version": "1",
        "source_run_id": "source-run-001",
        "source_artifact_hash": "b" * 64,
        "collector_id": "collector-a",
        "collector_version": "1",
        "venue_id": "polymarket",
        "acquisition_mode": "read-only-export",
        "status": QUALIFIED,
        "reasons": (),
        "policy_id": "measured-source-policy",
        "policy_version": "1",
        "policy_hash": "c" * 64,
        "artifact_hash": "PENDING",
    }
    values.update(changes)

    provisional = MeasuredVenueSourceQualification(**values)
    return replace(
        provisional,
        artifact_hash=measured_venue_source_qualification_hash(
            provisional
        ),
    )


def build(q=None, **changes):
    values = {
        "attestation_run_id": "attestation-run-001",
        "signer_id": "collector-signing-authority",
        "key_id": "collector-key-001",
        "signature_algorithm": ED25519,
        "signature_b64": base64.b64encode(bytes(64)).decode("ascii"),
    }
    values.update(changes)

    return build_measured_venue_acquisition_attestation(
        q or qualification(),
        **values,
    )


def test_contract_symbols_and_schema():
    assert ED25519 == "ED25519"
    assert VERIFIED == "VERIFIED"
    assert (
        PENDING_SIGNATURE_VERIFICATION
        == "PENDING_SIGNATURE_VERIFICATION"
    )
    assert (
        MEASURED_VENUE_ACQUISITION_ATTESTATION_SCHEMA_V1
        == "measured_venue_acquisition_attestation_v1"
    )


def test_valid_qualified_source_builds_pending_signature_attestation():
    q = qualification()
    artifact = build(q)

    assert artifact.status == PENDING_SIGNATURE_VERIFICATION
    assert artifact.reasons == ()
    assert artifact.qualification_run_id == q.qualification_run_id
    assert artifact.qualification_artifact_hash == q.artifact_hash
    assert artifact.evidence_artifact_hash == q.evidence_artifact_hash
    assert artifact.source_run_id == q.source_run_id
    assert artifact.source_artifact_hash == q.source_artifact_hash
    assert artifact.collector_id == q.collector_id
    assert artifact.collector_version == q.collector_version
    assert artifact.venue_id == q.venue_id
    assert artifact.acquisition_mode == q.acquisition_mode
    assert (
        measured_venue_acquisition_attestation_payload_hash(
            artifact
        )
        == artifact.payload_hash
    )
    assert (
        measured_venue_acquisition_attestation_artifact_hash(
            artifact
        )
        == artifact.artifact_hash
    )


def test_tampered_qualification_hash_is_blocked():
    q = replace(
        qualification(),
        artifact_hash="0" * 64,
    )

    artifact = build(q)

    assert artifact.status == BLOCKED_ATTESTATION
    assert (
        "QUALIFICATION_ARTIFACT_HASH_MISMATCH"
        in artifact.reasons
    )


def test_blocked_qualification_cannot_be_washed():
    q = qualification()
    provisional = replace(
        q,
        status=BLOCKED,
        reasons=("UPSTREAM_BLOCKED",),
        artifact_hash="PENDING",
    )
    blocked = replace(
        provisional,
        artifact_hash=measured_venue_source_qualification_hash(
            provisional
        ),
    )

    artifact = build(blocked)

    assert artifact.status == BLOCKED_ATTESTATION
    assert "QUALIFICATION_BLOCKED" in artifact.reasons
    assert "UPSTREAM_BLOCKED" in artifact.reasons


def test_only_ed25519_is_allowed_as_signature_contract():
    with pytest.raises(ValueError):
        build(signature_algorithm="HMAC-SHA256")


def test_signature_must_be_strict_base64_text():
    with pytest.raises(ValueError):
        build(signature_b64="not base64 !!!")


def test_payload_hash_binds_qualification_artifact_hash():
    artifact = build()

    changed = replace(
        artifact,
        qualification_artifact_hash="d" * 64,
    )

    assert (
        measured_venue_acquisition_attestation_payload_hash(
            changed
        )
        != artifact.payload_hash
    )


def test_payload_hash_binds_source_run_id():
    artifact = build()

    changed = replace(
        artifact,
        source_run_id="different-source-run",
    )

    assert (
        measured_venue_acquisition_attestation_payload_hash(
            changed
        )
        != artifact.payload_hash
    )


def test_payload_hash_binds_signer_and_key_identity():
    artifact = build()

    changed_signer = replace(
        artifact,
        signer_id="different-signer",
    )
    changed_key = replace(
        artifact,
        key_id="different-key",
    )

    assert (
        measured_venue_acquisition_attestation_payload_hash(
            changed_signer
        )
        != artifact.payload_hash
    )
    assert (
        measured_venue_acquisition_attestation_payload_hash(
            changed_key
        )
        != artifact.payload_hash
    )


def test_artifact_hash_binds_signature_but_payload_hash_does_not():
    artifact = build()

    changed = replace(
        artifact,
        signature_b64=base64.b64encode(bytes([1]) * 64).decode("ascii"),
    )

    assert (
        measured_venue_acquisition_attestation_payload_hash(
            changed
        )
        == artifact.payload_hash
    )
    assert (
        measured_venue_acquisition_attestation_artifact_hash(
            changed
        )
        != artifact.artifact_hash
    )


def test_attestation_run_id_is_nonsemantic():
    artifact = build()

    changed = replace(
        artifact,
        attestation_run_id="different-attestation-run",
    )

    assert (
        measured_venue_acquisition_attestation_payload_hash(
            changed
        )
        == artifact.payload_hash
    )
    assert (
        measured_venue_acquisition_attestation_artifact_hash(
            changed
        )
        == artifact.artifact_hash
    )


def test_verified_is_unreachable_without_crypto_backend():
    with pytest.raises(ValueError):
        MeasuredVenueAcquisitionAttestation(
            attestation_run_id="attestation-run",
            qualification_run_id="qualification-run",
            qualification_artifact_hash="a" * 64,
            evidence_artifact_hash="b" * 64,
            source_run_id="source-run",
            source_artifact_hash="c" * 64,
            collector_id="collector-a",
            collector_version="1",
            venue_id="polymarket",
            acquisition_mode="read-only-export",
            signer_id="collector-signing-authority",
            key_id="collector-key-001",
            signature_algorithm=ED25519,
            signature_b64=base64.b64encode(bytes(64)).decode("ascii"),
            status=VERIFIED,
            reasons=(),
            payload_hash="d" * 64,
            artifact_hash="e" * 64,
        )


def test_noncanonical_base64_signature_is_rejected():
    # AB== decodes, but canonical encoding of the resulting byte is AA==.
    with pytest.raises(ValueError):
        build(signature_b64="AB==")


def test_invalid_source_artifact_hash_in_qualification_is_blocked():
    q = qualification(
        source_artifact_hash="not-a-sha256",
    )

    artifact = build(q)

    assert artifact.status == BLOCKED_ATTESTATION
    assert (
        "QUALIFICATION_SOURCE_ARTIFACT_HASH_INVALID"
        in artifact.reasons
    )


def test_invalid_evidence_artifact_hash_in_qualification_is_blocked():
    q = qualification(
        evidence_artifact_hash="not-a-sha256",
    )

    artifact = build(q)

    assert artifact.status == BLOCKED_ATTESTATION
    assert (
        "QUALIFICATION_EVIDENCE_ARTIFACT_HASH_INVALID"
        in artifact.reasons
    )


def test_verifier_accepts_builder_produced_attestation():
    from std0_quant.execution.measured_venue_acquisition_attestation import (
        verify_measured_venue_acquisition_attestation,
    )

    q = qualification()
    artifact = build(q)

    assert (
        verify_measured_venue_acquisition_attestation(
            artifact,
            q,
        )
        == ()
    )


def test_verifier_detects_rehashed_source_run_rebinding():
    from std0_quant.execution.measured_venue_acquisition_attestation import (
        verify_measured_venue_acquisition_attestation,
    )

    q = qualification()
    artifact = build(q)

    provisional = replace(
        artifact,
        source_run_id="forged-source-run",
        payload_hash="PENDING",
        artifact_hash="PENDING",
    )
    payload_hash = (
        measured_venue_acquisition_attestation_payload_hash(
            provisional
        )
    )
    with_payload = replace(
        provisional,
        payload_hash=payload_hash,
    )
    forged = replace(
        with_payload,
        artifact_hash=(
            measured_venue_acquisition_attestation_artifact_hash(
                with_payload
            )
        ),
    )

    reasons = verify_measured_venue_acquisition_attestation(
        forged,
        q,
    )

    assert "ATTESTATION_SOURCE_RUN_ID_MISMATCH" in reasons


def test_verifier_detects_qualification_artifact_rebinding():
    from std0_quant.execution.measured_venue_acquisition_attestation import (
        verify_measured_venue_acquisition_attestation,
    )

    q = qualification()
    artifact = build(q)

    provisional = replace(
        artifact,
        qualification_artifact_hash="f" * 64,
        payload_hash="PENDING",
        artifact_hash="PENDING",
    )
    payload_hash = (
        measured_venue_acquisition_attestation_payload_hash(
            provisional
        )
    )
    with_payload = replace(
        provisional,
        payload_hash=payload_hash,
    )
    forged = replace(
        with_payload,
        artifact_hash=(
            measured_venue_acquisition_attestation_artifact_hash(
                with_payload
            )
        ),
    )

    reasons = verify_measured_venue_acquisition_attestation(
        forged,
        q,
    )

    assert (
        "ATTESTATION_QUALIFICATION_ARTIFACT_HASH_MISMATCH"
        in reasons
    )


def test_ed25519_signature_requires_exactly_64_decoded_bytes():
    with pytest.raises(ValueError):
        build(
            signature_b64=base64.b64encode(
                bytes(63)
            ).decode("ascii")
        )

    with pytest.raises(ValueError):
        build(
            signature_b64=base64.b64encode(
                bytes(65)
            ).decode("ascii")
        )


def test_verifier_detects_signature_tampering_without_artifact_rehash():
    from std0_quant.execution.measured_venue_acquisition_attestation import (
        verify_measured_venue_acquisition_attestation,
    )

    q = qualification()
    artifact = build(q)

    tampered = replace(
        artifact,
        signature_b64=base64.b64encode(
            bytes([1]) * 64
        ).decode("ascii"),
    )

    reasons = verify_measured_venue_acquisition_attestation(
        tampered,
        q,
    )

    assert "ATTESTATION_ARTIFACT_HASH_MISMATCH" in reasons


def test_verifier_detects_payload_rebinding_with_rehashed_artifact():
    from std0_quant.execution.measured_venue_acquisition_attestation import (
        verify_measured_venue_acquisition_attestation,
    )

    q = qualification()
    artifact = build(q)

    provisional = replace(
        artifact,
        evidence_artifact_hash="d" * 64,
        artifact_hash="PENDING",
    )
    forged = replace(
        provisional,
        artifact_hash=(
            measured_venue_acquisition_attestation_artifact_hash(
                provisional
            )
        ),
    )

    reasons = verify_measured_venue_acquisition_attestation(
        forged,
        q,
    )

    assert "ATTESTATION_PAYLOAD_HASH_MISMATCH" in reasons
    assert (
        "ATTESTATION_EVIDENCE_ARTIFACT_HASH_MISMATCH"
        in reasons
    )


def test_verifier_detects_rehashed_evidence_hash_rebinding():
    from std0_quant.execution.measured_venue_acquisition_attestation import (
        verify_measured_venue_acquisition_attestation,
    )

    q = qualification()
    artifact = build(q)

    provisional = replace(
        artifact,
        evidence_artifact_hash="d" * 64,
        payload_hash="PENDING",
        artifact_hash="PENDING",
    )
    payload_hash = (
        measured_venue_acquisition_attestation_payload_hash(
            provisional
        )
    )
    with_payload = replace(
        provisional,
        payload_hash=payload_hash,
    )
    forged = replace(
        with_payload,
        artifact_hash=(
            measured_venue_acquisition_attestation_artifact_hash(
                with_payload
            )
        ),
    )

    reasons = verify_measured_venue_acquisition_attestation(
        forged,
        q,
    )

    assert (
        "ATTESTATION_EVIDENCE_ARTIFACT_HASH_MISMATCH"
        in reasons
    )


def test_verifier_rejects_rehashed_blocked_qualification_laundering():
    from std0_quant.execution.measured_venue_acquisition_attestation import (
        verify_measured_venue_acquisition_attestation,
    )

    q = qualification()
    provisional_q = replace(
        q,
        status=BLOCKED,
        reasons=("UPSTREAM_BLOCKED",),
        artifact_hash="PENDING",
    )
    blocked_q = replace(
        provisional_q,
        artifact_hash=measured_venue_source_qualification_hash(
            provisional_q
        ),
    )

    blocked_attestation = build(blocked_q)

    provisional = replace(
        blocked_attestation,
        status=PENDING_SIGNATURE_VERIFICATION,
        reasons=(),
        payload_hash="PENDING",
        artifact_hash="PENDING",
    )
    payload_hash = (
        measured_venue_acquisition_attestation_payload_hash(
            provisional
        )
    )
    with_payload = replace(
        provisional,
        payload_hash=payload_hash,
    )
    forged = replace(
        with_payload,
        artifact_hash=(
            measured_venue_acquisition_attestation_artifact_hash(
                with_payload
            )
        ),
    )

    reasons = verify_measured_venue_acquisition_attestation(
        forged,
        blocked_q,
    )

    assert "QUALIFICATION_BLOCKED" in reasons
    assert "UPSTREAM_BLOCKED" in reasons
