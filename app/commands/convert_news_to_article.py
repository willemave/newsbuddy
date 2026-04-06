"""Application command for converting news items into article records."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.api.common import ConvertNewsResponse
from app.models.metadata import ContentStatus, ContentType
from app.models.schema import Content
from app.repositories import favorites_repository
from app.services.queue import TaskType, get_queue_service
from app.utils.url_utils import is_http_url, normalize_http_url


def convert_article_url_to_content(
    db: Session,
    *,
    article_url: str,
    title: str | None,
    source: str | None,
) -> tuple[Content, bool]:
    """Return an existing or newly created article content record.

    Args:
        db: Active database session.
        article_url: Canonical article URL to persist.
        title: Article title when available.
        source: Source/domain label when available.

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
    get_queue_service().enqueue(TaskType.PROCESS_CONTENT, content_id=new_article.id)
    return new_article, False


def ensure_article_saved_to_knowledge(
    db: Session,
    *,
    user_id: int,
    content_id: int,
) -> None:
    """Ensure an article content row is favorited for the current user.

    Args:
        db: Active database session.
        user_id: User saving the article.
        content_id: Article content identifier.

    Raises:
        HTTPException: When the favorite could not be persisted.
    """
    favorite = favorites_repository.add_favorite(db, content_id, user_id)
    if favorite is not None:
        return

    if favorites_repository.is_content_favorited(db, content_id, user_id):
        return

    raise HTTPException(
        status_code=500,
        detail="Article was created, but could not be saved to knowledge",
    )


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

    article, already_exists = convert_article_url_to_content(
        db,
        article_url=article_url,
        title=article_meta.get("title"),
        source=article_meta.get("source_domain"),
    )
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
