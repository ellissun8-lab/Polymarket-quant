"""Fail-closed bridge from Prime Agent proposals to FactorSpec v1.

Research-only boundary. Prime Agent may propose factor definitions; this module
does not evaluate, validate, promote, persist registry state, or execute orders.
"""

from __future__ import annotations

import json
from typing import Any

from .contracts import FactorSpec


PRIME_FACTOR_PROPOSAL_SCHEMA_V1 = "prime_factor_proposal_v1"

_REQUIRED_FIELDS = frozenset(
    {
        "schema_version",
        "factor_id",
        "version",
        "hypothesis",
        "inputs",
        "transform",
        "parameters",
        "lookback_ms",
        "decision_ts_rule",
        "availability_ts_rule",
        "missing_policy",
        "universe",
        "label",
        "period_key",
        "expected_direction",
        "expected_regime",
    }
)

_ALLOWED_TRANSFORMS = frozenset({"identity", "product"})


def _load_object(payload: str) -> dict[str, Any]:
    try:
        row = json.loads(payload)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("invalid Prime factor proposal JSON") from exc

    if not isinstance(row, dict):
        raise ValueError("Prime factor proposal must be a JSON object")

    return row


def parse_prime_factor_proposal(
    payload: str,
    *,
    created_at: str,
) -> FactorSpec:
    """Parse one untrusted Prime Agent proposal into an immutable FactorSpec.

    The proposal schema is intentionally narrower than FactorSpec. Creation
    metadata and all validation, registry, execution, and promotion fields are
    owned by deterministic std0-quant code rather than the agent.
    """

    row = _load_object(payload)

    unknown = sorted(set(row) - _REQUIRED_FIELDS)
    if unknown:
        raise ValueError(f"unknown Prime factor proposal fields: {unknown}")

    missing = sorted(_REQUIRED_FIELDS - set(row))
    if missing:
        raise ValueError(f"missing required Prime factor proposal fields: {missing}")

    if row["schema_version"] != PRIME_FACTOR_PROPOSAL_SCHEMA_V1:
        raise ValueError("unsupported Prime factor proposal schema_version")

    transform = row["transform"]
    if transform not in _ALLOWED_TRANSFORMS:
        raise ValueError(f"unsupported factor transform: {transform}")

    if row["missing_policy"] != "EXCLUDE":
        raise ValueError("unsupported missing_policy: Factor Bridge v1 requires EXCLUDE")

    parameters = row["parameters"]
    if not isinstance(parameters, dict):
        raise ValueError("parameters must be a JSON object")

    inputs = row["inputs"]
    if not isinstance(inputs, list):
        raise ValueError("inputs must be a JSON array")

    return FactorSpec(
        factor_id=row["factor_id"],
        version=row["version"],
        hypothesis=row["hypothesis"],
        inputs=tuple(inputs),
        transform=transform,
        parameters=tuple(parameters.items()),
        lookback_ms=row["lookback_ms"],
        decision_ts_rule=row["decision_ts_rule"],
        availability_ts_rule=row["availability_ts_rule"],
        missing_policy=row["missing_policy"],
        universe=row["universe"],
        label=row["label"],
        period_key=row["period_key"],
        expected_direction=row["expected_direction"],
        expected_regime=row["expected_regime"],
        created_by="prime-agent",
        created_at=created_at,
    )
