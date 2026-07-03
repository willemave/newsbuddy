from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.models.contracts import ContentClassification, ContentStatus, ContentType
from app.models.db import (
    Content,
    ContentReadStatus,
    ContentStatusEntry,
    NewsItem,
    NewsItemReadStatus,
)
from app.services.briefing.source_keys import build_source_key, parse_source_key
from app.services.news_feed import build_visible_news_item_filter, list_unread_visible_news_items
from app.utils.image_urls import (
    build_content_image_url,
    build_news_thumbnail_url,
    build_thumbnail_url,
)
from app.utils.news_titles import resolve_news_display_title
from app.utils.summary_utils import extract_short_summary


@dataclass(frozen=True)
class BriefingSource:
    source_key: str
    kind: str
    id: int
    tier: str
    lens_key: str | None
    title: str
    summary: str | None
    key_points: list[str]
    url: str | None
    image_url: str | None
    thumbnail_url: str | None
    published_at: datetime | None
    content_type: ContentType | None
    topic_slug: str | None = None
    topic_title: str | None = None

    def dto(self, *, read: bool) -> dict[str, object]:
        return {
            "source_key": self.source_key,
            "kind": self.kind,
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "key_points": self.key_points,
            "url": self.url,
            "image_url": self.image_url,
            "thumbnail_url": self.thumbnail_url,
            "published_at": self.published_at,
            "content_type": self.content_type,
            "read": read,
        }


def _clean_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned or None


def _key_points_from_metadata(metadata: dict[str, Any]) -> list[str]:
    candidates = metadata.get("key_points")
    if candidates is None and isinstance(metadata.get("summary"), dict):
        candidates = metadata["summary"].get("key_points") or metadata["summary"].get("points")
    if not isinstance(candidates, list):
        return []
    points: list[str] = []
    for item in candidates:
        text = item.get("text") if isinstance(item, dict) else item
        cleaned = _clean_string(text)
        if cleaned:
            points.append(cleaned)
    return points[:6]


def _content_is_read_clause(*, user_id: int):
    return exists(
        select(ContentReadStatus.id).where(
            ContentReadStatus.user_id == user_id,
            ContentReadStatus.content_id == Content.id,
        )
    )


def list_unread_longform_sources(
    db: Session,
    *,
    user_id: int,
    content_type: ContentType,
    limit: int,
) -> list[BriefingSource]:
    """Return unread completed to-read content rows for one long-form tier."""

    read_clause = _content_is_read_clause(user_id=user_id)
    rows = (
        db.query(Content)
        .join(
            ContentStatusEntry,
            ContentStatusEntry.content_id == Content.id,
        )
        .filter(ContentStatusEntry.user_id == user_id)
        .filter(ContentStatusEntry.status == "inbox")
        .filter(Content.status == ContentStatus.COMPLETED.value)
        .filter(Content.classification == ContentClassification.TO_READ.value)
        .filter(Content.content_type == content_type.value)
        .filter(~read_clause)
        .order_by(
            Content.publication_date.desc().nullslast(),
            Content.created_at.desc(),
            Content.id.desc(),
        )
        .limit(limit)
        .all()
    )
    return [_source_from_content(row) for row in rows]


def list_unread_news_sources(
    db: Session,
    *,
    user_id: int,
    limit: int,
) -> list[BriefingSource]:
    rows, _total = list_unread_visible_news_items(db, user_id=user_id, limit=limit)
    return [_source_from_news_item(item) for item in rows]


def list_bootstrap_sources(
    db: Session,
    *,
    user_id: int,
    audio_limit: int,
    longform_limit: int,
    news_limit: int,
) -> list[BriefingSource]:
    sources: list[BriefingSource] = []
    sources.extend(
        list_unread_longform_sources(
            db,
            user_id=user_id,
            content_type=ContentType.PODCAST,
            limit=audio_limit,
        )
    )
    sources.extend(
        list_unread_longform_sources(
            db,
            user_id=user_id,
            content_type=ContentType.ARTICLE,
            limit=longform_limit,
        )
    )
    sources.extend(list_unread_news_sources(db, user_id=user_id, limit=news_limit))
    return sources


def sources_for_keys(
    db: Session,
    *,
    user_id: int,
    source_keys: list[str],
) -> dict[str, BriefingSource]:
    parsed = [parse_source_key(key) for key in source_keys]
    content_ids = [key.source_id for key in parsed if key and key.kind == "content"]
    news_ids = [key.source_id for key in parsed if key and key.kind == "news"]

    found: dict[str, BriefingSource] = {}
    if content_ids:
        content_rows = (
            db.query(Content)
            .join(ContentStatusEntry, ContentStatusEntry.content_id == Content.id)
            .filter(ContentStatusEntry.user_id == user_id)
            .filter(Content.id.in_(content_ids))
            .all()
        )
        for content_row in content_rows:
            source = _source_from_content(content_row)
            found[source.source_key] = source

    if news_ids:
        visible = build_visible_news_item_filter(db, user_id=user_id)
        news_rows = db.query(NewsItem).filter(NewsItem.id.in_(news_ids)).filter(visible).all()
        for news_row in news_rows:
            source = _source_from_news_item(news_row)
            found[source.source_key] = source
    return found


