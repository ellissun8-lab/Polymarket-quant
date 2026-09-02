# Deterministic measured-venue source qualification v1.
#
# This layer evaluates whether measured execution evidence came through a
# source/collector/venue acquisition identity allowed by a frozen policy.
#
# QUALIFIED means policy-qualified source identity only. It does not prove
# cryptographic venue origin, authorize execution PASS, promote factors,
# load credentials, contact venues, or submit orders.

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
from typing import Any

from std0_quant.execution.execution_validation import (
    BLOCKED,
    READY_FOR_POLICY_EVALUATION,
)
from std0_quant.execution.measured_venue_execution import (
    MeasuredVenueExecutionArtifact,
    measured_venue_execution_artifact_hash,
)
from std0_quant.storage import canonical_json


QUALIFIED = "QUALIFIED"

MEASURED_VENUE_SOURCE_RULE_SCHEMA_V1 = (
    "measured_venue_source_rule_v1"
)
MEASURED_VENUE_SOURCE_QUALIFICATION_POLICY_SCHEMA_V1 = (
    "measured_venue_source_qualification_policy_v1"
)
MEASURED_VENUE_SOURCE_QUALIFICATION_SCHEMA_V1 = (
    "measured_venue_source_qualification_v1"
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


@dataclass(frozen=True)
class MeasuredVenueSourceRule:
    source_id: str
    source_version: str
    collector_id: str
    collector_version: str
    venue_id: str
    acquisition_mode: str
    schema_version: str = MEASURED_VENUE_SOURCE_RULE_SCHEMA_V1

    def __post_init__(self) -> None:
        for name in (
            "source_id",
            "source_version",
            "collector_id",
            "collector_version",
            "venue_id",
            "acquisition_mode",
        ):
            object.__setattr__(
                self,
                name,
                _nonempty(getattr(self, name), name),
            )

        if self.schema_version != MEASURED_VENUE_SOURCE_RULE_SCHEMA_V1:
            raise ValueError("unsupported MeasuredVenueSourceRule schema_version")


def _rule_sort_key(rule: MeasuredVenueSourceRule) -> tuple[str, ...]:
    return (
        rule.source_id,
        rule.source_version,
        rule.collector_id,
        rule.collector_version,
        rule.venue_id,
        rule.acquisition_mode,
        rule.schema_version,
    )


@dataclass(frozen=True)
class MeasuredVenueSourceQualificationPolicy:
    policy_id: str
    version: str
    rules: tuple[MeasuredVenueSourceRule, ...]
    schema_version: str = (
        MEASURED_VENUE_SOURCE_QUALIFICATION_POLICY_SCHEMA_V1
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "policy_id",
            _nonempty(self.policy_id, "policy_id"),
        )
        object.__setattr__(
            self,
            "version",
            _nonempty(self.version, "version"),
        )

        rules = tuple(self.rules)
        if not rules:
            raise ValueError("source qualification policy requires rules")

        for rule in rules:
            if not isinstance(rule, MeasuredVenueSourceRule):
                raise TypeError(
                    "rules must contain MeasuredVenueSourceRule"
                )

        if len(set(rules)) != len(rules):
            raise ValueError(
                "duplicate measured venue source qualification rule"
            )

        source_identities: dict[
            tuple[str, str],
            tuple[str, str, str, str],
        ] = {}

        for rule in rules:
            source_key = (
                rule.source_id,
                rule.source_version,
            )
            acquisition_identity = (
                rule.collector_id,
                rule.collector_version,
                rule.venue_id,
                rule.acquisition_mode,
            )

            previous = source_identities.get(source_key)
            if (
                previous is not None
                and previous != acquisition_identity
            ):
                raise ValueError(
                    "ambiguous measured venue source identity"
                )

            source_identities[source_key] = acquisition_identity

        object.__setattr__(self, "rules", rules)

        if (
            self.schema_version
            != MEASURED_VENUE_SOURCE_QUALIFICATION_POLICY_SCHEMA_V1
        ):
            raise ValueError(
                "unsupported MeasuredVenueSourceQualificationPolicy "
                "schema_version"
            )


def measured_venue_source_qualification_policy_hash(
    policy: MeasuredVenueSourceQualificationPolicy,
) -> str:
    if not isinstance(
        policy,
        MeasuredVenueSourceQualificationPolicy,
    ):
        raise TypeError(
            "policy must be MeasuredVenueSourceQualificationPolicy"
        )

    payload = {
        "policy_id": policy.policy_id,
        "version": policy.version,
        "rules": [
            asdict(rule)
            for rule in sorted(
                policy.rules,
                key=_rule_sort_key,
            )
        ],
        "schema_version": policy.schema_version,
    }

    return hashlib.sha256(
        canonical_json(payload).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class MeasuredVenueSourceQualification:
    qualification_run_id: str
    evidence_run_id: str
    evidence_artifact_hash: str

    source_id: str
    source_version: str
    source_run_id: str
    source_artifact_hash: str

    collector_id: str
    collector_version: str
    venue_id: str
    acquisition_mode: str

    status: str
    reasons: tuple[str, ...]

    policy_id: str
    policy_version: str
    policy_hash: str

    artifact_hash: str
    schema_version: str = MEASURED_VENUE_SOURCE_QUALIFICATION_SCHEMA_V1

    def __post_init__(self) -> None:
        for name in (
            "qualification_run_id",
            "evidence_run_id",
            "evidence_artifact_hash",
            "source_id",
            "source_version",
            "source_run_id",
            "source_artifact_hash",
            "collector_id",
            "collector_version",
            "venue_id",
            "acquisition_mode",
            "policy_id",
            "policy_version",
            "policy_hash",
            "artifact_hash",
        ):
            object.__setattr__(
                self,
                name,
                _nonempty(getattr(self, name), name),
            )

        if self.status not in {QUALIFIED, BLOCKED}:
            raise ValueError(
                "unsupported measured source qualification status"
            )

        reasons = tuple(
            _nonempty(reason, "reason")
            for reason in self.reasons
        )
        object.__setattr__(self, "reasons", reasons)

        if self.status == QUALIFIED and reasons:
            raise ValueError("QUALIFIED cannot contain reasons")

        if self.status == BLOCKED and not reasons:
            raise ValueError("BLOCKED requires reasons")

        if (
            self.schema_version
            != MEASURED_VENUE_SOURCE_QUALIFICATION_SCHEMA_V1
        ):
            raise ValueError(
                "unsupported MeasuredVenueSourceQualification "
                "schema_version"
            )


def _qualification_payload(
    qualification: MeasuredVenueSourceQualification,
) -> dict[str, Any]:
    return {
        "evidence_run_id": qualification.evidence_run_id,
        "evidence_artifact_hash": (
            qualification.evidence_artifact_hash
        ),
        "source_id": qualification.source_id,
        "source_version": qualification.source_version,
        "source_run_id": qualification.source_run_id,
        "source_artifact_hash": (
            qualification.source_artifact_hash
        ),
        "collector_id": qualification.collector_id,
        "collector_version": qualification.collector_version,
        "venue_id": qualification.venue_id,
        "acquisition_mode": qualification.acquisition_mode,
        "status": qualification.status,
        "reasons": qualification.reasons,
        "policy_id": qualification.policy_id,
        "policy_version": qualification.policy_version,
        "policy_hash": qualification.policy_hash,
        "schema_version": qualification.schema_version,
    }


def measured_venue_source_qualification_hash(
    qualification: MeasuredVenueSourceQualification,
) -> str:
    if not isinstance(
        qualification,
        MeasuredVenueSourceQualification,
    ):
        raise TypeError(
            "qualification must be MeasuredVenueSourceQualification"
        )

    return hashlib.sha256(
        canonical_json(
            _qualification_payload(qualification)
        ).encode("utf-8")
    ).hexdigest()


def verify_measured_venue_source_qualification(
    qualification: MeasuredVenueSourceQualification,
    evidence: MeasuredVenueExecutionArtifact,
    policy: MeasuredVenueSourceQualificationPolicy,
) -> tuple[str, ...]:
    if not isinstance(
        qualification,
        MeasuredVenueSourceQualification,
    ):
        raise TypeError(
            "qualification must be MeasuredVenueSourceQualification"
        )
    if not isinstance(evidence, MeasuredVenueExecutionArtifact):
        raise TypeError(
            "evidence must be MeasuredVenueExecutionArtifact"
        )
    if not isinstance(
        policy,
        MeasuredVenueSourceQualificationPolicy,
    ):
        raise TypeError(
            "policy must be MeasuredVenueSourceQualificationPolicy"
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
        add_reason("QUALIFICATION_ARTIFACT_HASH_MISMATCH")

    if (
        measured_venue_execution_artifact_hash(evidence)
        != evidence.artifact_hash
    ):
        add_reason("MEASURED_EVIDENCE_ARTIFACT_HASH_MISMATCH")

    if evidence.status == BLOCKED:
        add_reason("MEASURED_EVIDENCE_BLOCKED")
        for reason in evidence.reasons:
            add_reason(reason)
    elif evidence.status != READY_FOR_POLICY_EVALUATION:
        add_reason("MEASURED_EVIDENCE_STATUS_UNSUPPORTED")

    if qualification.evidence_run_id != evidence.evidence_run_id:
        add_reason("QUALIFICATION_EVIDENCE_RUN_ID_MISMATCH")

    if (
        qualification.evidence_artifact_hash
        != evidence.artifact_hash
    ):
        add_reason(
            "QUALIFICATION_EVIDENCE_ARTIFACT_HASH_MISMATCH"
        )

    if qualification.source_id != evidence.source.source_id:
        add_reason("QUALIFICATION_SOURCE_ID_MISMATCH")

    if (
        qualification.source_version
        != evidence.source.source_version
    ):
        add_reason("QUALIFICATION_SOURCE_VERSION_MISMATCH")

    if (
        qualification.source_run_id
        != evidence.source.source_run_id
    ):
        add_reason("QUALIFICATION_SOURCE_RUN_ID_MISMATCH")

    if (
        qualification.source_artifact_hash
        != evidence.source.source_artifact_hash
    ):
        add_reason(
            "QUALIFICATION_SOURCE_ARTIFACT_HASH_MISMATCH"
        )

    if not _is_sha256_hex(
        evidence.source.source_artifact_hash
    ):
        add_reason("SOURCE_ARTIFACT_HASH_INVALID")

    if qualification.policy_id != policy.policy_id:
        add_reason("QUALIFICATION_POLICY_ID_MISMATCH")

    if qualification.policy_version != policy.version:
        add_reason("QUALIFICATION_POLICY_VERSION_MISMATCH")

    expected_policy_hash = (
        measured_venue_source_qualification_policy_hash(
            policy
        )
    )

    if qualification.policy_hash != expected_policy_hash:
        add_reason("QUALIFICATION_POLICY_HASH_MISMATCH")

    matching_rule = any(
        rule.source_id == qualification.source_id
        and rule.source_version == qualification.source_version
        and rule.collector_id == qualification.collector_id
        and rule.collector_version
        == qualification.collector_version
        and rule.venue_id == qualification.venue_id
        and rule.acquisition_mode
        == qualification.acquisition_mode
        for rule in policy.rules
    )

    if not matching_rule:
        add_reason("SOURCE_NOT_ALLOWED_BY_POLICY")

    if qualification.status != QUALIFIED:
        add_reason("QUALIFICATION_NOT_QUALIFIED")

    return tuple(reasons)


def evaluate_measured_venue_source_qualification(
    evidence: MeasuredVenueExecutionArtifact,
    policy: MeasuredVenueSourceQualificationPolicy,
    *,
    collector_id: str,
    collector_version: str,
    venue_id: str,
    acquisition_mode: str,
    qualification_run_id: str,
) -> MeasuredVenueSourceQualification:
    if not isinstance(evidence, MeasuredVenueExecutionArtifact):
        raise TypeError(
            "evidence must be MeasuredVenueExecutionArtifact"
        )
    if not isinstance(
        policy,
        MeasuredVenueSourceQualificationPolicy,
    ):
        raise TypeError(
            "policy must be MeasuredVenueSourceQualificationPolicy"
        )

    collector_id = _nonempty(collector_id, "collector_id")
    collector_version = _nonempty(
        collector_version,
        "collector_version",
    )
    venue_id = _nonempty(venue_id, "venue_id")
    acquisition_mode = _nonempty(
        acquisition_mode,
        "acquisition_mode",
    )
    qualification_run_id = _nonempty(
        qualification_run_id,
        "qualification_run_id",
    )

    reasons: list[str] = []

    def add_reason(reason: str) -> None:
        if reason not in reasons:
            reasons.append(reason)

    if not _is_sha256_hex(
        evidence.source.source_artifact_hash
    ):
        add_reason("SOURCE_ARTIFACT_HASH_INVALID")

    if (
        measured_venue_execution_artifact_hash(evidence)
        != evidence.artifact_hash
    ):
        add_reason(
            "MEASURED_EVIDENCE_ARTIFACT_HASH_MISMATCH"
        )

    if evidence.status == BLOCKED:
        add_reason("MEASURED_EVIDENCE_BLOCKED")
        for reason in evidence.reasons:
            add_reason(reason)
    elif evidence.status != READY_FOR_POLICY_EVALUATION:
        add_reason(
            "MEASURED_EVIDENCE_STATUS_UNSUPPORTED"
        )

    matching_rule = any(
        rule.source_id == evidence.source.source_id
        and rule.source_version == evidence.source.source_version
        and rule.collector_id == collector_id
        and rule.collector_version == collector_version
        and rule.venue_id == venue_id
        and rule.acquisition_mode == acquisition_mode
        for rule in policy.rules
    )

    if not matching_rule:
        add_reason("SOURCE_NOT_ALLOWED_BY_POLICY")

    status = BLOCKED if reasons else QUALIFIED

    provisional = MeasuredVenueSourceQualification(
        qualification_run_id=qualification_run_id,
        evidence_run_id=evidence.evidence_run_id,
        evidence_artifact_hash=evidence.artifact_hash,
        source_id=evidence.source.source_id,
        source_version=evidence.source.source_version,
        source_run_id=evidence.source.source_run_id,
        source_artifact_hash=(
            evidence.source.source_artifact_hash
        ),
        collector_id=collector_id,
        collector_version=collector_version,
        venue_id=venue_id,
        acquisition_mode=acquisition_mode,
        status=status,
        reasons=tuple(reasons),
        policy_id=policy.policy_id,
        policy_version=policy.version,
        policy_hash=(
            measured_venue_source_qualification_policy_hash(
                policy
            )
        ),
        artifact_hash="PENDING",
    )

    return replace(
        provisional,
        artifact_hash=(
            measured_venue_source_qualification_hash(
                provisional
            )
        ),
    )
