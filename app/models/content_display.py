"""Display-oriented helpers for normalized content."""

from typing import Any

from app.constants import SELF_SUBMISSION_SOURCE
from app.models.metadata import ContentData, ContentType
from app.utils.image_urls import (
    build_content_image_url,
    build_news_thumbnail_url,
    build_thumbnail_url,
)


def resolve_image_urls(domain_content: ContentData) -> tuple[str | None, str | None]:
    """Resolve image URLs without filesystem checks."""
    metadata = domain_content.metadata or {}
    provider_thumbnail = None

    if domain_content.content_type == ContentType.NEWS:
        return None, None

    if domain_content.content_type == ContentType.PODCAST:
        raw_thumbnail = metadata.get("thumbnail_url")
        if isinstance(raw_thumbnail, str) and raw_thumbnail.startswith("http"):
            provider_thumbnail = raw_thumbnail

    image_url = metadata.get("image_url")
    thumbnail_url = metadata.get("thumbnail_url")
    has_generated_image = bool(metadata.get("image_generated_at"))

    if domain_content.content_type == ContentType.PODCAST and has_generated_image:
        if image_url == provider_thumbnail:
            image_url = None
        if thumbnail_url == provider_thumbnail:
            thumbnail_url = None

    if not image_url and has_generated_image and domain_content.id:
        if domain_content.content_type == ContentType.NEWS:
            image_url = build_news_thumbnail_url(domain_content.id)
        else:
            image_url = build_content_image_url(domain_content.id)

    if not thumbnail_url and has_generated_image and domain_content.id:
        thumbnail_url = build_thumbnail_url(domain_content.id)

    if domain_content.content_type == ContentType.PODCAST and not image_url:
        image_url = provider_thumbnail
        thumbnail_url = None

    return image_url, thumbnail_url


def is_ready_for_long_form_summary(domain_content: ContentData) -> bool:
    """Return True when long-form content has enough summary data for feed display."""
    if domain_content.content_type == ContentType.ARTICLE:
        if domain_content.structured_summary and domain_content.bullet_points:
            return True
        return bool(domain_content.short_summary or domain_content.summary)

    if domain_content.content_type == ContentType.PODCAST:
        if domain_content.structured_summary:
            return True
        return bool(domain_content.short_summary or domain_content.summary)

    return True


def can_subscribe_for_feed(
    domain_content: ContentData,
    detected_feed_data: dict[str, Any] | None,
) -> bool:
    """Return whether a detected feed should expose subscription affordances."""
    if not detected_feed_data:
        return False
    return (
        domain_content.content_type == ContentType.NEWS
        or domain_content.source == SELF_SUBMISSION_SOURCE
    )
