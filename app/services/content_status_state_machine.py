"""Canonical content-status transitions for long-form readiness."""

from __future__ import annotations

from app.models.contracts import ContentStatus, ContentType

LONG_FORM_CONTENT_TYPES: set[str] = {
    ContentType.ARTICLE.value,
    ContentType.PODCAST.value,
}


class InvalidContentStatusTransition(ValueError):
    """Raised when code attempts an invalid status transition."""


class ContentStatusStateMachine:
    """Central status-transition rules for content readiness."""

    @staticmethod
    def is_long_form(content_type: str | ContentType | None) -> bool:
        if isinstance(content_type, ContentType):
            return content_type.value in LONG_FORM_CONTENT_TYPES
        return bool(content_type and content_type in LONG_FORM_CONTENT_TYPES)

    @classmethod
    def status_after_summary(
        cls,
        *,
        content_type: str | ContentType,
        artwork_ready: bool,
    ) -> ContentStatus:
        """Return the canonical post-summary status."""
        if not cls.is_long_form(content_type):
            return ContentStatus.COMPLETED
        if artwork_ready:
            return ContentStatus.COMPLETED
        return ContentStatus.AWAITING_IMAGE

    @classmethod
    def status_after_generated_artwork(
        cls,
        *,
        content_type: str | ContentType,
        current_status: str | ContentStatus | None,
    ) -> ContentStatus:
        """Return the canonical post-image status."""
        if not cls.is_long_form(content_type):
            return ContentStatus.COMPLETED

        normalized_current = cls._normalize_status(current_status)
        if normalized_current not in {
            ContentStatus.AWAITING_IMAGE,
            ContentStatus.COMPLETED,
        }:
            raise InvalidContentStatusTransition(
                "Long-form artwork generation can only complete content from "
                f"`awaiting_image` or `completed`, got `{normalized_current.value}`"
            )
        return ContentStatus.COMPLETED

    @classmethod
    def status_allows_artwork_enqueue(cls, status: str | ContentStatus | None) -> bool:
        """Return True when a row is eligible for generate-image work."""
        normalized_status = cls._normalize_status(status)
        return normalized_status in {
            ContentStatus.AWAITING_IMAGE,
            ContentStatus.COMPLETED,
        }

    @staticmethod
    def _normalize_status(status: str | ContentStatus | None) -> ContentStatus:
        if isinstance(status, ContentStatus):
            return status
        if isinstance(status, str):
            return ContentStatus(status)
        raise InvalidContentStatusTransition("Content status is required")
