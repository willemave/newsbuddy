"""Canonical article-body lookup and persistence for short-form news items."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.models.metadata import ContentType
from app.models.schema import Content, NewsItem
from app.services.content_bodies import get_content_body_resolver
from app.services.gateways.object_storage_gateway import (
    ObjectStorageGateway,
    get_object_storage_gateway,
)
from app.utils.url_utils import normalize_http_url

NEWS_ARTICLE_BODY_REF_KEY = "article_body_ref"
NEWS_ARTICLE_EXTRACTION_KEY = "article_extraction"


@dataclass(frozen=True)
class ResolvedNewsItemArticleBody:
    """Resolved canonical article body for one news item."""

    source: str
    text: str
    updated_at: datetime | None = None


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _clean_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _build_news_item_storage_key(*, news_item_id: int, sha256: str) -> str:
    prefix = get_settings().storage.content_body_storage_prefix.strip("/")
    return f"{prefix}/news-items/{news_item_id}/source-{sha256}.txt"


def _existing_article_content(db: Session, article_url: str) -> Content | None:
    normalized = normalize_http_url(article_url)
    if normalized is None:
        return None
    return (
        db.query(Content)
        .filter(Content.content_type == ContentType.ARTICLE.value)
        .filter((Content.url == normalized) | (Content.source_url == normalized))
        .order_by(Content.id.asc())
        .first()
    )


def persist_news_item_article_body(
    db: Session,
    *,
    news_item: NewsItem,
    text: str,
    source_url: str | None,
    final_url: str | None,
    gateway: ObjectStorageGateway | None = None,
) -> dict[str, Any]:
    """Persist one news-item article body to object storage and return the pointer."""
    cleaned = _clean_text(text)
    if cleaned is None:
        raise ValueError("News item article body text must not be empty")

    storage_gateway = gateway or get_object_storage_gateway()
    encoded = cleaned.encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    news_item_id = news_item.id
    if news_item_id is None:
        raise ValueError("News item must have an id before persisting article body")
    storage_key = _build_news_item_storage_key(news_item_id=int(news_item_id), sha256=digest)
    stored = storage_gateway.put_text(
        key=storage_key,
        text=cleaned,
        content_type="text/plain",
    )
    return {
        "kind": "storage",
        "storage_provider": stored.provider,
        "storage_bucket": stored.bucket,
        "storage_key": stored.key,
        "content_format": "text",
        "sha256": digest,
        "byte_size": len(encoded),
        "char_count": len(cleaned),
        "source_url": normalize_http_url(source_url) if source_url else None,
        "final_url": normalize_http_url(final_url) if final_url else None,
        "updated_at": _utcnow_naive().isoformat(),
    }


class NewsItemArticleBodyResolver:
    """Resolve a canonical article body for short-form news items."""

    def __init__(self, gateway: ObjectStorageGateway | None = None) -> None:
        self._gateway = gateway or get_object_storage_gateway()
        self._content_body_resolver = get_content_body_resolver()

    def resolve(
        self,
        db: Session,
        *,
        news_item: NewsItem,
    ) -> ResolvedNewsItemArticleBody | None:
        """Return the best available article body for one news item."""
        raw_metadata = dict(news_item.raw_metadata or {})
        body_ref = raw_metadata.get(NEWS_ARTICLE_BODY_REF_KEY)
        if isinstance(body_ref, dict):
            kind = str(body_ref.get("kind") or "").strip().lower()
            if kind == "content":
                raw_content_id = body_ref.get("content_id")
                if isinstance(raw_content_id, int):
                    content = (
                        db.query(Content)
                        .filter(
                            Content.id == raw_content_id,
                            Content.content_type == ContentType.ARTICLE.value,
                        )
                        .first()
                    )
                    if content is not None:
                        text = self._content_body_resolver.resolve_text(db, content=content)
                        if text:
                            return ResolvedNewsItemArticleBody(
                                source="content",
                                text=text,
                                updated_at=getattr(content, "updated_at", None),
                            )

            if kind == "storage":
                storage_key = _clean_text(body_ref.get("storage_key"))
                if storage_key:
                    text = self._gateway.get_text(key=storage_key)
                    return ResolvedNewsItemArticleBody(
                        source="storage",
                        text=text,
                        updated_at=None,
                    )

        article_url = normalize_http_url(news_item.article_url or news_item.canonical_story_url)
        if article_url is None:
            return None
        existing_article = _existing_article_content(db, article_url)
        if existing_article is None:
            return None
        text = self._content_body_resolver.resolve_text(db, content=existing_article)
        if not text:
            return None
        return ResolvedNewsItemArticleBody(
            source="content",
            text=text,
            updated_at=getattr(existing_article, "updated_at", None),
        )

    def resolve_text(
        self,
        db: Session,
        *,
        news_item: NewsItem,
    ) -> str | None:
        """Return resolved article text only."""
        resolved = self.resolve(db, news_item=news_item)
        return resolved.text if resolved else None


_news_item_article_body_resolver: NewsItemArticleBodyResolver | None = None


def get_news_item_article_body_resolver() -> NewsItemArticleBodyResolver:
    """Return a cached article-body resolver for news items."""
    global _news_item_article_body_resolver
    if _news_item_article_body_resolver is None:
        _news_item_article_body_resolver = NewsItemArticleBodyResolver()
    return _news_item_article_body_resolver
