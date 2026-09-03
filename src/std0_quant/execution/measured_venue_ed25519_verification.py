# Deterministic Ed25519 verification for measured venue attestation v1.
#
# This module cryptographically verifies an acquisition attestation against
# the frozen trusted public-key policy. SIGNATURE_VERIFIED means only that
# the trusted Ed25519 key for the exact acquisition context verified the
# canonical attestation payload hash. It does not authorize execution PASS,
# production eligibility, promotion, credentials, orders, or LIVE execution.

from __future__ import annotations

import base64
from dataclasses import dataclass, replace
import hashlib
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PublicKey,
)

from std0_quant.execution.measured_venue_acquisition_attestation import (
    ED25519,
    MeasuredVenueAcquisitionAttestation,
    verify_measured_venue_acquisition_attestation,
)
from std0_quant.execution.measured_venue_source_qualification import (
    MeasuredVenueSourceQualification,
)
from std0_quant.execution.measured_venue_trusted_public_key_policy import (
    MeasuredVenueTrustedPublicKeyPolicy,
    measured_venue_trusted_public_key_policy_hash,
    resolve_measured_venue_trusted_public_key_rule,
)
from std0_quant.storage import canonical_json


SIGNATURE_VERIFIED = "SIGNATURE_VERIFIED"
BLOCKED_SIGNATURE_VERIFICATION = "BLOCKED_SIGNATURE_VERIFICATION"

MEASURED_VENUE_ED25519_VERIFICATION_SCHEMA_V1 = (
    "measured_venue_ed25519_verification_v1"
)

_SIGNING_DOMAIN_V1 = (
    b"std0-quant/measured-venue-acquisition-attestation/v1\n"
)


def _nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    text = value.strip()
    if not text:
        raise ValueError(f"{name} must be non-empty")
    return text


