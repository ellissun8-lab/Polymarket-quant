# Deterministic measured-venue acquisition attestation contract v1.
#
# This module binds a policy-qualified measured venue source to a canonical
# signature envelope. It performs no cryptographic signature verification.
#
# VERIFIED is intentionally unreachable in v1. This module does not contact
# venues, hold credentials, submit orders, authorize execution PASS, promote
# factors, or enable LIVE execution.

from __future__ import annotations

import base64
from dataclasses import dataclass, replace
import hashlib
from typing import Any

from std0_quant.execution.execution_validation import BLOCKED
from std0_quant.execution.measured_venue_source_qualification import (
    QUALIFIED,
    MeasuredVenueSourceQualification,
    measured_venue_source_qualification_hash,
)
from std0_quant.storage import canonical_json


ED25519 = "ED25519"

PENDING_SIGNATURE_VERIFICATION = "PENDING_SIGNATURE_VERIFICATION"
BLOCKED_ATTESTATION = "BLOCKED_ATTESTATION"
VERIFIED = "VERIFIED"

MEASURED_VENUE_ACQUISITION_ATTESTATION_SCHEMA_V1 = (
    "measured_venue_acquisition_attestation_v1"
)


def _nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    text = value.strip()
    if not text:
        raise ValueError(f"{name} must be non-empty")
    return text


def _strict_base64(
    value: Any,
    name: str,
    *,
    decoded_length: int | None = None,
) -> str:
    text = _nonempty(value, name)

    try:
        decoded = base64.b64decode(
            text.encode("ascii"),
            validate=True,
        )
    except (ValueError, UnicodeEncodeError) as exc:
        raise ValueError(
            f"{name} must be strict base64 text"
        ) from exc

    canonical = base64.b64encode(decoded).decode("ascii")
    if canonical != text:
        raise ValueError(
            f"{name} must use canonical base64 encoding"
        )

    if (
        decoded_length is not None
        and len(decoded) != decoded_length
    ):
        raise ValueError(
            f"{name} must decode to exactly "
            f"{decoded_length} bytes"
        )

    return text