def read_source_keys(db: Session, *, user_id: int) -> set[str]:
    content_ids = db.execute(
        select(ContentReadStatus.content_id).where(ContentReadStatus.user_id == user_id)
    ).scalars()
    news_ids = db.execute(
        select(NewsItemReadStatus.news_item_id).where(NewsItemReadStatus.user_id == user_id)
    ).scalars()
    keys = {
        build_source_key("content", int(content_id))
        for content_id in content_ids
        if content_id is not None
    }
    keys.update(
        build_source_key("news", int(news_id)) for news_id in news_ids if news_id is not None
    )
    return keys


def _source_from_content(content: Content) -> BriefingSource:
    content_id = _require_id(content.id, "content.id")
    content_type = ContentType(str(content.content_type))
    metadata = dict(content.content_metadata or {})
    summary = extract_short_summary(metadata.get("summary")) or _clean_string(
        metadata.get("excerpt")
    )
    key_points = _key_points_from_metadata(metadata)
    tier = "audio" if content_type == ContentType.PODCAST else "longform"
    lens_key = "podcasts" if content_type == ContentType.PODCAST else "articles"
    image_version = metadata.get("image_version") or metadata.get("thumbnail_version")
    return BriefingSource(
        source_key=build_source_key("content", content_id),
        kind="content",
        id=content_id,
        tier=tier,
        lens_key=lens_key,
        title=content.title or f"Content {content_id}",
        summary=summary,
        key_points=key_points,
        url=content.source_url or content.url,
        image_url=build_content_image_url(content_id, version=image_version),
        thumbnail_url=build_thumbnail_url(content_id, version=image_version),
        published_at=content.publication_date or content.created_at,
        content_type=content_type,
    )


def _source_from_news_item(item: NewsItem) -> BriefingSource:
    item_id = _require_id(item.id, "news_item.id")
    raw_metadata = dict(item.raw_metadata or {})
    topic_slug, topic_title = _topic_from_news_metadata(raw_metadata)
    image_version = raw_metadata.get("image_version") or raw_metadata.get("thumbnail_version")
    # Only advertise a thumbnail when the image pipeline actually generated one
    # (mirrors content_display's has_generated_image gate). News items without a
    # generated image stay imageless so no figure is composed for them.
    has_generated_image = bool(raw_metadata.get("image_generated_at"))
    thumbnail_url = (
        build_news_thumbnail_url(item_id, version=image_version) if has_generated_image else None
    )
    return BriefingSource(
        source_key=build_source_key("news", item_id),
        kind="news",
        id=item_id,
        tier="news",
        lens_key=None,
        title=resolve_news_display_title(
            raw_metadata,
            summary_text=item.summary_text,
            fallback=f"News item {item_id}",
        ),
        summary=_clean_string(item.summary_text),
        key_points=[point for point in (item.summary_key_points or []) if isinstance(point, str)],
        url=item.article_url or item.canonical_story_url or item.canonical_item_url,
        image_url=None,
        thumbnail_url=thumbnail_url,
        published_at=item.published_at or item.processed_at or item.ingested_at or item.created_at,
        content_type=ContentType.NEWS,
        topic_slug=topic_slug,
        topic_title=topic_title,
    )


def _topic_from_news_metadata(raw_metadata: dict[str, Any]) -> tuple[str | None, str | None]:
    aggregator = raw_metadata.get("aggregator")
    if not isinstance(aggregator, dict):
        return None, None
    raw_topic = _clean_string(aggregator.get("topic"))
    if raw_topic is None:
        return None, None
    slug = _slugify(raw_topic)
    if not slug:
        return None, None
    title = _clean_string(aggregator.get("topic_title")) or raw_topic.replace("-", " ").title()
    return slug, title


def _slugify(value: str) -> str:
    chars: list[str] = []
    previous_dash = False
    for char in value.strip().lower():
        if char.isalnum():
            chars.append(char)
            previous_dash = False
        elif not previous_dash:
            chars.append("-")
            previous_dash = True
    return "".join(chars).strip("-")[:48]


def _require_id(value: int | None, field: str) -> int:
    if value is None:
        raise ValueError(f"Missing persisted {field}")
    return int(value)
