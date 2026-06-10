"""Shared API model field types."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import PlainSerializer


def serialize_utc_datetime(value: datetime) -> str:
    """Serialize naive or aware datetimes as UTC with an explicit Z suffix."""
    utc_value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return utc_value.isoformat().replace("+00:00", "Z")


UTCDateTime = Annotated[
    datetime,
    PlainSerializer(serialize_utc_datetime, return_type=str, when_used="json"),
]
