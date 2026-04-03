"""Normalization and summarization for short-form news items."""

from __future__ import annotations

import inspect
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
from app.services.news_relations import reconcile_news_item_relation
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


def _has_materialized_summary(
    *,
    key_points: list[str],
    summary_text: str | None,
) -> bool:
    return bool(key_points or summary_text)


def _extract_existing_summary(item: NewsItem) -> NewsSummary | None:
    item_key_points = _normalize_key_points(item.summary_key_points)
    if _has_materialized_summary(
        key_points=item_key_points,
        summary_text=item.summary_text,
    ):
        return NewsSummary(
            title=item.summary_title,
            article_url=item.article_url,
            key_points=item_key_points,
            summary=item.summary_text,
        )

    raw_metadata = dict(item.raw_metadata or {})
    summary = raw_metadata.get("summary")
    if not isinstance(summary, dict):
        return None
    summary_key_points = _normalize_key_points(summary.get("key_points"))
    summary_text = _clean_string(summary.get("summary"))
    if not _has_materialized_summary(
        key_points=summary_key_points,
        summary_text=summary_text,
    ):
        return None
    return NewsSummary(
        title=_clean_string(summary.get("title")) or item.article_title,
        article_url=_clean_string(summary.get("article_url")) or item.article_url,
        key_points=summary_key_points,
        summary=summary_text,
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


def _finalize_processed_item(
    db: Session,
    *,
    item: NewsItem,
    raw_metadata: dict[str, Any],
    summary: NewsSummary,
) -> None:
    """Persist one summary, reconcile clustering, and commit."""
    _persist_summary(item, summary, raw_metadata)
    reconcile_news_item_relation(db, news_item_id=item.id)
    db.commit()


def _summarizer_accepts_context_kwargs(summarizer: object) -> bool:
    """Return whether a summarizer callable can accept ``db`` context kwargs."""
    summarize = getattr(summarizer, "summarize", None)
    if summarize is None:
        return False
    try:
        signature = inspect.signature(summarize)
    except (TypeError, ValueError):
        return True
    for parameter in signature.parameters.values():
        if parameter.kind == inspect.Parameter.VAR_KEYWORD:
            return True
    return "db" in signature.parameters or "usage_persist" in signature.parameters


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
    db.commit()
    db.refresh(item)

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

        summary_to_persist = _extract_existing_summary(item)
        used_existing_summary = bool(
            summary_to_persist
            and (
                summary_to_persist.title
                or summary_to_persist.key_points
                or summary_to_persist.summary
            )
        )
        if not used_existing_summary:
            prompt = _build_processing_prompt(item, raw_metadata)
            content_summarizer = summarizer or get_content_summarizer()
            summarize_kwargs: dict[str, object] = {
                "content_type": "news",
                "title": item.article_title or item.summary_title,
                "content_id": item.id,
            }
            if summarizer is None or _summarizer_accepts_context_kwargs(content_summarizer):
                summarize_kwargs["db"] = db
                summarize_kwargs["usage_persist"] = {
                    "feature": "news_processing",
                    "operation": "news_processing.summarize_short_form",
                    "source": "queue",
                    "user_id": item.owner_user_id,
                    "metadata": {
                        "news_item_id": item.id,
                        "source_type": item.source_type,
                    },
                }
            generated = content_summarizer.summarize(prompt, **summarize_kwargs)
            if not isinstance(generated, NewsSummary):
                raise TypeError(
                    "Short-form news summarizer returned an invalid payload: "
                    f"{type(generated).__name__}"
                )
            summary_to_persist = generated

        _finalize_processed_item(
            db,
            item=item,
            raw_metadata=raw_metadata,
            summary=summary_to_persist or _fallback_summary(item, raw_metadata),
        )
        return NewsItemProcessingResult(
            success=True,
            status=item.status,
            used_existing_summary=used_existing_summary,
            generated_summary=not used_existing_summary,
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
        db.rollback()
        item = db.query(NewsItem).filter(NewsItem.id == news_item_id).first()
        if item is None:
            return NewsItemProcessingResult(
                success=False,
                status="failed",
                error_message=str(exc),
                retryable=True,
            )
        raw_metadata = dict(item.raw_metadata or {})
        raw_metadata["processing_error"] = str(exc)
        item.raw_metadata = raw_metadata
        item.status = NewsItemStatus.FAILED.value
        item.processed_at = _utcnow_naive()
        db.commit()
        return NewsItemProcessingResult(
            success=False,
            status=item.status,
            error_message=str(exc),
            retryable=True,
        )