def _is_sha256_hex(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(ch in "0123456789abcdef" for ch in value)


def _canonical_base64_exact_length(
    value: Any,
    name: str,
    decoded_length: int,
) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value:
        raise ValueError(f"{name} must be non-empty")
    if value != value.strip():
        raise ValueError(
            f"{name} must not contain surrounding whitespace"
        )

    try:
        decoded = base64.b64decode(
            value.encode("ascii"),
            validate=True,
        )
    except (ValueError, UnicodeEncodeError) as exc:
        raise ValueError(
            f"{name} must be strict base64 text"
        ) from exc

    if base64.b64encode(decoded).decode("ascii") != value:
        raise ValueError(
            f"{name} must use canonical base64 encoding"
        )

    if len(decoded) != decoded_length:
        raise ValueError(
            f"{name} must decode to exactly "
            f"{decoded_length} bytes"
        )

    return value


@dataclass(frozen=True)
class MeasuredVenueEd25519Verification:
    verification_run_id: str

    attestation_artifact_hash: str
    attestation_payload_hash: str
    qualification_artifact_hash: str

    signer_id: str
    key_id: str
    signature_algorithm: str
    public_key_b64: str | None

    collector_id: str
    collector_version: str
    venue_id: str
    acquisition_mode: str

    trusted_public_key_policy_id: str
    trusted_public_key_policy_version: str
    trusted_public_key_policy_hash: str

    status: str
    reasons: tuple[str, ...]

    artifact_hash: str
    schema_version: str = (
        MEASURED_VENUE_ED25519_VERIFICATION_SCHEMA_V1
    )

    def __post_init__(self) -> None:
        for name in (
            "verification_run_id",
            "attestation_artifact_hash",
            "attestation_payload_hash",
            "qualification_artifact_hash",
            "signer_id",
            "key_id",
            "collector_id",
            "collector_version",
            "venue_id",
            "acquisition_mode",
            "trusted_public_key_policy_id",
            "trusted_public_key_policy_version",
            "trusted_public_key_policy_hash",
            "artifact_hash",
        ):
            object.__setattr__(
                self,
                name,
                _nonempty(getattr(self, name), name),
            )

        if self.signature_algorithm != ED25519:
            raise ValueError(
                "measured venue Ed25519 verification v1 "
                "requires ED25519"
            )

        if self.public_key_b64 is not None:
            object.__setattr__(
                self,
                "public_key_b64",
                _canonical_base64_exact_length(
                    self.public_key_b64,
                    "public_key_b64",
                    32,
                ),
            )

        reasons = tuple(
            _nonempty(reason, "reason")
            for reason in self.reasons
        )
        object.__setattr__(self, "reasons", reasons)

        if self.status not in {
            SIGNATURE_VERIFIED,
            BLOCKED_SIGNATURE_VERIFICATION,
        }:
            raise ValueError(
                "unsupported measured venue Ed25519 "
                "verification status"
            )

        if (
            self.status == SIGNATURE_VERIFIED
            and self.public_key_b64 is None
        ):
            raise ValueError(
                "SIGNATURE_VERIFIED requires resolved public_key_b64"
            )

        if self.status == SIGNATURE_VERIFIED and reasons:
            raise ValueError(
                "SIGNATURE_VERIFIED cannot contain reasons"
            )

        if (
            self.status == BLOCKED_SIGNATURE_VERIFICATION
            and not reasons
        ):
            raise ValueError(
                "BLOCKED_SIGNATURE_VERIFICATION requires reasons"
            )

        if (
            self.schema_version
            != MEASURED_VENUE_ED25519_VERIFICATION_SCHEMA_V1
        ):
            raise ValueError(
                "unsupported MeasuredVenueEd25519Verification "
                "schema_version"
            )


def measured_venue_ed25519_signing_message(
    attestation: MeasuredVenueAcquisitionAttestation,
) -> bytes:
    if not isinstance(
        attestation,
        MeasuredVenueAcquisitionAttestation,
    ):
        raise TypeError(
            "attestation must be "
            "MeasuredVenueAcquisitionAttestation"
        )

    if not _is_sha256_hex(attestation.payload_hash):
        raise ValueError(
            "attestation payload_hash must be lowercase SHA256 hex"
        )

    return (
        _SIGNING_DOMAIN_V1
        + bytes.fromhex(attestation.payload_hash)
    )


def _verification_payload(
    artifact: MeasuredVenueEd25519Verification,
) -> dict[str, Any]:
    return {
        "attestation_artifact_hash": (
            artifact.attestation_artifact_hash
        ),
        "attestation_payload_hash": (
            artifact.attestation_payload_hash
        ),
        "qualification_artifact_hash": (
            artifact.qualification_artifact_hash
        ),
        "signer_id": artifact.signer_id,
        "key_id": artifact.key_id,
        "signature_algorithm": artifact.signature_algorithm,
        "public_key_b64": artifact.public_key_b64,
        "collector_id": artifact.collector_id,
        "collector_version": artifact.collector_version,
        "venue_id": artifact.venue_id,
        "acquisition_mode": artifact.acquisition_mode,
        "trusted_public_key_policy_id": (
            artifact.trusted_public_key_policy_id
        ),
        "trusted_public_key_policy_version": (
            artifact.trusted_public_key_policy_version
        ),
        "trusted_public_key_policy_hash": (
            artifact.trusted_public_key_policy_hash
        ),
        "status": artifact.status,
        "reasons": artifact.reasons,
        "schema_version": artifact.schema_version,
    }


def measured_venue_ed25519_verification_artifact_hash(
    artifact: MeasuredVenueEd25519Verification,
) -> str:
    if not isinstance(
        artifact,
        MeasuredVenueEd25519Verification,
    ):
        raise TypeError(
            "artifact must be MeasuredVenueEd25519Verification"
        )

    return hashlib.sha256(
        canonical_json(
            _verification_payload(artifact)
        ).encode("utf-8")
    ).hexdigest()


def _verify_signature_with_rule(
    attestation: MeasuredVenueAcquisitionAttestation,
    public_key_b64: str,
) -> tuple[str, ...]:
    reasons: list[str] = []

    try:
        public_key_raw = base64.b64decode(
            public_key_b64.encode("ascii"),
            validate=True,
        )
        signature_raw = base64.b64decode(
            attestation.signature_b64.encode("ascii"),
            validate=True,
        )
        public_key = Ed25519PublicKey.from_public_bytes(
            public_key_raw
        )
        public_key.verify(
            signature_raw,
            measured_venue_ed25519_signing_message(
                attestation
            ),
        )
    except InvalidSignature:
        reasons.append("ED25519_SIGNATURE_INVALID")
    except (
        ValueError,
        TypeError,
        UnicodeEncodeError,
    ):
        reasons.append("ED25519_KEY_OR_SIGNATURE_INVALID")

    return tuple(reasons)


def _evaluation_reasons(
    attestation: MeasuredVenueAcquisitionAttestation,
    qualification: MeasuredVenueSourceQualification,
    policy: MeasuredVenueTrustedPublicKeyPolicy,
) -> tuple[
    tuple[str, ...],
    str | None,
]:
    reasons: list[str] = []

    def add_reason(reason: str) -> None:
        if reason not in reasons:
            reasons.append(reason)

    upstream = verify_measured_venue_acquisition_attestation(
        attestation,
        qualification,
    )
    if upstream:
        add_reason("ATTESTATION_INVALID")
        for reason in upstream:
            add_reason(reason)

    rule = resolve_measured_venue_trusted_public_key_rule(
        policy,
        signer_id=attestation.signer_id,
        key_id=attestation.key_id,
        signature_algorithm=attestation.signature_algorithm,
        collector_id=attestation.collector_id,
        collector_version=attestation.collector_version,
        venue_id=attestation.venue_id,
        acquisition_mode=attestation.acquisition_mode,
    )

    if rule is None:
        add_reason("TRUSTED_PUBLIC_KEY_NOT_FOUND")
        public_key_b64 = None
    else:
        try:
            public_key_b64 = _canonical_base64_exact_length(
                rule.public_key_b64,
                "public_key_b64",
                32,
            )
        except (ValueError, TypeError):
            add_reason("ED25519_KEY_OR_SIGNATURE_INVALID")
            public_key_b64 = None
        else:
            for reason in _verify_signature_with_rule(
                attestation,
                public_key_b64,
            ):
                add_reason(reason)

    return tuple(reasons), public_key_b64


def verify_measured_venue_ed25519_signature(
    attestation: MeasuredVenueAcquisitionAttestation,
    qualification: MeasuredVenueSourceQualification,
    policy: MeasuredVenueTrustedPublicKeyPolicy,
    *,
    verification_run_id: str,
) -> MeasuredVenueEd25519Verification:
    if not isinstance(
        attestation,
        MeasuredVenueAcquisitionAttestation,
    ):
        raise TypeError(
            "attestation must be "
            "MeasuredVenueAcquisitionAttestation"
        )
    if not isinstance(
        qualification,
        MeasuredVenueSourceQualification,
    ):
        raise TypeError(
            "qualification must be "
            "MeasuredVenueSourceQualification"
        )
    if not isinstance(
        policy,
        MeasuredVenueTrustedPublicKeyPolicy,
    ):
        raise TypeError(
            "policy must be "
            "MeasuredVenueTrustedPublicKeyPolicy"
        )

    verification_run_id = _nonempty(
        verification_run_id,
        "verification_run_id",
    )

    reasons, public_key_b64 = _evaluation_reasons(
        attestation,
        qualification,
        policy,
    )

    status = (
        SIGNATURE_VERIFIED
        if not reasons
        else BLOCKED_SIGNATURE_VERIFICATION
    )

    provisional = MeasuredVenueEd25519Verification(
        verification_run_id=verification_run_id,
        attestation_artifact_hash=attestation.artifact_hash,
        attestation_payload_hash=attestation.payload_hash,
        qualification_artifact_hash=qualification.artifact_hash,
        signer_id=attestation.signer_id,
        key_id=attestation.key_id,
        signature_algorithm=attestation.signature_algorithm,
        public_key_b64=public_key_b64,
        collector_id=attestation.collector_id,
        collector_version=attestation.collector_version,
        venue_id=attestation.venue_id,
        acquisition_mode=attestation.acquisition_mode,
        trusted_public_key_policy_id=policy.policy_id,
        trusted_public_key_policy_version=policy.version,
        trusted_public_key_policy_hash=(
            measured_venue_trusted_public_key_policy_hash(
                policy
            )
        ),
        status=status,
        reasons=reasons,
        artifact_hash="PENDING",
    )

    return replace(
        provisional,
        artifact_hash=(
            measured_venue_ed25519_verification_artifact_hash(
                provisional
            )
        ),
    )


def verify_measured_venue_ed25519_verification_artifact(
    artifact: MeasuredVenueEd25519Verification,
    attestation: MeasuredVenueAcquisitionAttestation,
    qualification: MeasuredVenueSourceQualification,
    policy: MeasuredVenueTrustedPublicKeyPolicy,
) -> tuple[str, ...]:
    if not isinstance(
        artifact,
        MeasuredVenueEd25519Verification,
    ):
        raise TypeError(
            "artifact must be MeasuredVenueEd25519Verification"
        )

    reasons: list[str] = []

    def add_reason(reason: str) -> None:
        if reason not in reasons:
            reasons.append(reason)

    if (
        measured_venue_ed25519_verification_artifact_hash(
            artifact
        )
        != artifact.artifact_hash
    ):
        add_reason(
            "ED25519_VERIFICATION_ARTIFACT_HASH_MISMATCH"
        )

    expected_policy_hash = (
        measured_venue_trusted_public_key_policy_hash(policy)
    )

    bindings = (
        (
            artifact.attestation_artifact_hash,
            attestation.artifact_hash,
            "VERIFICATION_ATTESTATION_ARTIFACT_HASH_MISMATCH",
        ),
        (
            artifact.attestation_payload_hash,
            attestation.payload_hash,
            "VERIFICATION_ATTESTATION_PAYLOAD_HASH_MISMATCH",
        ),
        (
            artifact.qualification_artifact_hash,
            qualification.artifact_hash,
            "VERIFICATION_QUALIFICATION_ARTIFACT_HASH_MISMATCH",
        ),
        (
            artifact.signer_id,
            attestation.signer_id,
            "VERIFICATION_SIGNER_ID_MISMATCH",
        ),
        (
            artifact.key_id,
            attestation.key_id,
            "VERIFICATION_KEY_ID_MISMATCH",
        ),
        (
            artifact.signature_algorithm,
            attestation.signature_algorithm,
            "VERIFICATION_SIGNATURE_ALGORITHM_MISMATCH",
        ),
        (
            artifact.collector_id,
            attestation.collector_id,
            "VERIFICATION_COLLECTOR_ID_MISMATCH",
        ),
        (
            artifact.collector_version,
            attestation.collector_version,
            "VERIFICATION_COLLECTOR_VERSION_MISMATCH",
        ),
        (
            artifact.venue_id,
            attestation.venue_id,
            "VERIFICATION_VENUE_ID_MISMATCH",
        ),
        (
            artifact.acquisition_mode,
            attestation.acquisition_mode,
            "VERIFICATION_ACQUISITION_MODE_MISMATCH",
        ),
        (
            artifact.trusted_public_key_policy_id,
            policy.policy_id,
            "VERIFICATION_TRUSTED_KEY_POLICY_ID_MISMATCH",
        ),
        (
            artifact.trusted_public_key_policy_version,
            policy.version,
            "VERIFICATION_TRUSTED_KEY_POLICY_VERSION_MISMATCH",
        ),
        (
            artifact.trusted_public_key_policy_hash,
            expected_policy_hash,
            "VERIFICATION_TRUSTED_KEY_POLICY_HASH_MISMATCH",
        ),
    )

    for actual, expected, reason in bindings:
        if actual != expected:
            add_reason(reason)

    evaluation_reasons, expected_public_key_b64 = (
        _evaluation_reasons(
            attestation,
            qualification,
            policy,
        )
    )

    if artifact.public_key_b64 != expected_public_key_b64:
        add_reason(
            "VERIFICATION_TRUSTED_PUBLIC_KEY_MISMATCH"
        )

    expected_status = (
        SIGNATURE_VERIFIED
        if not evaluation_reasons
        else BLOCKED_SIGNATURE_VERIFICATION
    )

    status_mismatch = artifact.status != expected_status
    reasons_mismatch = artifact.reasons != evaluation_reasons

    if status_mismatch:
        add_reason("VERIFICATION_STATUS_MISMATCH")

    if reasons_mismatch:
        add_reason("VERIFICATION_REASONS_MISMATCH")

    if status_mismatch or reasons_mismatch:
        for reason in evaluation_reasons:
            add_reason(reason)

    return tuple(reasons)
