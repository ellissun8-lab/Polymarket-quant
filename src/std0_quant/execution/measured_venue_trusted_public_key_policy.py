# Frozen trusted public-key policy for measured venue attestation v1.
#
# This module defines which Ed25519 public keys are trusted for which
# acquisition contexts. It contains no private keys, performs no signing,
# performs no cryptographic signature verification, and does not authorize
# execution PASS, promotion, or LIVE execution.

from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
import hashlib
from typing import Any

from std0_quant.storage import canonical_json


ED25519 = "ED25519"

MEASURED_VENUE_TRUSTED_PUBLIC_KEY_RULE_SCHEMA_V1 = (
    "measured_venue_trusted_public_key_rule_v1"
)
MEASURED_VENUE_TRUSTED_PUBLIC_KEY_POLICY_SCHEMA_V1 = (
    "measured_venue_trusted_public_key_policy_v1"
)


def _nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")

    text = value.strip()
    if not text:
        raise ValueError(f"{name} must be non-empty")

    return text


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

    text = value

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

    if len(decoded) != decoded_length:
        raise ValueError(
            f"{name} must decode to exactly "
            f"{decoded_length} bytes"
        )

    return text


@dataclass(frozen=True)
class MeasuredVenueTrustedPublicKeyRule:
    signer_id: str
    key_id: str
    signature_algorithm: str
    public_key_b64: str

    collector_id: str
    collector_version: str
    venue_id: str
    acquisition_mode: str

    schema_version: str = (
        MEASURED_VENUE_TRUSTED_PUBLIC_KEY_RULE_SCHEMA_V1
    )

    def __post_init__(self) -> None:
        for name in (
            "signer_id",
            "key_id",
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

        if self.signature_algorithm != ED25519:
            raise ValueError(
                "trusted public-key policy v1 requires ED25519"
            )

        object.__setattr__(
            self,
            "public_key_b64",
            _canonical_base64_exact_length(
                self.public_key_b64,
                "public_key_b64",
                32,
            ),
        )

        if (
            self.schema_version
            != MEASURED_VENUE_TRUSTED_PUBLIC_KEY_RULE_SCHEMA_V1
        ):
            raise ValueError(
                "unsupported MeasuredVenueTrustedPublicKeyRule "
                "schema_version"
            )


def _rule_sort_key(
    rule: MeasuredVenueTrustedPublicKeyRule,
) -> tuple[str, ...]:
    return (
        rule.signer_id,
        rule.key_id,
        rule.signature_algorithm,
        rule.public_key_b64,
        rule.collector_id,
        rule.collector_version,
        rule.venue_id,
        rule.acquisition_mode,
        rule.schema_version,
    )


@dataclass(frozen=True)
class MeasuredVenueTrustedPublicKeyPolicy:
    policy_id: str
    version: str
    rules: tuple[MeasuredVenueTrustedPublicKeyRule, ...]
    schema_version: str = (
        MEASURED_VENUE_TRUSTED_PUBLIC_KEY_POLICY_SCHEMA_V1
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
            raise ValueError(
                "trusted public-key policy requires rules"
            )

        for rule in rules:
            if not isinstance(
                rule,
                MeasuredVenueTrustedPublicKeyRule,
            ):
                raise TypeError(
                    "rules must contain "
                    "MeasuredVenueTrustedPublicKeyRule"
                )

        if len(set(rules)) != len(rules):
            raise ValueError(
                "duplicate measured venue trusted public-key rule"
            )

        key_material: dict[
            tuple[str, str],
            tuple[str, str],
        ] = {}
        material_identity: dict[
            tuple[str, str],
            tuple[str, str],
        ] = {}

        for rule in rules:
            identity = (
                rule.signer_id,
                rule.key_id,
            )
            material = (
                rule.signature_algorithm,
                rule.public_key_b64,
            )

            previous = key_material.get(identity)
            if previous is not None and previous != material:
                raise ValueError(
                    "ambiguous trusted public-key identity"
                )

            previous_identity = material_identity.get(material)
            if (
                previous_identity is not None
                and previous_identity != identity
            ):
                raise ValueError(
                    "trusted public-key material cannot alias "
                    "multiple logical identities"
                )

            key_material[identity] = material
            material_identity[material] = identity

        object.__setattr__(self, "rules", rules)

        if (
            self.schema_version
            != MEASURED_VENUE_TRUSTED_PUBLIC_KEY_POLICY_SCHEMA_V1
        ):
            raise ValueError(
                "unsupported "
                "MeasuredVenueTrustedPublicKeyPolicy "
                "schema_version"
            )


def measured_venue_trusted_public_key_policy_hash(
    policy: MeasuredVenueTrustedPublicKeyPolicy,
) -> str:
    if not isinstance(
        policy,
        MeasuredVenueTrustedPublicKeyPolicy,
    ):
        raise TypeError(
            "policy must be "
            "MeasuredVenueTrustedPublicKeyPolicy"
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


def resolve_measured_venue_trusted_public_key_rule(
    policy: MeasuredVenueTrustedPublicKeyPolicy,
    *,
    signer_id: str,
    key_id: str,
    signature_algorithm: str,
    collector_id: str,
    collector_version: str,
    venue_id: str,
    acquisition_mode: str,
) -> MeasuredVenueTrustedPublicKeyRule | None:
    if not isinstance(
        policy,
        MeasuredVenueTrustedPublicKeyPolicy,
    ):
        raise TypeError(
            "policy must be "
            "MeasuredVenueTrustedPublicKeyPolicy"
        )

    for rule in policy.rules:
        if (
            rule.signer_id == signer_id
            and rule.key_id == key_id
            and rule.signature_algorithm
            == signature_algorithm
            and rule.collector_id == collector_id
            and rule.collector_version
            == collector_version
            and rule.venue_id == venue_id
            and rule.acquisition_mode == acquisition_mode
        ):
            return rule

    return None
