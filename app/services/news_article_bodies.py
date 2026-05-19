"""Canonical article-body lookup and persistence for short-form news items."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from botocore.exceptions import ClientError
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.models.contracts import ContentType
from app.models.db import Content, NewsItem
from app.services.content_bodies import (
    ContentBodyFormat,
    ContentBodyVariant,
    get_content_body_resolver,
)
from app.services.gateways.object_storage_gateway import (
    ObjectStorageGateway,
    get_object_storage_gateway,
)
from app.utils.url_utils import normalize_http_url

NEWS_ARTICLE_BODY_REF_KEY = "article_body_ref"
NEWS_ARTICLE_EXTRACTION_KEY = "article_extraction"

logger = get_logger(__name__)


@dataclass(frozen=True)
class ResolvedNewsItemArticleBody:
    """Resolved canonical article body for one news item."""

    source: str
    text: str
    variant: ContentBodyVariant = ContentBodyVariant.SOURCE
    format: ContentBodyFormat = ContentBodyFormat.TEXT
    updated_at: datetime | None = None


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _clean_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _content_format_from_body_ref(body_ref: dict[str, Any]) -> ContentBodyFormat:
    raw_format = _clean_text(body_ref.get("content_format"))
    if raw_format is None:
        return ContentBodyFormat.TEXT
    try:
        return ContentBodyFormat(raw_format)
    except ValueError:
        return ContentBodyFormat.TEXT


def _body_ref_for_news_item(news_item: NewsItem) -> dict[str, Any] | None:
    raw_metadata = dict(news_item.raw_metadata or {})
    body_ref = raw_metadata.get(NEWS_ARTICLE_BODY_REF_KEY)
    return body_ref if isinstance(body_ref, dict) else None


def get_news_item_article_body_reference_format(news_item: NewsItem) -> ContentBodyFormat | None:
    """Return the declared source-body format when a news item has a body pointer."""
    body_ref = _body_ref_for_news_item(news_item)
    if body_ref is None:
        return None

    kind = str(body_ref.get("kind") or "").strip().lower()
    if kind == "content" and isinstance(body_ref.get("content_id"), int):
        return ContentBodyFormat.TEXT
    if kind == "storage" and _clean_text(body_ref.get("storage_key")):
        return _content_format_from_body_ref(body_ref)
    return None


def get_news_item_article_body_available_format(
    db: Session,
    *,
    news_item: NewsItem,
) -> ContentBodyFormat | None:
    """Return the available article-body format for detail payload gating."""
    reference_format = get_news_item_article_body_reference_format(news_item)
    if reference_format is not None:
        return reference_format

    article_url = normalize_http_url(news_item.article_url or news_item.canonical_story_url)
    if article_url is None:
        return None
    existing_article = _existing_article_content(db, article_url)
    if existing_article is None:
        return None

    resolver = get_content_body_resolver()
    for variant in (ContentBodyVariant.SOURCE, ContentBodyVariant.RENDERED):
        resolved = resolver.resolve(db, content=existing_article, variant=variant)
        if resolved is not None and resolved.text:
            return resolved.format
    return None


def _is_missing_storage_error(exc: ClientError) -> bool:
    error_code = str(exc.response.get("Error", {}).get("Code") or "")
    return error_code in {"404", "NoSuchKey", "NotFound"}


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
        variant: ContentBodyVariant = ContentBodyVariant.SOURCE,
    ) -> ResolvedNewsItemArticleBody | None:
        """Return the best available article body for one news item."""
        body_ref = _body_ref_for_news_item(news_item)
        if body_ref is not None:
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
                        resolved = self._content_body_resolver.resolve(
                            db,
                            content=content,
                            variant=variant,
                        )
                        if resolved is not None and resolved.text:
                            return ResolvedNewsItemArticleBody(
                                source="content",
                                text=resolved.text,
                                variant=resolved.variant,
                                format=resolved.format,
                                updated_at=resolved.updated_at,
                            )

            if kind == "storage":
                if variant != ContentBodyVariant.SOURCE:
                    return None
                storage_key = _clean_text(body_ref.get("storage_key"))
                if storage_key:
                    try:
                        text = self._gateway.get_text(key=storage_key)
                    except FileNotFoundError:
                        logger.warning(
                            "News item article body missing from local storage",
                            extra={
                                "news_item_id": news_item.id,
                                "storage_key": storage_key,
                            },
                        )
                        return None
                    except ClientError as exc:
                        if not _is_missing_storage_error(exc):
                            raise
                        logger.warning(
                            "News item article body missing from object storage",
                            extra={
                                "news_item_id": news_item.id,
                                "storage_key": storage_key,
                            },
                        )
                        return None
                    return ResolvedNewsItemArticleBody(
                        source="storage",
                        text=text,
                        variant=ContentBodyVariant.SOURCE,
                        format=_content_format_from_body_ref(body_ref),
                        updated_at=_parse_iso_datetime(body_ref.get("updated_at")),
                    )

        article_url = normalize_http_url(news_item.article_url or news_item.canonical_story_url)
        if article_url is None:
            return None
        existing_article = _existing_article_content(db, article_url)
        if existing_article is None:
            return None
        resolved = self._content_body_resolver.resolve(
            db,
            content=existing_article,
            variant=variant,
        )
        if resolved is None or not resolved.text:
            return None
        return ResolvedNewsItemArticleBody(
            source="content",
            text=resolved.text,
            variant=resolved.variant,
            format=resolved.format,
            updated_at=resolved.updated_at,
        )

    def resolve_text(
        self,
        db: Session,
        *,
        news_item: NewsItem,
        variant: ContentBodyVariant = ContentBodyVariant.SOURCE,
    ) -> str | None:
        """Return resolved article text only."""
        resolved = self.resolve(db, news_item=news_item, variant=variant)
        return resolved.text if resolved else None


_news_item_article_body_resolver: NewsItemArticleBodyResolver | None = None


def get_news_item_article_body_resolver() -> NewsItemArticleBodyResolver:
    """Return a cached article-body resolver for news items."""
    global _news_item_article_body_resolver
    if _news_item_article_body_resolver is None:
        _news_item_article_body_resolver = NewsItemArticleBodyResolver()
    return _news_item_article_body_resolver
