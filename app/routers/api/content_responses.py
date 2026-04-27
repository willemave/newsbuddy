"""API response builders for normalized content."""

from typing import Any

from app.models.api.common import ContentDetailResponse, ContentSummaryResponse, DetectedFeed
from app.models.content_display import resolve_image_urls
from app.models.contracts import ContentClassification, ContentStatus
from app.models.metadata import ContentData, ContentType
from app.models.metadata_access import metadata_view
from app.models.schema import Content
from app.services.content_bodies import sanitize_metadata_for_api
from app.utils.image_urls import build_content_image_url, build_thumbnail_url


def _require_content_id(content_id: int | None) -> int:
    if content_id is None:
        raise ValueError("Content is missing an id")
    return content_id


def _extract_news_summary(domain_content: ContentData) -> dict[str, Any]:
    view = metadata_view(domain_content.metadata)
    fields = view.news_fields()
    news_article_url = str(domain_content.url) if domain_content.url else fields.article.get("url")

    return {
        "news_article_url": news_article_url,
        "news_discussion_url": fields.discussion_url,
        "news_key_points": fields.summary_key_points,
        "news_summary_text": domain_content.summary,
        "classification": fields.summary.get("classification"),
        "comment_count": fields.comment_count,
    }


def build_content_summary_response(
    content: Content,
    domain_content: ContentData,
    is_read: bool,
    is_saved_to_knowledge: bool,
    image_url: str | None = None,
    thumbnail_url: str | None = None,
) -> ContentSummaryResponse:
    """Build a summary response for list/search views."""
    content_id = _require_content_id(domain_content.id)
    if image_url is None or thumbnail_url is None:
        image_url, thumbnail_url = resolve_image_urls(domain_content)

    classification = None
    if domain_content.structured_summary:
        classification = domain_content.structured_summary.get("classification")

    news_article_url = None
    news_discussion_url = None
    news_key_points = None
    news_summary_text = domain_content.short_summary
    metadata = metadata_view(domain_content.metadata)
    discussion_url = metadata.get("discussion_url")
    comment_count: int | None = None

    if domain_content.content_type == ContentType.NEWS:
        news_fields = _extract_news_summary(domain_content)
        news_article_url = news_fields["news_article_url"]
        news_discussion_url = news_fields["news_discussion_url"]
        news_key_points = news_fields["news_key_points"]
        news_summary_text = news_fields["news_summary_text"]
        classification = news_fields["classification"] or classification
        comment_count = news_fields["comment_count"]
        discussion_url = news_discussion_url

    primary_topic = None
    topics = domain_content.topics
    if topics:
        candidate = str(topics[0]).strip()
        if candidate:
            primary_topic = candidate
    if primary_topic is None and domain_content.content_type == ContentType.NEWS:
        platform = (domain_content.platform or content.platform or "").strip()
        if platform:
            primary_topic = platform

    raw_top_comment = metadata.get("top_comment")
    top_comment: dict[str, str] | None = None
    if isinstance(raw_top_comment, dict):
        author = str(raw_top_comment.get("author") or "unknown").strip() or "unknown"
        text = str(raw_top_comment.get("text") or "").strip()
        if text:
            top_comment = {"author": author, "text": text}

    return ContentSummaryResponse(
        id=content_id,
        content_type=domain_content.content_type,
        url=str(domain_content.url),
        source_url=domain_content.source_url,
        title=domain_content.display_title,
        source=domain_content.source,
        platform=domain_content.platform or content.platform,
        status=domain_content.status,
        discussion_url=discussion_url,
        short_summary=news_summary_text,
        created_at=domain_content.created_at.isoformat() if domain_content.created_at else "",
        processed_at=(
            domain_content.processed_at.isoformat() if domain_content.processed_at else None
        ),
        classification=classification,
        publication_date=domain_content.publication_date.isoformat()
        if domain_content.publication_date
        else None,
        is_read=is_read,
        is_saved_to_knowledge=is_saved_to_knowledge,
        news_article_url=news_article_url,
        news_discussion_url=news_discussion_url,
        news_key_points=news_key_points,
        news_summary=news_summary_text,
        user_status="inbox"
        if domain_content.content_type in (ContentType.ARTICLE, ContentType.PODCAST)
        else None,
        image_url=image_url,
        thumbnail_url=thumbnail_url,
        primary_topic=primary_topic,
        top_comment=top_comment,
        comment_count=comment_count,
    )


