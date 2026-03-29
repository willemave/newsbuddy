"""Normalization and summarization for short-form news items."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.contracts import NewsItemStatus
from app.models.metadata import NewsSummary
from app.models.schema import NewsItem
from app.services.discussion_fetcher import _build_discussion_payload
from app.services.llm_summarization import ContentSummarizer, get_content_summarizer
from app.utils.url_utils import normalize_http_url

logger = get_logger(__name__)

DISCUSSION_COMMENT_CAP = 50
MAX_DISCUSSION_SNIPPETS = 5


@dataclass(frozen=True)
class NewsItemProcessingResult:
    """Outcome for one news item normalization attempt."""

    success: bool
    status: str
    error_message: str | None = None
    retryable: bool = True
    used_existing_summary: bool = False
    generated_summary: bool = False


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _clean_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned or None


def _normalize_key_points(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    points: list[str] = []
    for raw in value:
        text = _clean_string(raw.get("text")) if isinstance(raw, dict) else _clean_string(raw)
        if text:
            points.append(text)
    return points


def _extract_existing_summary(item: NewsItem) -> NewsSummary | None:
    if item.summary_title or item.summary_key_points or item.summary_text:
        return NewsSummary(
            title=item.summary_title,
            article_url=item.article_url,
            key_points=_normalize_key_points(item.summary_key_points),
            summary=item.summary_text,
        )

    raw_metadata = dict(item.raw_metadata or {})
    summary = raw_metadata.get("summary")
    if not isinstance(summary, dict):
        return None
    return NewsSummary(
        title=_clean_string(summary.get("title")) or item.article_title,
        article_url=_clean_string(summary.get("article_url")) or item.article_url,
        key_points=_normalize_key_points(summary.get("key_points")),
        summary=_clean_string(summary.get("summary")),
    )


def _extract_discussion_snippets(raw_metadata: dict[str, Any]) -> list[str]:
    discussion = raw_metadata.get("discussion_payload")
    if not isinstance(discussion, dict):
        return []
    compact_comments = discussion.get("compact_comments")
    if isinstance(compact_comments, list):
        snippets = [_clean_string(comment) for comment in compact_comments]
        return [snippet for snippet in snippets if snippet][:MAX_DISCUSSION_SNIPPETS]

    comments = discussion.get("comments")
    if not isinstance(comments, list):
        return []
    snippets: list[str] = []
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        text = _clean_string(comment.get("compact_text") or comment.get("text"))
        if text:
            snippets.append(text)
        if len(snippets) >= MAX_DISCUSSION_SNIPPETS:
            break
    return snippets


def _build_processing_prompt(item: NewsItem, raw_metadata: dict[str, Any]) -> str:
    lines = [
        "Create a compact short-form news summary grounded only in this evidence.",
    ]
    if item.source_label:
        lines.append(f"Source label: {item.source_label}")
    if item.platform:
        lines.append(f"Platform: {item.platform}")
    if item.article_title:
        lines.append(f"Article title: {item.article_title}")
    if item.article_domain:
        lines.append(f"Article domain: {item.article_domain}")
    if item.article_url:
        lines.append(f"Article URL: {item.article_url}")

    aggregator = raw_metadata.get("aggregator")
    if isinstance(aggregator, dict):
        if _clean_string(aggregator.get("title")):
            lines.append(f"Aggregator title: {_clean_string(aggregator.get('title'))}")
        if _clean_string(aggregator.get("author")):
            lines.append(f"Aggregator author: {_clean_string(aggregator.get('author'))}")

    excerpt = _clean_string(raw_metadata.get("excerpt"))
    if excerpt:
        lines.extend(["", "Excerpt:", excerpt])

    discussion_snippets = _extract_discussion_snippets(raw_metadata)
    if discussion_snippets:
        lines.extend(["", "Discussion snippets:"])
        lines.extend(f"- {snippet}" for snippet in discussion_snippets)

    return "\n".join(lines)


def _fallback_summary(item: NewsItem, raw_metadata: dict[str, Any]) -> NewsSummary:
    key_points = _normalize_key_points(item.summary_key_points)
    if not key_points:
        snippet = _clean_string(raw_metadata.get("excerpt")) or _clean_string(item.summary_text)
        if snippet:
            key_points = [snippet[:220]]

    summary_text = item.summary_text or (key_points[0] if key_points else item.article_title)
    return NewsSummary(
        title=item.summary_title or item.article_title or f"News item {item.id}",
        article_url=item.article_url,
        key_points=key_points[:5],
        summary=summary_text,
    )


def _persist_summary(item: NewsItem, summary: NewsSummary, raw_metadata: dict[str, Any]) -> None:
    item.summary_title = _clean_string(summary.title) or item.article_title or item.summary_title
    normalized_article_url = (
        normalize_http_url(summary.article_url) if summary.article_url else None
    )
    if normalized_article_url:
        item.article_url = normalized_article_url
        item.canonical_story_url = normalized_article_url
    item.summary_key_points = _normalize_key_points(summary.key_points)
    item.summary_text = _clean_string(summary.summary) or item.summary_text
    item.raw_metadata = raw_metadata
    item.status = NewsItemStatus.READY.value
    item.processed_at = _utcnow_naive()


def process_news_item(
    db: Session,
    *,
    news_item_id: int,
    summarizer: ContentSummarizer | None = None,
) -> NewsItemProcessingResult:
    """Normalize one ``news_items`` row into digest-ready fields.

    Args:
        db: Active SQLAlchemy session.
        news_item_id: News item identifier.
        summarizer: Optional injected summarizer for tests.

    Returns:
        Processing outcome with retry guidance.
    """
    item = db.query(NewsItem).filter(NewsItem.id == news_item_id).first()
    if item is None:
        return NewsItemProcessingResult(
            success=False,
            status="failed",
            error_message="News item not found",
            retryable=False,
        )

    raw_metadata = dict(item.raw_metadata or {})
    item.status = NewsItemStatus.PROCESSING.value
    db.flush()

    try:
        discussion_payload = raw_metadata.get("discussion_payload")
        if not isinstance(discussion_payload, dict):
            discussion = _build_discussion_payload(
                platform=item.platform or "",
                discussion_url=item.discussion_url,
                metadata=raw_metadata,
                comment_cap=DISCUSSION_COMMENT_CAP,
            )
            raw_metadata["discussion_payload"] = discussion.payload
            if discussion.error_message:
                raw_metadata["discussion_error"] = discussion.error_message

        existing_summary = _extract_existing_summary(item)
        if existing_summary is not None and (
            existing_summary.title or existing_summary.key_points or existing_summary.summary
        ):
            _persist_summary(item, existing_summary, raw_metadata)
            db.flush()
            return NewsItemProcessingResult(
                success=True,
                status=item.status,
                used_existing_summary=True,
            )

        prompt = _build_processing_prompt(item, raw_metadata)
        content_summarizer = summarizer or get_content_summarizer()
        generated = content_summarizer.summarize(
            prompt,
            content_type="news",
            title=item.article_title or item.summary_title,
            content_id=item.id,
        )
        if not isinstance(generated, NewsSummary):
            raise TypeError(
                "Short-form news summarizer returned an invalid payload: "
                f"{type(generated).__name__}"
            )

        _persist_summary(item, generated, raw_metadata)
        db.flush()
        return NewsItemProcessingResult(
            success=True,
            status=item.status,
            generated_summary=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "News item processing failed",
            extra={
                "component": "news_processing",
                "operation": "process_news_item",
                "item_id": str(news_item_id),
                "context_data": {"error": str(exc)},
            },
        )
        raw_metadata["processing_error"] = str(exc)
        item.raw_metadata = raw_metadata
        item.status = NewsItemStatus.FAILED.value
        item.processed_at = _utcnow_naive()
        db.flush()
        return NewsItemProcessingResult(
            success=False,
            status=item.status,
            error_message=str(exc),
            retryable=True,
        )
