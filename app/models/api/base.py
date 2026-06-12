"""Shared API model field types."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import Field, PlainSerializer
from pydantic.fields import PydanticUndefined

CONTRACT_SCHEMA_EXTRA_KEY = "contract"
CONTRACT_LENIENT_KEY = "lenient"


def serialize_utc_datetime(value: datetime) -> str:
    """Serialize naive or aware datetimes as UTC with an explicit Z suffix."""
    utc_value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return utc_value.isoformat().replace("+00:00", "Z")


UTCDateTime = Annotated[
    datetime,
    PlainSerializer(serialize_utc_datetime, return_type=str, when_used="json"),
]


def lenient_field(default: Any = PydanticUndefined, **kwargs: Any) -> Any:
    """Mark a Pydantic field as intentionally tolerant in generated clients."""

    json_schema_extra = kwargs.pop("json_schema_extra", None)
    if callable(json_schema_extra):
        raise TypeError("lenient_field does not support callable json_schema_extra")

    extra = dict(json_schema_extra or {})
    contract_policy = dict(extra.get(CONTRACT_SCHEMA_EXTRA_KEY) or {})
    contract_policy[CONTRACT_LENIENT_KEY] = True
    extra[CONTRACT_SCHEMA_EXTRA_KEY] = contract_policy

    if default is PydanticUndefined:
        return Field(json_schema_extra=extra, **kwargs)
    return Field(default, json_schema_extra=extra, **kwargs)
