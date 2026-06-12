"""Contract policy helpers consumed by code generators."""

from __future__ import annotations

from typing import Any

from app.models.api.base import (
    CONTRACT_LENIENT_KEY,
    CONTRACT_SCHEMA_EXTRA_KEY,
    lenient_field,
)

__all__ = [
    "contract_policy_from_extra",
    "is_lenient_policy",
    "lenient_field",
]


def contract_policy_from_extra(json_schema_extra: dict[str, Any]) -> dict[str, Any]:
    """Return generator policy metadata from a Pydantic field's schema extra."""

    raw_policy = json_schema_extra.get(CONTRACT_SCHEMA_EXTRA_KEY)
    if not isinstance(raw_policy, dict):
        return {}
    return dict(raw_policy)


def is_lenient_policy(contract_policy: dict[str, Any]) -> bool:
    """Whether a field is explicitly allowed to decode with a fallback."""

    return contract_policy.get(CONTRACT_LENIENT_KEY) is True
