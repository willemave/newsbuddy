"""Helpers for deriving canonical content form."""

from __future__ import annotations

from typing import Literal

from app.models.contracts import ContentType

ContentForm = Literal["short", "long"]


def derive_content_form(content_type: str | ContentType | None) -> ContentForm | None:
    """Return canonical content form for a content type."""
    if content_type is None:
        return None

    normalized = content_type.value if isinstance(content_type, ContentType) else str(content_type)
    if normalized == ContentType.NEWS.value:
        return "short"
    if normalized in {ContentType.ARTICLE.value, ContentType.PODCAST.value}:
        return "long"
    return None
