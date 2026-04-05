"""Application command for converting news items into article records."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.api.common import ConvertNewsResponse
from app.models.metadata import ContentStatus, ContentType
from app.models.schema import Content
from app.utils.url_utils import is_http_url, normalize_http_url


def execute(db: Session, *, content_id: int) -> ConvertNewsResponse:
    """Convert a news content item into a full article content record."""
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

    existing_article = (
        db.query(Content)
        .filter(Content.url == article_url)
        .filter(Content.content_type == ContentType.ARTICLE.value)
        .first()
    )
    if existing_article:
        return ConvertNewsResponse(
            status="success",
            new_content_id=existing_article.id,
            original_content_id=content_id,
            already_exists=True,
            message="Article already exists in system",
        )

    new_article = Content(
        url=article_url,
        source_url=article_url,
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.PENDING.value,
        title=article_meta.get("title"),
        source=article_meta.get("source_domain"),
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
                return ConvertNewsResponse(
                    status="success",
                    new_content_id=existing_article.id,
                    original_content_id=content_id,
                    already_exists=True,
                    message="Article already exists in system",
                )
        raise

    db.refresh(new_article)
    return ConvertNewsResponse(
        status="success",
        new_content_id=new_article.id,
        original_content_id=content_id,
        already_exists=False,
        message="Article created and queued for processing",
    )
