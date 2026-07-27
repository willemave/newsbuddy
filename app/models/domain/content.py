from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from app.models.contracts import ContentStatus, ContentType
from app.models.domain import summary_projection
from app.models.metadata.articles import ArticleMetadata
from app.models.metadata.news import NewsMetadata
from app.models.metadata.podcasts import PodcastMetadata
from app.utils.summary_utils import extract_short_summary, extract_summary_text
from app.utils.title_utils import resolve_content_display_title


def _clean_metadata_values(metadata: dict[str, Any]) -> dict[str, Any]:
    return {key: None if value == "" else value for key, value in metadata.items()}


def _validate_metadata_model(
    model_type: type[BaseModel],
    value: dict[str, Any],
    *,
    label: str,
    exclude_unset: bool = True,
    exclude_none: bool = False,
) -> dict[str, Any]:
    """Validate metadata once and keep the normalized JSON-compatible result."""
    try:
        return model_type.model_validate(value).model_dump(
            mode="json",
            exclude_none=exclude_none,
            exclude_unset=exclude_unset,
        )
    except Exception as exc:
        raise ValueError(f"Invalid {label} metadata: {exc}") from exc


class ContentData(BaseModel):
    """
    Unified content data model for passing between layers.
    """

    model_config = ConfigDict(ignored_types=(property,))

    id: int | None = None
    content_type: ContentType
    url: HttpUrl
    source_url: str | None = None
    title: str | None = None
    status: ContentStatus = ContentStatus.NEW
    metadata: dict[str, Any] = Field(default_factory=dict)

    platform: str | None = Field(default=None, exclude=True)
    source: str | None = Field(default=None, exclude=True)

    # Processing metadata
    error_message: str | None = None
    retry_count: int = 0

    # Timestamps
    created_at: datetime | None = None
    processed_at: datetime | None = None
    publication_date: datetime | None = None

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, v, info):
        """Ensure metadata matches content type."""
        if info.data:
            content_type = info.data.get("content_type")

            # Clean up empty strings in metadata
            if isinstance(v, dict):
                v = _clean_metadata_values(v)

            if content_type == ContentType.ARTICLE:
                return _validate_metadata_model(ArticleMetadata, v, label="article")
            elif content_type == ContentType.PODCAST:
                return _validate_metadata_model(PodcastMetadata, v, label="podcast")
            elif content_type == ContentType.NEWS:
                return _validate_metadata_model(
                    NewsMetadata,
                    v,
                    label="news",
                    exclude_unset=False,
                    exclude_none=True,
                )
        return v

    @property
    def summary(self) -> str | None:
        """Get summary text (overview, hook, or plain summary)."""
        summary_data = self.metadata.get("summary")
        if not summary_data:
            if self.content_type == ContentType.NEWS:
                excerpt = self.metadata.get("excerpt")
                if excerpt:
                    return excerpt
            return None
        summary_text = extract_summary_text(summary_data)
        if summary_text:
            return summary_text
        return None

    @property
    def display_title(self) -> str:
        """Get title to display - prefer summary title over content title."""
        return resolve_content_display_title(title=self.title, metadata=self.metadata)

    @property
    def short_summary(self) -> str | None:
        """Get short version of summary for list view."""
        return extract_short_summary(self.metadata.get("summary"))

    @property
    def structured_summary(self) -> dict[str, Any] | None:
        """Get structured or interleaved summary if available."""
        return summary_projection.structured_summary(self.metadata)

    @property
    def bullet_points(self) -> list[dict[str, str]]:
        """Get bullet points from structured or interleaved summary.

        For interleaved summaries, converts insights to bullet point format.
        """
        return summary_projection.bullet_points(self.metadata)

    @property
    def quotes(self) -> list[dict[str, str]]:
        """Get quotes from structured or interleaved summary.

        For interleaved summaries, extracts supporting quotes from insights.
        """
        return summary_projection.quotes(self.metadata)

    @property
    def topics(self) -> list[str]:
        """Get topics from structured or interleaved summary.

        For interleaved summaries, extracts unique topic names from insights.
        """
        projected_topics = summary_projection.topics(self.metadata)
        if projected_topics is not None:
            return projected_topics

        return self.metadata.get("topics", [])

    @property
    def transcript(self) -> str | None:
        """Get transcript for podcasts."""
        if self.content_type == ContentType.PODCAST:
            return self.metadata.get("transcript")
        return None

    @property
    def full_markdown(self) -> str | None:
        """Get full article content formatted as markdown from StructuredSummary."""
        summary_data = self.metadata.get("summary")
        if isinstance(summary_data, dict):
            return summary_data.get("full_markdown")
        return None

    @model_validator(mode="after")
    def populate_source_fields(self) -> ContentData:
        """Backfill platform/source fields from metadata when missing."""
        if self.platform is None:
            metadata_platform = self.metadata.get("platform")
            if isinstance(metadata_platform, str) and metadata_platform.strip():
                self.platform = metadata_platform.strip()
        if self.source is None:
            metadata_source = self.metadata.get("source")
            if isinstance(metadata_source, str) and metadata_source.strip():
                self.source = metadata_source.strip()
        return self

    def model_dump(self, *args, **kwargs):
        excludes = kwargs.pop("exclude", set())
        excludes = set(excludes) | {"platform", "source"}
        data = super().model_dump(*args, exclude=excludes, **kwargs)
        metadata = data.get("metadata") or {}
        platform = metadata.get("platform")
        source = metadata.get("source")
        if platform is not None:
            data["platform"] = platform
        if source is not None:
            data["source"] = source
        return data