def _is_sha256_hex(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(
        ch in "0123456789abcdef"
        for ch in value
    )


@dataclass(frozen=True)
class MeasuredVenueAcquisitionAttestation:
    attestation_run_id: str

    qualification_run_id: str
    qualification_artifact_hash: str

    evidence_artifact_hash: str

    source_run_id: str
    source_artifact_hash: str

    collector_id: str
    collector_version: str
    venue_id: str
    acquisition_mode: str

    signer_id: str
    key_id: str
    signature_algorithm: str
    signature_b64: str

    status: str
    reasons: tuple[str, ...]

    payload_hash: str
    artifact_hash: str

    schema_version: str = (
        MEASURED_VENUE_ACQUISITION_ATTESTATION_SCHEMA_V1
    )

    def __post_init__(self) -> None:
        for name in (
            "attestation_run_id",
            "qualification_run_id",
            "qualification_artifact_hash",
            "evidence_artifact_hash",
            "source_run_id",
            "source_artifact_hash",
            "collector_id",
            "collector_version",
            "venue_id",
            "acquisition_mode",
            "signer_id",
            "key_id",
            "payload_hash",
            "artifact_hash",
        ):
            object.__setattr__(
                self,
                name,
                _nonempty(getattr(self, name), name),
            )

        if self.signature_algorithm != ED25519:
            raise ValueError(
                "measured venue acquisition attestation v1 "
                "requires ED25519 signature contract"
            )

        object.__setattr__(
            self,
            "signature_b64",
            _strict_base64(
                self.signature_b64,
                "signature_b64",
                decoded_length=64,
            ),
        )

        reasons = tuple(
            _nonempty(reason, "reason")
            for reason in self.reasons
        )
        object.__setattr__(self, "reasons", reasons)

        if self.status == VERIFIED:
            raise ValueError(
                "VERIFIED is unreachable in acquisition "
                "attestation v1 without a cryptographic backend"
            )

        if self.status not in {
            PENDING_SIGNATURE_VERIFICATION,
            BLOCKED_ATTESTATION,
        }:
            raise ValueError(
                "unsupported acquisition attestation status"
            )

        if (
            self.status == PENDING_SIGNATURE_VERIFICATION
            and reasons
        ):
            raise ValueError(
                "PENDING_SIGNATURE_VERIFICATION cannot "
                "contain reasons"
            )

        if (
            self.status == BLOCKED_ATTESTATION
            and not reasons
        ):
            raise ValueError(
                "BLOCKED_ATTESTATION requires reasons"
            )

        if (
            self.schema_version
            != MEASURED_VENUE_ACQUISITION_ATTESTATION_SCHEMA_V1
        ):
            raise ValueError(
                "unsupported MeasuredVenueAcquisitionAttestation "
                "schema_version"
            )


def _payload(
    artifact: MeasuredVenueAcquisitionAttestation,
) -> dict[str, Any]:
    return {
        "qualification_run_id": (
            artifact.qualification_run_id
        ),
        "qualification_artifact_hash": (
            artifact.qualification_artifact_hash
        ),
        "evidence_artifact_hash": (
            artifact.evidence_artifact_hash
        ),
        "source_run_id": artifact.source_run_id,
        "source_artifact_hash": (
            artifact.source_artifact_hash
        ),
        "collector_id": artifact.collector_id,
        "collector_version": artifact.collector_version,
        "venue_id": artifact.venue_id,
        "acquisition_mode": artifact.acquisition_mode,
        "signer_id": artifact.signer_id,
        "key_id": artifact.key_id,
        "signature_algorithm": artifact.signature_algorithm,
        "schema_version": artifact.schema_version,
    }


def measured_venue_acquisition_attestation_payload_hash(
    artifact: MeasuredVenueAcquisitionAttestation,
) -> str:
    if not isinstance(
        artifact,
        MeasuredVenueAcquisitionAttestation,
    ):
        raise TypeError(
            "artifact must be "
            "MeasuredVenueAcquisitionAttestation"
        )

    return hashlib.sha256(
        canonical_json(_payload(artifact)).encode("utf-8")
    ).hexdigest()


def _artifact_payload(
    artifact: MeasuredVenueAcquisitionAttestation,
) -> dict[str, Any]:
    return {
        **_payload(artifact),
        "signature_b64": artifact.signature_b64,
        "status": artifact.status,
        "reasons": artifact.reasons,
        "payload_hash": artifact.payload_hash,
    }


def measured_venue_acquisition_attestation_artifact_hash(
    artifact: MeasuredVenueAcquisitionAttestation,
) -> str:
    if not isinstance(
        artifact,
        MeasuredVenueAcquisitionAttestation,
    ):
        raise TypeError(
            "artifact must be "
            "MeasuredVenueAcquisitionAttestation"
        )

    return hashlib.sha256(
        canonical_json(
            _artifact_payload(artifact)
        ).encode("utf-8")
    ).hexdigest()


def verify_measured_venue_acquisition_attestation(
    artifact: MeasuredVenueAcquisitionAttestation,
    qualification: MeasuredVenueSourceQualification,
) -> tuple[str, ...]:
    if not isinstance(
        artifact,
        MeasuredVenueAcquisitionAttestation,
    ):
        raise TypeError(
            "artifact must be "
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

    reasons: list[str] = []

    def add_reason(reason: str) -> None:
        if reason not in reasons:
            reasons.append(reason)

    if (
        measured_venue_acquisition_attestation_payload_hash(
            artifact
        )
        != artifact.payload_hash
    ):
        add_reason("ATTESTATION_PAYLOAD_HASH_MISMATCH")

    if (
        measured_venue_acquisition_attestation_artifact_hash(
            artifact
        )
        != artifact.artifact_hash
    ):
        add_reason("ATTESTATION_ARTIFACT_HASH_MISMATCH")

    if (
        measured_venue_source_qualification_hash(
            qualification
        )
        != qualification.artifact_hash
    ):
        add_reason(
            "QUALIFICATION_ARTIFACT_HASH_MISMATCH"
        )

    if not _is_sha256_hex(
        qualification.source_artifact_hash
    ):
        add_reason(
            "QUALIFICATION_SOURCE_ARTIFACT_HASH_INVALID"
        )

    if not _is_sha256_hex(
        qualification.evidence_artifact_hash
    ):
        add_reason(
            "QUALIFICATION_EVIDENCE_ARTIFACT_HASH_INVALID"
        )

    if qualification.status == BLOCKED:
        add_reason("QUALIFICATION_BLOCKED")
        for reason in qualification.reasons:
            add_reason(reason)
    elif qualification.status != QUALIFIED:
        add_reason(
            "QUALIFICATION_STATUS_UNSUPPORTED"
        )

    if not _is_sha256_hex(
        qualification.source_artifact_hash
    ):
        add_reason(
            "QUALIFICATION_SOURCE_ARTIFACT_HASH_INVALID"
        )

    if not _is_sha256_hex(
        qualification.evidence_artifact_hash
    ):
        add_reason(
            "QUALIFICATION_EVIDENCE_ARTIFACT_HASH_INVALID"
        )

    if (
        artifact.qualification_run_id
        != qualification.qualification_run_id
    ):
        add_reason(
            "ATTESTATION_QUALIFICATION_RUN_ID_MISMATCH"
        )

    if (
        artifact.qualification_artifact_hash
        != qualification.artifact_hash
    ):
        add_reason(
            "ATTESTATION_QUALIFICATION_ARTIFACT_HASH_MISMATCH"
        )

    if (
        artifact.evidence_artifact_hash
        != qualification.evidence_artifact_hash
    ):
        add_reason(
            "ATTESTATION_EVIDENCE_ARTIFACT_HASH_MISMATCH"
        )

    if (
        artifact.source_run_id
        != qualification.source_run_id
    ):
        add_reason(
            "ATTESTATION_SOURCE_RUN_ID_MISMATCH"
        )

    if (
        artifact.source_artifact_hash
        != qualification.source_artifact_hash
    ):
        add_reason(
            "ATTESTATION_SOURCE_ARTIFACT_HASH_MISMATCH"
        )

    if artifact.collector_id != qualification.collector_id:
        add_reason(
            "ATTESTATION_COLLECTOR_ID_MISMATCH"
        )

    if (
        artifact.collector_version
        != qualification.collector_version
    ):
        add_reason(
            "ATTESTATION_COLLECTOR_VERSION_MISMATCH"
        )

    if artifact.venue_id != qualification.venue_id:
        add_reason(
            "ATTESTATION_VENUE_ID_MISMATCH"
        )

    if (
        artifact.acquisition_mode
        != qualification.acquisition_mode
    ):
        add_reason(
            "ATTESTATION_ACQUISITION_MODE_MISMATCH"
        )

    if artifact.status == BLOCKED_ATTESTATION:
        add_reason("ATTESTATION_BLOCKED")
        for reason in artifact.reasons:
            add_reason(reason)
    elif artifact.status != PENDING_SIGNATURE_VERIFICATION:
        add_reason(
            "ATTESTATION_STATUS_UNSUPPORTED"
        )

    return tuple(reasons)


def build_measured_venue_acquisition_attestation(
    qualification: MeasuredVenueSourceQualification,
    *,
    attestation_run_id: str,
    signer_id: str,
    key_id: str,
    signature_algorithm: str,
    signature_b64: str,
) -> MeasuredVenueAcquisitionAttestation:
    if not isinstance(
        qualification,
        MeasuredVenueSourceQualification,
    ):
        raise TypeError(
            "qualification must be "
            "MeasuredVenueSourceQualification"
        )

    attestation_run_id = _nonempty(
        attestation_run_id,
        "attestation_run_id",
    )
    signer_id = _nonempty(signer_id, "signer_id")
    key_id = _nonempty(key_id, "key_id")

    if signature_algorithm != ED25519:
        raise ValueError(
            "measured venue acquisition attestation v1 "
            "requires ED25519 signature contract"
        )

    signature_b64 = _strict_base64(
        signature_b64,
        "signature_b64",
        decoded_length=64,
    )

    reasons: list[str] = []

    def add_reason(reason: str) -> None:
        if reason not in reasons:
            reasons.append(reason)

    if (
        measured_venue_source_qualification_hash(
            qualification
        )
        != qualification.artifact_hash
    ):
        add_reason(
            "QUALIFICATION_ARTIFACT_HASH_MISMATCH"
        )

    if not _is_sha256_hex(
        qualification.source_artifact_hash
    ):
        add_reason(
            "QUALIFICATION_SOURCE_ARTIFACT_HASH_INVALID"
        )

    if not _is_sha256_hex(
        qualification.evidence_artifact_hash
    ):
        add_reason(
            "QUALIFICATION_EVIDENCE_ARTIFACT_HASH_INVALID"
        )

    if qualification.status == BLOCKED:
        add_reason("QUALIFICATION_BLOCKED")
        for reason in qualification.reasons:
            add_reason(reason)
    elif qualification.status != QUALIFIED:
        add_reason(
            "QUALIFICATION_STATUS_UNSUPPORTED"
        )

    status = (
        BLOCKED_ATTESTATION
        if reasons
        else PENDING_SIGNATURE_VERIFICATION
    )

    provisional = MeasuredVenueAcquisitionAttestation(
        attestation_run_id=attestation_run_id,
        qualification_run_id=(
            qualification.qualification_run_id
        ),
        qualification_artifact_hash=(
            qualification.artifact_hash
        ),
        evidence_artifact_hash=(
            qualification.evidence_artifact_hash
        ),
        source_run_id=qualification.source_run_id,
        source_artifact_hash=(
            qualification.source_artifact_hash
        ),
        collector_id=qualification.collector_id,
        collector_version=(
            qualification.collector_version
        ),
        venue_id=qualification.venue_id,
        acquisition_mode=(
            qualification.acquisition_mode
        ),
        signer_id=signer_id,
        key_id=key_id,
        signature_algorithm=signature_algorithm,
        signature_b64=signature_b64,
        status=status,
        reasons=tuple(reasons),
        payload_hash="PENDING",
        artifact_hash="PENDING",
    )

    payload_hash = (
        measured_venue_acquisition_attestation_payload_hash(
            provisional
        )
    )

    with_payload_hash = replace(
        provisional,
        payload_hash=payload_hash,
    )

    return replace(
        with_payload_hash,
        artifact_hash=(
            measured_venue_acquisition_attestation_artifact_hash(
                with_payload_hash
            )
        ),
    )
