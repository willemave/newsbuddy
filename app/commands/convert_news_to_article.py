"""Application command for converting news items into article records."""

from __future__ import annotations

from typing import cast

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.api.content_actions import ConvertNewsResponse
from app.models.api.news import ConvertNewsItemResponse
from app.models.contracts import ContentStatus, ContentType
from app.models.db import Content, NewsItem
from app.repositories import knowledge_repository
from app.services import knowledge as knowledge_service
from app.services.content_bodies import persist_content_body
from app.services.news_article_bodies import get_news_item_article_body_resolver
from app.services.news_feed import get_visible_news_item
from app.services.queue import TaskType, get_queue_service
from app.services.source_metadata import SOURCE_METADATA_KEY, dump_source_metadata
from app.utils.news_titles import get_news_article_title
from app.utils.url_utils import is_http_url, normalize_http_url

logger = get_logger(__name__)


def _seed_article_body_from_news_item(
    db: Session,
    *,
    article: Content,
    news_item: NewsItem,
) -> bool:
    """Reuse a news item's canonical article body when one is already stored."""
    try:
        resolved_body = get_news_item_article_body_resolver().resolve(
            db,
            news_item=news_item,
        )
    except Exception:
        db.rollback()
        logger.exception(
            "Failed to resolve reusable news-item article body",
            extra={"news_item_id": news_item.id, "content_id": article.id},
        )
        return False

    if resolved_body is None or article.id is None:
        return False

    try:
        persist_content_body(
            db,
            content_id=article.id,
            variant=resolved_body.variant,
            text=resolved_body.text,
            content_format=resolved_body.format,
        )
        metadata = dict(article.content_metadata or {})
        metadata["content_type"] = resolved_body.format.value
        metadata["final_url_after_redirects"] = article.url
        news_metadata = news_item.raw_metadata if isinstance(news_item.raw_metadata, dict) else {}
        source_metadata = dump_source_metadata(news_metadata.get(SOURCE_METADATA_KEY))
        if source_metadata is not None:
            metadata[SOURCE_METADATA_KEY] = source_metadata
        article.content_metadata = metadata
        article.status = ContentStatus.PROCESSING.value
        db.commit()
    except Exception:
        db.rollback()
        logger.exception(
            "Failed to attach reusable news-item article body",
            extra={"news_item_id": news_item.id, "content_id": article.id},
        )
        return False

    return True


def convert_article_url_to_content(
    db: Session,
    *,
    article_url: str,
    title: str | None,
    source: str | None,
    news_item: NewsItem | None = None,
) -> tuple[Content, bool]:
    """Return an existing or newly created article content record.

    Args:
        db: Active database session.
        article_url: Canonical article URL to persist.
        title: Article title when available.
        source: Source/domain label when available.
        news_item: Source news item whose stored article body may be reused.

    Returns:
        A tuple of the article content row and whether it already existed.
    """
    existing_article = (
        db.query(Content)
        .filter(Content.url == article_url)
        .filter(Content.content_type == ContentType.ARTICLE.value)
        .first()
    )
    if existing_article:
        return existing_article, True

    new_article = Content(
        url=article_url,
        source_url=article_url,
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.PENDING.value,
        title=title,
        source=source,
        platform=None,
        content_metadata={},
        classification=None,
        publication_date=news_item.published_at if news_item is not None else None,
    )
    db.add(new_article)

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        if "UNIQUE constraint failed" in str(exc) or "duplicate key" in str(exc).lower():
            existing_article = (
                db.query(Content)
                .filter(Content.url == article_url)
                .filter(Content.content_type == ContentType.ARTICLE.value)
                .first()
            )
            if existing_article:
                return existing_article, True
        raise

    db.refresh(new_article)
    body_was_reused = news_item is not None and _seed_article_body_from_news_item(
        db,
        article=new_article,
        news_item=news_item,
    )
    next_task = TaskType.SUMMARIZE if body_was_reused else TaskType.PROCESS_CONTENT
    get_queue_service().enqueue(next_task, content_id=new_article.id)
    return new_article, False


def ensure_article_saved_to_knowledge(
    db: Session,
    *,
    user_id: int,
    content_id: int,
) -> None:
    """Ensure an article content row is saved to knowledge for the current user.

    Args:
        db: Active database session.
        user_id: User saving the article.
        content_id: Article content identifier.

    Raises:
        HTTPException: When the knowledge save could not be persisted.
    """
    save_error: Exception | None = None
    try:
        knowledge_service.save_to_knowledge(db, content_id, user_id)
        return
    except Exception as exc:
        db.rollback()
        save_error = exc

    if knowledge_repository.is_saved_to_knowledge(db, content_id, user_id):
        return

    raise HTTPException(
        status_code=500,
        detail="Article was created, but could not be saved to knowledge",
    ) from save_error


def execute(db: Session, *, content_id: int, user_id: int) -> ConvertNewsResponse:
    """Convert a news content item into a saved article content record."""
    content = db.query(Content).filter(Content.id == content_id).first()
    if not content:
        raise HTTPException(status_code=404, detail="Content not found")

    if content.content_type != ContentType.NEWS.value:
        raise HTTPException(
            status_code=400,
            detail="Only news content can be converted to articles",
        )

    metadata = content.content_metadata or {}
    article_meta = metadata.get("article", {})
    article_url = normalize_http_url(str(content.url))
    if not article_url:
        article_url = normalize_http_url(article_meta.get("url"))
    if not is_http_url(article_url):
        raise HTTPException(status_code=400, detail="No article URL found in news metadata")
    canonical_article_url = cast(str, article_url)

    title = article_meta.get("title") if isinstance(article_meta, dict) else None
    source = article_meta.get("source_domain") if isinstance(article_meta, dict) else None
    article, already_exists = convert_article_url_to_content(
        db,
        article_url=canonical_article_url,
        title=title if isinstance(title, str) else None,
        source=source if isinstance(source, str) else None,
    )
    if article.id is None:
        raise HTTPException(status_code=500, detail="Article record missing id")
    ensure_article_saved_to_knowledge(db, user_id=user_id, content_id=article.id)

    return ConvertNewsResponse(
        status="success",
        new_content_id=article.id,
        original_content_id=content_id,
        already_exists=already_exists,
        message=(
            "Article already exists in system"
            if already_exists
            else "Article created and queued for processing"
        ),
    )


def execute_news_item(
    db: Session,
    *,
    user_id: int,
    news_item_id: int,
) -> ConvertNewsItemResponse:
    """Convert a visible news item into saved article content."""
    item = get_visible_news_item(db, user_id=user_id, news_item_id=news_item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="News item not found")

    article_url = normalize_http_url(item.article_url or item.canonical_story_url)
    if not is_http_url(article_url):
        raise HTTPException(status_code=400, detail="No article URL found for news item")
    canonical_article_url = cast(str, article_url)

    article, already_exists = convert_article_url_to_content(
        db,
        article_url=canonical_article_url,
        title=get_news_article_title(item.raw_metadata) or item.article_title,
        source=item.article_domain,
        news_item=item,
    )
    if item.id is None or article.id is None:
        raise HTTPException(status_code=500, detail="Converted content is missing required ids")
    ensure_article_saved_to_knowledge(db, user_id=user_id, content_id=article.id)

    return ConvertNewsItemResponse(
        news_item_id=item.id,
        new_content_id=article.id,
        already_exists=already_exists,
        message=(
            "Article already exists in system"
            if already_exists
            else "Article created and queued for processing"
        ),
    )
