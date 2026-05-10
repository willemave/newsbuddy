from __future__ import annotations

from typing import Any

from app.models.contracts import ContentType
from app.models.metadata.articles import ArticleMetadata
from app.models.metadata.insight_reports import InsightReportMetadata
from app.models.metadata.news import NewsMetadata
from app.models.metadata.podcasts import PodcastMetadata


def validate_content_metadata(
    content_type: str, metadata: dict[str, Any]
) -> ArticleMetadata | PodcastMetadata | NewsMetadata | InsightReportMetadata:
    """
    Validate and parse metadata based on content type.

    Args:
        content_type: Type of content ('article', 'podcast', 'news', 'insight_report')
        metadata: Raw metadata dictionary

    Returns:
        Validated metadata model

    Raises:
        ValueError: If content_type is unknown
        ValidationError: If metadata doesn't match schema
    """
    # Remove error fields if present (they should be in separate columns)
    cleaned_metadata = {k: v for k, v in metadata.items() if k not in ["error", "error_type"]}

    if content_type == ContentType.ARTICLE.value:
        return ArticleMetadata(**cleaned_metadata)
    if content_type == ContentType.PODCAST.value:
        return PodcastMetadata(**cleaned_metadata)
    if content_type == ContentType.NEWS.value:
        return NewsMetadata(**cleaned_metadata)
    if content_type == ContentType.INSIGHT_REPORT.value:
        return InsightReportMetadata(**cleaned_metadata)
    if content_type == ContentType.UNKNOWN.value:
        # UNKNOWN content uses minimal ArticleMetadata as placeholder
        return ArticleMetadata(**cleaned_metadata)
    raise ValueError(f"Unknown content type: {content_type}")
