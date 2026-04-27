"""Typed metadata accessors for content JSON blobs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.models.metadata_state import merge_runtime_metadata, normalize_metadata_shape


@dataclass(frozen=True)
class NewsFields:
    article: dict[str, Any]
    aggregator: dict[str, Any]
    summary: dict[str, Any]
    discussion_url: str | None
    summary_key_points: list[Any] | None
    comment_count: int | None


class ContentMetadataView:
    """Read-only compatibility view over legacy and namespaced metadata."""

    def __init__(self, metadata: dict[str, Any] | None) -> None:
        self._normalized = normalize_metadata_shape(metadata)
        self._runtime = merge_runtime_metadata(self._normalized)

    @property
    def normalized(self) -> dict[str, Any]:
        return dict(self._normalized)

    @property
    def runtime(self) -> dict[str, Any]:
        return dict(self._runtime)

    def get(self, key: str, default: Any = None) -> Any:
        return self._runtime.get(key, default)

    def summary(self) -> dict[str, Any] | None:
        value = self._runtime.get("summary")
        return value if isinstance(value, dict) else None

    def summary_kind(self) -> Any:
        return self._runtime.get("summary_kind")

    def summary_version(self) -> Any:
        return self._runtime.get("summary_version")

    def processing_flag(self, key: str) -> Any:
        processing = self._normalized.get("processing")
        if isinstance(processing, dict) and key in processing:
            return processing[key]
        return self._runtime.get(key)

    def detected_feed(self) -> dict[str, Any] | None:
        value = self.processing_flag("detected_feed")
        return value if isinstance(value, dict) else None

    def submission_user_id(self) -> int | None:
        value = self.processing_flag("submitted_by_user_id")
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def image_state(self) -> dict[str, Any]:
        return {
            "image_generated_at": self._runtime.get("image_generated_at"),
            "thumbnail_url": self._runtime.get("thumbnail_url"),
            "image_url": self._runtime.get("image_url"),
        }

    def news_fields(self) -> NewsFields:
        article = self._dict_field("article")
        aggregator = self._dict_field("aggregator")
        aggregator_metadata = aggregator.get("metadata", {})
        if not isinstance(aggregator_metadata, dict):
            aggregator_metadata = {}
        summary = self.summary() or {}
        key_points = summary.get("key_points")
        if not isinstance(key_points, list) or not key_points:
            fallback = self._runtime.get("summary_key_points")
            key_points = fallback if isinstance(fallback, list) and fallback else None
        return NewsFields(
            article=article,
            aggregator=aggregator,
            summary=summary,
            discussion_url=_str_or_none(
                self._runtime.get("discussion_url") or aggregator.get("url")
            ),
            summary_key_points=key_points,
            comment_count=_first_non_negative_int(
                self._runtime.get("comment_count"),
                aggregator_metadata.get("comments_count"),
            ),
        )

    def _dict_field(self, key: str) -> dict[str, Any]:
        value = self._runtime.get(key)
        return value if isinstance(value, dict) else {}


def metadata_view(metadata: dict[str, Any] | None) -> ContentMetadataView:
    return ContentMetadataView(metadata)


def summary(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    return metadata_view(metadata).summary()


def processing_flag(metadata: dict[str, Any] | None, key: str) -> Any:
    return metadata_view(metadata).processing_flag(key)


def detected_feed(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    return metadata_view(metadata).detected_feed()


def submission_user_id(metadata: dict[str, Any] | None) -> int | None:
    return metadata_view(metadata).submission_user_id()


def image_state(metadata: dict[str, Any] | None) -> dict[str, Any]:
    return metadata_view(metadata).image_state()


def news_fields(metadata: dict[str, Any] | None) -> NewsFields:
    return metadata_view(metadata).news_fields()


def _first_non_negative_int(*values: Any) -> int | None:
    for value in values:
        if value is None:
            continue
        try:
            return max(int(value), 0)
        except (TypeError, ValueError):
            continue
    return None


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
