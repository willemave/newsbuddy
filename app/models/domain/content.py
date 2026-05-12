from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from app.constants import (
    SUMMARY_KIND_LONG_BULLETS,
    SUMMARY_KIND_LONG_EDITORIAL_NARRATIVE,
    SUMMARY_KIND_LONG_INTERLEAVED,
    SUMMARY_KIND_LONG_STRUCTURED,
    SUMMARY_KIND_LONGFORM_ARTIFACT,
    SUMMARY_VERSION_V2,
)
from app.models.contracts import ContentStatus, ContentType
from app.models.metadata.articles import ArticleMetadata
from app.models.metadata.news import NewsMetadata
from app.models.metadata.podcasts import PodcastMetadata
from app.utils.summary_utils import extract_short_summary, extract_summary_text
from app.utils.title_utils import resolve_content_display_title


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
                cleaned_v = {}
                for key, value in v.items():
                    if value == "":
                        cleaned_v[key] = None
                    else:
                        cleaned_v[key] = value
                v = cleaned_v

            if content_type == ContentType.ARTICLE:
                # Validate article metadata
                try:
                    ArticleMetadata(**v)
                except Exception as e:
                    raise ValueError(f"Invalid article metadata: {e}") from e
            elif content_type == ContentType.PODCAST:
                # Validate podcast metadata
                try:
                    PodcastMetadata(**v)
                except Exception as e:
                    raise ValueError(f"Invalid podcast metadata: {e}") from e
            elif content_type == ContentType.NEWS:
                try:
                    return NewsMetadata(**v).model_dump(mode="json", exclude_none=True)
                except Exception as e:
                    raise ValueError(f"Invalid news metadata: {e}") from e
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
        summary_data = self.metadata.get("summary")
        summary_kind = self.metadata.get("summary_kind")
        if isinstance(summary_data, dict) and summary_kind in {
            SUMMARY_KIND_LONG_STRUCTURED,
            SUMMARY_KIND_LONG_INTERLEAVED,
            SUMMARY_KIND_LONG_BULLETS,
            SUMMARY_KIND_LONG_EDITORIAL_NARRATIVE,
            SUMMARY_KIND_LONGFORM_ARTIFACT,
        }:
            return summary_data
        # Legacy fallback: infer by payload shape
        if isinstance(summary_data, dict) and (
            "bullet_points" in summary_data
            or "insights" in summary_data
            or "editorial_narrative" in summary_data
            or ("artifact" in summary_data and "selection_trace" in summary_data)
        ):
            return summary_data
        return None

    @property
    def bullet_points(self) -> list[dict[str, str]]:
        """Get bullet points from structured or interleaved summary.

        For interleaved summaries, converts insights to bullet point format.
        """
        if not self.structured_summary:
            return []

        summary_kind = self.metadata.get("summary_kind")
        summary_version = self.metadata.get("summary_version")

        # Standard structured summary with bullet_points
        if summary_kind == SUMMARY_KIND_LONG_STRUCTURED:
            return self.structured_summary.get("bullet_points", [])

        if summary_kind == SUMMARY_KIND_LONG_INTERLEAVED:
            if summary_version == SUMMARY_VERSION_V2:
                return self.structured_summary.get("key_points", [])
            # Interleaved v1 - convert insights to bullet point format
            insights = self.structured_summary.get("insights", [])
            if insights:
                return [
                    {"text": ins.get("insight", ""), "category": ins.get("topic", "")}
                    for ins in insights
                    if ins.get("insight")
                ]
        if summary_kind == SUMMARY_KIND_LONG_BULLETS:
            points = self.structured_summary.get("points", [])
            if isinstance(points, list):
                return [
                    {"text": point.get("text", ""), "category": "key_point"}
                    for point in points
                    if isinstance(point, dict) and point.get("text")
                ]
        if summary_kind == SUMMARY_KIND_LONG_EDITORIAL_NARRATIVE:
            key_points = self.structured_summary.get("key_points", [])
            if isinstance(key_points, list):
                return [
                    {"text": point.get("point", ""), "category": "key_point"}
                    for point in key_points
                    if isinstance(point, dict) and point.get("point")
                ]
        if summary_kind == SUMMARY_KIND_LONGFORM_ARTIFACT:
            artifact = self.structured_summary.get("artifact")
            payload = artifact.get("payload") if isinstance(artifact, dict) else None
            raw_points = payload.get("key_points", []) if isinstance(payload, dict) else []
            artifact_type = artifact.get("type") if isinstance(artifact, dict) else None
            if isinstance(raw_points, list):
                return [
                    {
                        "text": " — ".join(
                            part
                            for part in (
                                str(point.get("heading") or "").strip(),
                                str(point.get("content") or "").strip(),
                            )
                            if part
                        ),
                        "category": str(artifact_type or "key_point"),
                    }
                    for point in raw_points
                    if isinstance(point, dict) and (point.get("heading") or point.get("content"))
                ]

        return []

    @property
    def quotes(self) -> list[dict[str, str]]:
        """Get quotes from structured or interleaved summary.

        For interleaved summaries, extracts supporting quotes from insights.
        """
        if not self.structured_summary:
            return []

        summary_kind = self.metadata.get("summary_kind")
        summary_version = self.metadata.get("summary_version")

        # Standard structured summary with quotes
        if summary_kind == SUMMARY_KIND_LONG_STRUCTURED:
            return self.structured_summary.get("quotes", [])

        if summary_kind == SUMMARY_KIND_LONG_INTERLEAVED:
            if summary_version == SUMMARY_VERSION_V2:
                return self.structured_summary.get("quotes", [])
            # Interleaved v1 - extract supporting quotes from insights
            insights = self.structured_summary.get("insights", [])
            quotes = []
            for ins in insights:
                quote_text = ins.get("supporting_quote")
                if quote_text:
                    quotes.append(
                        {
                            "text": quote_text,
                            "context": ins.get("quote_attribution", ins.get("topic", "")),
                        }
                    )
            return quotes
        if summary_kind == SUMMARY_KIND_LONG_BULLETS:
            points = self.structured_summary.get("points", [])
            if isinstance(points, list):
                flattened: list[dict[str, str]] = []
                for point in points:
                    if not isinstance(point, dict):
                        continue
                    for quote in point.get("quotes", []) or []:
                        if not isinstance(quote, dict):
                            continue
                        text = quote.get("text")
                        if text:
                            flattened.append(
                                {
                                    "text": text,
                                    "context": quote.get("context") or quote.get("attribution", ""),
                                }
                            )
                return flattened
        if summary_kind == SUMMARY_KIND_LONG_EDITORIAL_NARRATIVE:
            raw_quotes = self.structured_summary.get("quotes", [])
            if isinstance(raw_quotes, list):
                return [
                    {
                        "text": quote.get("text", ""),
                        "context": quote.get("attribution", ""),
                    }
                    for quote in raw_quotes
                    if isinstance(quote, dict) and quote.get("text")
                ]
        if summary_kind == SUMMARY_KIND_LONGFORM_ARTIFACT:
            artifact = self.structured_summary.get("artifact")
            payload = artifact.get("payload") if isinstance(artifact, dict) else None
            raw_quotes = payload.get("quotes", []) if isinstance(payload, dict) else []
            if isinstance(raw_quotes, list):
                return [
                    {
                        "text": quote.get("text", ""),
                        "context": quote.get("attribution", ""),
                    }
                    for quote in raw_quotes
                    if isinstance(quote, dict) and quote.get("text")
                ]

        return []

    @property
    def topics(self) -> list[str]:
        """Get topics from structured or interleaved summary.

        For interleaved summaries, extracts unique topic names from insights.
        """
        if self.structured_summary:
            summary_kind = self.metadata.get("summary_kind")
            summary_version = self.metadata.get("summary_version")

            # Standard topics array
            if summary_kind == SUMMARY_KIND_LONG_STRUCTURED:
                raw_topics = self.structured_summary.get("topics", [])
                if isinstance(raw_topics, list):
                    return [topic for topic in raw_topics if isinstance(topic, str)]
                return []

            if summary_kind == SUMMARY_KIND_LONG_INTERLEAVED:
                if summary_version == SUMMARY_VERSION_V2:
                    topics = self.structured_summary.get("topics", [])
                    if isinstance(topics, list):
                        extracted_topics: list[str] = []
                        for topic in topics:
                            if not isinstance(topic, dict):
                                continue
                            topic_name = topic.get("topic")
                            if isinstance(topic_name, str) and topic_name:
                                extracted_topics.append(topic_name)
                        return extracted_topics
                # Interleaved v1 - extract unique topics from insights
                insights = self.structured_summary.get("insights", [])
                if insights:
                    seen = set()
                    topics = []
                    for ins in insights:
                        topic = ins.get("topic")
                        if topic and topic not in seen:
                            seen.add(topic)
                            topics.append(topic)
                    return topics
            if summary_kind == SUMMARY_KIND_LONG_BULLETS:
                return []
            if summary_kind == SUMMARY_KIND_LONG_EDITORIAL_NARRATIVE:
                return []
            if summary_kind == SUMMARY_KIND_LONGFORM_ARTIFACT:
                artifact = self.structured_summary.get("artifact")
                if isinstance(artifact, dict) and isinstance(artifact.get("type"), str):
                    return [artifact["type"]]
                return []

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
