"""Normalization and summarization for short-form news items."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.constants import SUMMARY_KIND_SHORT_NEWS, SUMMARY_VERSION_V1
from app.core.logging import get_logger
from app.models.contracts import NewsItemStatus
from app.models.db import NewsItem
from app.models.metadata.summaries import NewsSummary
from app.services.llm_summarization import ContentSummarizer, get_content_summarizer
from app.services.news_article_bodies import get_news_item_article_body_resolver
from app.services.news_relations import reconcile_news_item_relation
from app.services.news_relevant_links import (
    NEWS_ARTICLE_RELEVANT_LINKS_KEY,
    select_news_article_relevant_links,
)
from app.utils.news_titles import get_news_article_title, get_news_summary_title
from app.utils.title_utils import clean_title, resolve_title_candidate
from app.utils.url_utils import normalize_http_url

logger = get_logger(__name__)

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


def _require_news_item_id(item: NewsItem) -> int:
    """Return a persisted news-item ID or raise."""
    news_item_id = item.id
    if news_item_id is None:
        raise ValueError("News item must be persisted before use")
    return news_item_id


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
    raw_metadata = dict(item.raw_metadata or {})
    if raw_metadata.get("summary_kind") == SUMMARY_KIND_SHORT_NEWS:
        has_generated_summary = raw_metadata.get("summary_version") == SUMMARY_VERSION_V1
    else:
        has_generated_summary = raw_metadata.get(
            "summary_version"
        ) == SUMMARY_VERSION_V1 and isinstance(raw_metadata.get("summary"), dict)
    if not has_generated_summary:
        return None

    item_key_points = _normalize_key_points(item.summary_key_points)
    if _has_materialized_summary(
        key_points=item_key_points,
        summary_text=item.summary_text,
    ):
        return NewsSummary(
            title=get_news_summary_title(raw_metadata) or get_news_article_title(raw_metadata),
            article_url=item.article_url,
            key_points=item_key_points,
            summary=item.summary_text,
        )

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
        title=clean_title(summary.get("title")) or get_news_article_title(raw_metadata),
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
        compact_snippets = [_clean_string(comment) for comment in compact_comments]
        return [snippet for snippet in compact_snippets if snippet][:MAX_DISCUSSION_SNIPPETS]

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


def _build_processing_prompt(
    item: NewsItem,
    raw_metadata: dict[str, Any],
    *,
    article_body_text: str | None = None,
) -> str:
    lines = [
        "Create a compact short-form news summary grounded only in this evidence.",
    ]
    if item.source_label:
        lines.append(f"Source label: {item.source_label}")
    if item.platform:
        lines.append(f"Platform: {item.platform}")
    article_title = get_news_article_title(raw_metadata) or clean_title(item.article_title)
    if article_title:
        lines.append(f"Article title: {article_title}")
    if item.article_domain:
        lines.append(f"Article domain: {item.article_domain}")
    if item.article_url:
        lines.append(f"Article URL: {item.article_url}")

    aggregator = raw_metadata.get("aggregator")
    if isinstance(aggregator, dict):
        aggregator_title = clean_title(aggregator.get("title"))
        if aggregator_title:
            lines.append(f"Aggregator title: {aggregator_title}")
        if _clean_string(aggregator.get("author")):
            lines.append(f"Aggregator author: {_clean_string(aggregator.get('author'))}")

    excerpt = _clean_string(raw_metadata.get("excerpt"))
    if article_body_text:
        lines.extend(["", "Article body:", article_body_text])
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

    canonical_article_title = get_news_article_title(raw_metadata) or item.article_title
    summary_text = item.summary_text or (key_points[0] if key_points else canonical_article_title)
    return NewsSummary(
        title=(
            get_news_summary_title(raw_metadata)
            or get_news_article_title(raw_metadata)
            or f"News item {item.id}"
        ),
        article_url=item.article_url,
        key_points=key_points[:5],
        summary=summary_text,
    )


def _resolve_summary_text(
    summary: NewsSummary,
    normalized_key_points: list[str],
    existing_summary_text: str | None,
) -> str | None:
    return (
        _clean_string(summary.summary)
        or (normalized_key_points[0] if normalized_key_points else None)
        or _clean_string(existing_summary_text)
    )


def _persist_summary(item: NewsItem, summary: NewsSummary, raw_metadata: dict[str, Any]) -> None:
    normalized_key_points = _normalize_key_points(summary.key_points)
    resolved_summary_text = _resolve_summary_text(
        summary,
        normalized_key_points,
        item.summary_text,
    )
    resolved_title = resolve_title_candidate(
        summary.title,
        item.article_title,
        item.summary_title,
        summary_text=resolved_summary_text,
    )
    if resolved_title:
        item.summary_title = resolved_title
    normalized_article_url = (
        normalize_http_url(summary.article_url) if summary.article_url else None
    )
    if normalized_article_url:
        item.article_url = normalized_article_url
        item.canonical_story_url = normalized_article_url
    item.summary_key_points = normalized_key_points
    item.summary_text = resolved_summary_text or item.summary_text
    raw_summary = summary.model_dump(mode="json", by_alias=True, exclude_none=True)
    if resolved_title:
        raw_summary["title"] = resolved_title
    if resolved_summary_text:
        raw_summary["summary"] = resolved_summary_text
    raw_metadata["summary"] = raw_summary
    raw_metadata["summary_kind"] = SUMMARY_KIND_SHORT_NEWS
    raw_metadata["summary_version"] = SUMMARY_VERSION_V1
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

    def _write() -> None:
        target = db.query(NewsItem).filter(NewsItem.id == item.id).first()
        if target is None:
            raise ValueError(f"News item {item.id} not found")
        _persist_summary(target, summary, dict(raw_metadata))
        reconcile_news_item_relation(db, news_item_id=_require_news_item_id(target))
        db.commit()

    _write()
    db.refresh(item)


def _mark_processing_failure(
    db: Session,
    *,
    news_item_id: int,
    error_message: str,
) -> str:
    """Persist a failed processing state."""

    def _write() -> str:
        item = db.query(NewsItem).filter(NewsItem.id == news_item_id).first()
        if item is None:
            return NewsItemStatus.FAILED.value
        raw_metadata = dict(item.raw_metadata or {})
        raw_metadata["processing_error"] = error_message
        item.raw_metadata = raw_metadata
        item.status = NewsItemStatus.FAILED.value
        item.processed_at = _utcnow_naive()
        db.commit()
        return item.status

    return _write()


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
    """Normalize one ``news_items`` row into news-ready fields.

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
        article_body_resolver = get_news_item_article_body_resolver()
        article_body_text = article_body_resolver.resolve_text(db, news_item=item)
        if article_body_text and not isinstance(
            raw_metadata.get(NEWS_ARTICLE_RELEVANT_LINKS_KEY),
            list,
        ):
            article_relevant_links = select_news_article_relevant_links(
                article_body_text,
                source_url=item.article_url or item.canonical_story_url,
                title=get_news_article_title(raw_metadata) or clean_title(item.article_title),
                usage_persist={
                    "feature": "news_relevant_links",
                    "operation": "news_processing.select_article_links",
                    "source": "queue",
                    "user_id": item.owner_user_id,
                    "metadata": {
                        "news_item_id": item.id,
                        "source_type": item.source_type,
                    },
                },
            )
            if article_relevant_links:
                raw_metadata[NEWS_ARTICLE_RELEVANT_LINKS_KEY] = article_relevant_links

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
            prompt = _build_processing_prompt(
                item,
                raw_metadata,
                article_body_text=article_body_text,
            )
            content_summarizer = summarizer or get_content_summarizer()
            summarize_kwargs: dict[str, Any] = {
                "content_type": "news",
                "title": get_news_article_title(raw_metadata)
                or get_news_summary_title(raw_metadata),
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
        failed_status = _mark_processing_failure(
            db,
            news_item_id=news_item_id,
            error_message=str(exc),
        )
        return NewsItemProcessingResult(
            success=False,
            status=failed_status,
            error_message=str(exc),
            retryable=True,
        )