def build_fallback_content_summary_response(
    content: Content,
    *,
    is_read: bool,
    is_saved_to_knowledge: bool,
) -> ContentSummaryResponse | None:
    """Build a minimal summary response when full metadata normalization fails."""
    metadata = metadata_view(
        content.content_metadata if isinstance(content.content_metadata, dict) else {}
    )
    short_summary = content.short_summary
    if not short_summary:
        return None
    content_id = _require_content_id(content.id)
    raw_content_type = content.content_type
    raw_url = content.url
    raw_status = content.status
    if raw_content_type is None or raw_url is None or raw_status is None:
        return None
    classification = None
    if content.classification in {
        ContentClassification.TO_READ.value,
        ContentClassification.SKIP.value,
    }:
        classification = ContentClassification(content.classification)

    image_url: str | None = None
    thumbnail_url: str | None = None
    if content.content_type in {
        ContentType.ARTICLE.value,
        ContentType.PODCAST.value,
    } and metadata.image_state().get("image_generated_at"):
        image_url = build_content_image_url(content_id)
        thumbnail_url = build_thumbnail_url(content_id)
    elif content.content_type == ContentType.PODCAST.value:
        raw_thumbnail = metadata.image_state().get("thumbnail_url")
        if isinstance(raw_thumbnail, str) and raw_thumbnail.startswith("http"):
            image_url = raw_thumbnail

    return ContentSummaryResponse(
        id=content_id,
        content_type=ContentType(raw_content_type),
        url=raw_url,
        source_url=content.source_url,
        title=content.title,
        source=content.source,
        platform=content.platform,
        status=ContentStatus(raw_status),
        discussion_url=(
            metadata.get("discussion_url")
            if isinstance(metadata.get("discussion_url"), str)
            else None
        ),
        short_summary=short_summary,
        created_at=content.created_at.isoformat() if content.created_at else "",
        processed_at=content.processed_at.isoformat() if content.processed_at else None,
        classification=classification,
        publication_date=content.publication_date.isoformat() if content.publication_date else None,
        is_read=is_read,
        is_saved_to_knowledge=is_saved_to_knowledge,
        news_article_url=None,
        news_discussion_url=None,
        news_key_points=None,
        news_summary=None,
        user_status="inbox"
        if content.content_type in (ContentType.ARTICLE.value, ContentType.PODCAST.value)
        else None,
        image_url=image_url,
        thumbnail_url=thumbnail_url,
        primary_topic=None,
        top_comment=None,
        comment_count=None,
    )


def build_content_detail_response(
    content: Content,
    domain_content: ContentData,
    is_read: bool,
    is_saved_to_knowledge: bool,
    detected_feed_data: dict[str, Any] | None,
    can_subscribe: bool,
    *,
    body_available: bool = False,
    body_kind: str | None = None,
    body_format: str | None = None,
) -> ContentDetailResponse:
    """Build a detail response for content."""
    content_id = _require_content_id(domain_content.id)
    image_url, thumbnail_url = resolve_image_urls(domain_content)

    structured_summary = domain_content.structured_summary
    bullet_points = domain_content.bullet_points
    quotes = domain_content.quotes
    topics = domain_content.topics
    full_markdown = None
    metadata = metadata_view(domain_content.metadata)
    summary_kind = metadata.summary_kind()
    summary_version = metadata.summary_version()
    news_article_url = None
    news_discussion_url = None
    news_key_points = None
    news_summary_text = domain_content.summary
    discussion_url = metadata.get("discussion_url")

    if domain_content.content_type == ContentType.NEWS:
        news_fields = _extract_news_summary(domain_content)
        news_article_url = news_fields["news_article_url"]
        news_discussion_url = news_fields["news_discussion_url"]
        news_key_points = news_fields["news_key_points"]
        news_summary_text = news_fields["news_summary_text"]
        structured_summary = None
        bullet_points = []
        quotes = []
        topics = []
        full_markdown = None
        discussion_url = news_discussion_url

    detected_feed = None
    if detected_feed_data:
        detected_feed = DetectedFeed(
            url=detected_feed_data["url"],
            type=detected_feed_data["type"],
            title=detected_feed_data.get("title"),
            format=detected_feed_data.get("format", "rss"),
        )

    return ContentDetailResponse(
        id=content_id,
        content_type=domain_content.content_type,
        url=str(domain_content.url),
        source_url=domain_content.source_url,
        title=domain_content.title,
        display_title=domain_content.display_title,
        source=domain_content.source,
        status=domain_content.status,
        discussion_url=discussion_url,
        error_message=domain_content.error_message,
        retry_count=domain_content.retry_count,
        metadata=sanitize_metadata_for_api(domain_content.metadata or {}),
        created_at=domain_content.created_at.isoformat() if domain_content.created_at else "",
        updated_at=content.updated_at.isoformat() if content.updated_at else None,
        processed_at=domain_content.processed_at.isoformat()
        if domain_content.processed_at
        else None,
        checked_out_by=content.checked_out_by,
        checked_out_at=content.checked_out_at.isoformat() if content.checked_out_at else None,
        publication_date=domain_content.publication_date.isoformat()
        if domain_content.publication_date
        else None,
        is_read=is_read,
        is_saved_to_knowledge=is_saved_to_knowledge,
        summary=news_summary_text,
        short_summary=news_summary_text,
        summary_kind=summary_kind,
        summary_version=summary_version,
        structured_summary=structured_summary,
        bullet_points=bullet_points,
        quotes=quotes,
        topics=topics,
        full_markdown=full_markdown,
        body_available=body_available,
        body_kind=body_kind,
        body_format=body_format,
        news_article_url=news_article_url,
        news_discussion_url=news_discussion_url,
        news_key_points=news_key_points,
        news_summary=news_summary_text,
        image_url=image_url,
        thumbnail_url=thumbnail_url,
        detected_feed=detected_feed,
        can_subscribe=can_subscribe,
    )
