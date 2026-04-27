"""Adapters from canonical news_items rows to content-card API contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.models.api.common import ContentDetailResponse, ContentSummaryResponse
from app.models.contracts import ContentClassification, ContentStatus, ContentType
from app.models.schema import NewsItem
from app.utils.news_titles import (
    get_news_article_title,
    resolve_news_display_title,
    resolve_news_summary_title,
)
from app.utils.url_utils import normalize_http_url


def _require_news_item_id(item: NewsItem) -> int:
    item_id = item.id
    if item_id is None:
        raise ValueError("News item is missing an id")
    return int(item_id)


def _require_news_item_created_at(item: NewsItem) -> datetime:
    created_at = item.ingested_at or item.created_at
    if created_at is not None:
        return created_at
    return datetime.now(UTC).replace(tzinfo=None)


def _resolve_item_url(item: NewsItem) -> str:
    for candidate in (
        item.article_url,
        item.canonical_story_url,
        item.discussion_url,
        item.canonical_item_url,
    ):
        normalized = normalize_http_url(candidate) if candidate else None
        if normalized:
            return normalized
    return f"https://newsly.invalid/news-items/{item.id}"


def _cluster_metadata(item: NewsItem) -> dict[str, Any]:
    raw_metadata = dict(item.raw_metadata or {})
    cluster = raw_metadata.get("cluster")
    return cluster if isinstance(cluster, dict) else {}


def _top_comment(item: NewsItem) -> dict[str, str] | None:
    raw_top_comment = dict(item.raw_metadata or {}).get("top_comment")
    if not isinstance(raw_top_comment, dict):
        return None
    author = str(raw_top_comment.get("author") or "unknown").strip() or "unknown"
    text = str(raw_top_comment.get("text") or "").strip()
    if not text:
        return None
    return {"author": author, "text": text}


def _comment_count(item: NewsItem) -> int | None:
    """Return the best available discussion count for a news item."""
    metadata = dict(item.raw_metadata or {})
    aggregator = metadata.get("aggregator")
    aggregator_metadata = aggregator.get("metadata") if isinstance(aggregator, dict) else None

    for raw in (
        metadata.get("comment_count"),
        (
            aggregator_metadata.get("comments_count")
            if isinstance(aggregator_metadata, dict)
            else None
        ),
    ):
        if raw is None:
            continue
        try:
            return max(int(raw), 0)
        except (TypeError, ValueError):
            continue

    cluster_size = item.cluster_size
    if cluster_size is not None and cluster_size > 1:
        return cluster_size - 1
    return None


def _content_status(item: NewsItem) -> ContentStatus:
    if item.status == "failed":
        return ContentStatus.FAILED
    if item.status == "processing":
        return ContentStatus.PROCESSING
    if item.status == "new":
        return ContentStatus.NEW
    return ContentStatus.COMPLETED


def _content_classification(item: NewsItem) -> ContentClassification | None:
    raw_summary = dict(item.raw_metadata or {}).get("summary")
    if not isinstance(raw_summary, dict):
        return None
    classification = raw_summary.get("classification")
    if classification in {ContentClassification.TO_READ.value, ContentClassification.SKIP.value}:
        return ContentClassification(classification)
    return None


def _news_item_display_title(item: NewsItem) -> str:
    """Resolve a news-item title that avoids placeholder source labels."""
    return resolve_news_display_title(
        item.raw_metadata,
        summary_text=item.summary_text,
        fallback=f"News item {item.id}",
    )


def present_news_item_summary(
    item: NewsItem,
    *,
    is_read: bool,
) -> ContentSummaryResponse:
    """Emit a legacy content-card summary directly from a canonical news item."""
    cluster = _cluster_metadata(item)
    discussion_snippets = cluster.get("discussion_snippets")
    top_comment = _top_comment(item)
    if top_comment is None and isinstance(discussion_snippets, list) and discussion_snippets:
        top_comment = {"author": "Related", "text": str(discussion_snippets[0]).strip()}
    display_title = _news_item_display_title(item)

    return ContentSummaryResponse(
        id=_require_news_item_id(item),
        content_type=ContentType.NEWS,
        url=_resolve_item_url(item),
        source_url=item.canonical_item_url or item.discussion_url,
        discussion_url=item.discussion_url,
        title=display_title,
        source=item.source_label,
        platform=item.platform,
        status=_content_status(item),
        short_summary=item.summary_text,
        created_at=_require_news_item_created_at(item).isoformat(),
        processed_at=item.processed_at.isoformat() if item.processed_at else None,
        classification=_content_classification(item),
        publication_date=item.published_at.isoformat() if item.published_at else None,
        is_read=is_read,
        is_saved_to_knowledge=False,
        news_article_url=item.article_url or item.canonical_story_url,
        news_discussion_url=item.discussion_url or item.canonical_item_url,
        news_key_points=list(item.summary_key_points or []) or None,
        news_summary=item.summary_text,
        user_status=None,
        image_url=None,
        thumbnail_url=None,
        primary_topic=None,
        top_comment=top_comment,
        comment_count=_comment_count(item),
    )


def present_news_item_detail(
    item: NewsItem,
    *,
    is_read: bool,
) -> ContentDetailResponse:
    """Emit a legacy content detail response directly from a canonical news item."""
    metadata = dict(item.raw_metadata or {})
    display_title = _news_item_display_title(item)
    article = metadata.get("article")
    if not isinstance(article, dict):
        article = {}
    article.setdefault("url", item.article_url or item.canonical_story_url)
    article["title"] = get_news_article_title(metadata) or display_title
    article.setdefault("source_domain", item.article_domain)
    metadata["article"] = article

    summary = metadata.get("summary")
    if not isinstance(summary, dict):
        summary = {}
    raw_summary_text = summary.get("summary")
    summary_text = raw_summary_text if isinstance(raw_summary_text, str) else item.summary_text
    summary_title = resolve_news_summary_title(metadata, summary_text=summary_text)
    article_url = item.article_url or item.canonical_story_url
    summary_key_points = list(item.summary_key_points or [])
    if summary_title:
        summary["title"] = summary_title
    if article_url and not summary.get("article_url"):
        summary["article_url"] = article_url
    if summary_key_points and not summary.get("key_points"):
        summary["key_points"] = summary_key_points
    if item.summary_text and not summary.get("summary"):
        summary["summary"] = item.summary_text
    metadata["summary"] = summary

    metadata.setdefault("discussion_url", item.discussion_url)
    metadata.setdefault("cluster", _cluster_metadata(item))

    return ContentDetailResponse(
        id=_require_news_item_id(item),
        content_type=ContentType.NEWS,
        url=_resolve_item_url(item),
        source_url=item.canonical_item_url or item.discussion_url,
        discussion_url=item.discussion_url,
        title=display_title,
        display_title=display_title,
        source=item.source_label,
        status=_content_status(item),
        error_message=None,
        retry_count=0,
        metadata=metadata,
        created_at=_require_news_item_created_at(item).isoformat(),
        updated_at=item.updated_at.isoformat() if item.updated_at else None,
        processed_at=item.processed_at.isoformat() if item.processed_at else None,
        checked_out_by=None,
        checked_out_at=None,
        publication_date=item.published_at.isoformat() if item.published_at else None,
        body_available=False,
        body_kind=None,
        body_format=None,
        is_read=is_read,
        is_saved_to_knowledge=False,
        summary=item.summary_text,
        short_summary=item.summary_text,
        summary_kind=None,
        summary_version=None,
        structured_summary=None,
        bullet_points=[],
        quotes=[],
        topics=[],
        full_markdown=None,
        news_article_url=item.article_url or item.canonical_story_url,
        news_discussion_url=item.discussion_url or item.canonical_item_url,
        news_key_points=list(item.summary_key_points or []) or None,
        news_summary=item.summary_text,
        image_url=None,
        thumbnail_url=None,
        detected_feed=None,
        can_subscribe=False,
    )
