"""Typed helpers for display-only source metadata."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from app.models.metadata.source import SourceMetadataEnvelope

SOURCE_METADATA_KEY = "source_metadata"


def normalize_source_metadata(value: Any) -> SourceMetadataEnvelope | None:
    """Return a validated source metadata envelope, or None for absent/invalid data."""
    if isinstance(value, SourceMetadataEnvelope):
        return value
    if not isinstance(value, Mapping):
        return None
    try:
        return SourceMetadataEnvelope.model_validate(value)
    except ValidationError:
        return None


def dump_source_metadata(value: Any) -> dict[str, Any] | None:
    """Return JSON-safe source metadata after schema validation."""
    metadata = normalize_source_metadata(value)
    if metadata is None:
        return None
    return metadata.model_dump(mode="json", exclude_none=True)


def attach_source_metadata(target: dict[str, Any], value: Any) -> dict[str, Any]:
    """Attach validated source metadata to an existing metadata payload."""
    payload = dump_source_metadata(value)
    if payload is not None:
        target[SOURCE_METADATA_KEY] = payload
    return target
