"""Helpers for writing short-form evidence into news-native tables."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.constants import CONTENT_DIGEST_VISIBILITY_DIGEST_ONLY
from app.core.logging import get_logger
from app.models.contracts import NewsItemStatus, NewsItemVisibilityScope
from app.models.metadata import ContentType
from app.models.schema import Content, NewsItem
from app.utils.news_titles import merge_news_metadata, normalize_news_metadata_titles
from app.utils.title_utils import clean_title, resolve_title_candidate
from app.utils.url_utils import normalize_http_url

logger = get_logger(__name__)


@dataclass(frozen=True)
class NewsItemUpsertInput:
    """Normalized payload used to create or update a news item."""

    visibility_scope: NewsItemVisibilityScope
    owner_user_id: int | None
    platform: str | None
    source_type: str | None
    source_label: str | None
    source_external_id: str | None
    user_scraper_config_id: int | None
    user_integration_connection_id: int | None
    canonical_item_url: str | None
    canonical_story_url: str | None
    article_url: str | None
    article_title: str | None
    article_domain: str | None
    discussion_url: str | None
    summary_title: str | None
    summary_key_points: list[str]
    summary_text: str | None
    raw_metadata: dict[str, Any]
    status: NewsItemStatus
    published_at: datetime | None
    ingested_at: datetime | None
    legacy_content_id: int | None = None


@dataclass(frozen=True)
class NewsBackfillStats:
    """Outcome counters for a legacy-content backfill pass."""

    created: int = 0
    updated: int = 0
    skipped: int = 0


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _clean_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _clean_title(value: Any) -> str | None:
    return clean_title(value)


def _normalize_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value
        return value.astimezone(UTC).replace(tzinfo=None)
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(UTC).replace(tzinfo=None)


def _normalize_scope(value: Any) -> NewsItemVisibilityScope:
    cleaned = _clean_string(value)
    if cleaned == NewsItemVisibilityScope.USER.value:
        return NewsItemVisibilityScope.USER
    return NewsItemVisibilityScope.GLOBAL


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
    summary_key_points: list[str],
    summary_text: str | None,
) -> bool:
    return bool(summary_key_points or summary_text)


def _normalize_url(value: Any) -> str | None:
    cleaned = _clean_string(value)
    if cleaned is None:
        return None
    return normalize_http_url(cleaned) or cleaned


_SOURCE_LABELS_BY_PLATFORM: dict[str, str] = {
    "hackernews": "Hacker News",
    "techmeme": "Techmeme",
    "mediagazer": "Mediagazer",
    "memeorandum": "Memeorandum",
    "sciurls": "SciURLs",
    "finurls": "FinURLs",
    "brutalist": "Brutalist Report",
    "reddit": "Reddit",
    "twitter": "X",
}


def _source_label_from_platform(platform: str | None) -> str | None:
    if platform in _SOURCE_LABELS_BY_PLATFORM:
        return _SOURCE_LABELS_BY_PLATFORM[platform]
    return _clean_string(platform)


def _infer_visibility_scope(
    metadata: dict[str, Any], user_id: int | None
) -> NewsItemVisibilityScope:
    if user_id is not None:
        return NewsItemVisibilityScope.USER
    if metadata.get("digest_visibility") == CONTENT_DIGEST_VISIBILITY_DIGEST_ONLY:
        return NewsItemVisibilityScope.USER
    if metadata.get("submitted_by_user_id") is not None:
        return NewsItemVisibilityScope.USER
    return NewsItemVisibilityScope.GLOBAL


def _extract_story_url(
    metadata: dict[str, Any],
    *,
    fallback_url: str | None,
) -> str | None:
    article = metadata.get("article")
    summary = metadata.get("summary")
    candidates = []
    if isinstance(article, dict):
        candidates.append(article.get("url"))
    if isinstance(summary, dict):
        candidates.append(summary.get("article_url"))
    candidates.extend(
        [
            metadata.get("tweet_url"),
            fallback_url,
        ]
    )
    for candidate in candidates:
        normalized = _normalize_url(candidate)
        if normalized:
            return normalized
    return None


def _extract_item_url(
    metadata: dict[str, Any],
    *,
    fallback_url: str | None,
) -> str | None:
    candidates = [
        metadata.get("discussion_url"),
        fallback_url,
    ]
    aggregator = metadata.get("aggregator")
    if isinstance(aggregator, dict):
        candidates.append(aggregator.get("url"))
    for candidate in candidates:
        normalized = _normalize_url(candidate)
        if normalized:
            return normalized
    return None


def _build_ingest_key(payload: NewsItemUpsertInput) -> str:
    material = {
        "visibility_scope": payload.visibility_scope.value,
        "owner_user_id": payload.owner_user_id,
    }
    if payload.legacy_content_id is not None:
        material.update(
            {
                "identity_type": "legacy_content_id",
                "legacy_content_id": payload.legacy_content_id,
            }
        )
    elif payload.platform and payload.source_external_id:
        material.update(
            {
                "identity_type": "platform_source_external_id",
                "platform": payload.platform,
                "source_external_id": payload.source_external_id,
            }
        )
    elif payload.canonical_item_url:
        material.update(
            {
                "identity_type": "canonical_item_url",
                "canonical_item_url": payload.canonical_item_url,
            }
        )
    elif payload.discussion_url:
        material.update(
            {
                "identity_type": "discussion_url",
                "discussion_url": payload.discussion_url,
            }
        )
    elif payload.canonical_story_url:
        material.update(
            {
                "identity_type": "canonical_story_url",
                "canonical_story_url": payload.canonical_story_url,
            }
        )
    else:
        material.update(
            {
                "identity_type": "title_url_fallback",
                "platform": payload.platform,
                "source_type": payload.source_type,
                "article_title": payload.article_title,
                "article_url": payload.article_url,
            }
        )
    encoded = json.dumps(material, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _owner_user_id_matcher(owner_user_id: int | None) -> Any:
    if owner_user_id is None:
        return NewsItem.owner_user_id.is_(None)
    return NewsItem.owner_user_id == owner_user_id


def _find_existing_news_item(db: Session, payload: NewsItemUpsertInput) -> NewsItem | None:
    if payload.legacy_content_id is not None:
        existing = (
            db.query(NewsItem)
            .filter(NewsItem.legacy_content_id == payload.legacy_content_id)
            .first()
        )
        if existing is not None:
            return existing

    if payload.platform and payload.source_external_id:
        existing = (
            db.query(NewsItem)
            .filter(
                and_(
                    NewsItem.visibility_scope == payload.visibility_scope.value,
                    _owner_user_id_matcher(payload.owner_user_id),
                    NewsItem.platform == payload.platform,
                    NewsItem.source_external_id == payload.source_external_id,
                )
            )
            .order_by(NewsItem.id.asc())
            .first()
        )
        if existing is not None:
            return existing

    for url_field, value in (
        (NewsItem.canonical_item_url, payload.canonical_item_url),
        (NewsItem.discussion_url, payload.discussion_url),
        (NewsItem.canonical_story_url, payload.canonical_story_url),
    ):
        if value is None:
            continue
        existing = (
            db.query(NewsItem)
            .filter(
                and_(
                    NewsItem.visibility_scope == payload.visibility_scope.value,
                    _owner_user_id_matcher(payload.owner_user_id),
                    url_field == value,
                )
            )
            .order_by(NewsItem.id.asc())
            .first()
        )
        if existing is not None:
            return existing

    ingest_key = _build_ingest_key(payload)
    return db.query(NewsItem).filter(NewsItem.ingest_key == ingest_key).first()


def build_news_item_upsert_input_from_scraped_item(item: dict[str, Any]) -> NewsItemUpsertInput:
    """Normalize one scraper/X payload into a news item upsert input.

    Args:
        item: Raw scraper item dictionary.

    Returns:
        Normalized upsert payload for ``news_items``.
    """
    metadata = item.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    article = metadata.get("article")
    article_meta = article if isinstance(article, dict) else {}
    summary = metadata.get("summary")
    summary_meta = summary if isinstance(summary, dict) else {}
    aggregator = metadata.get("aggregator")
    aggregator_meta = aggregator if isinstance(aggregator, dict) else {}

    raw_owner_user_id = (
        item.get("owner_user_id") or item.get("user_id") or metadata.get("submitted_by_user_id")
    )
    owner_user_id = int(raw_owner_user_id) if isinstance(raw_owner_user_id, int) else None
    visibility_scope = item.get("visibility_scope")
    normalized_scope = (
        _normalize_scope(visibility_scope)
        if visibility_scope is not None
        else _infer_visibility_scope(metadata, owner_user_id)
    )

    platform = _clean_string(item.get("platform")) or _clean_string(metadata.get("platform"))
    source_label = (
        _clean_string(item.get("source_label"))
        or _clean_string(metadata.get("source_label"))
        or _clean_string(metadata.get("source"))
        or _source_label_from_platform(platform)
    )
    source_type = (
        _clean_string(item.get("source_type"))
        or _clean_string(metadata.get("source_type"))
        or _clean_string(aggregator_meta.get("name"))
        or platform
    )
    discussion_url = _normalize_url(metadata.get("discussion_url"))
    story_url = _extract_story_url(
        metadata,
        fallback_url=item.get("url"),
    )
    article_url = story_url
    article_title = (
        _clean_title(article_meta.get("title"))
        or _clean_title(summary_meta.get("title"))
        or _clean_title(item.get("title"))
    )
    article_domain = _clean_string(article_meta.get("source_domain")) or _clean_string(
        metadata.get("source")
    )
    materialized_summary_title = _clean_title(summary_meta.get("title"))
    summary_title = materialized_summary_title or article_title
    summary_key_points = _normalize_key_points(summary_meta.get("key_points"))
    summary_text = _clean_string(summary_meta.get("summary"))
    source_external_id = _clean_string(item.get("source_external_id")) or _clean_string(
        metadata.get("source_external_id")
    )
    if source_external_id is None:
        source_external_id = _clean_string(aggregator_meta.get("external_id")) or _clean_string(
            metadata.get("tweet_id")
        )

    has_materialized_summary = _has_materialized_summary(
        summary_key_points=summary_key_points,
        summary_text=summary_text,
    )

    normalized_metadata = normalize_news_metadata_titles(
        metadata,
        article_title=article_title,
        summary_title=materialized_summary_title,
    )

    return NewsItemUpsertInput(
        visibility_scope=normalized_scope,
        owner_user_id=owner_user_id,
        platform=platform,
        source_type=source_type,
        source_label=source_label,
        source_external_id=source_external_id,
        user_scraper_config_id=item.get("user_scraper_config_id"),
        user_integration_connection_id=item.get("user_integration_connection_id"),
        canonical_item_url=_extract_item_url(
            metadata, fallback_url=discussion_url or item.get("url")
        ),
        canonical_story_url=story_url,
        article_url=article_url,
        article_title=article_title,
        article_domain=article_domain,
        discussion_url=discussion_url,
        summary_title=summary_title,
        summary_key_points=summary_key_points,
        summary_text=summary_text,
        raw_metadata=normalized_metadata,
        status=NewsItemStatus.READY if has_materialized_summary else NewsItemStatus.NEW,
        published_at=_normalize_datetime(
            item.get("published_at")
            or metadata.get("published_at")
            or metadata.get("tweet_created_at")
        ),
        ingested_at=_normalize_datetime(item.get("ingested_at")) or _utcnow_naive(),
    )


def build_news_item_upsert_input_from_content(content: Content) -> NewsItemUpsertInput | None:
    """Translate a legacy ``contents`` news row into a news item input."""
    if content.content_type != ContentType.NEWS.value:
        return None

    metadata = dict(content.content_metadata or {})
    summary = metadata.get("summary")
    summary_meta = summary if isinstance(summary, dict) else {}

    raw_owner_user_id = metadata.get("submitted_by_user_id")
    owner_user_id = int(raw_owner_user_id) if isinstance(raw_owner_user_id, int) else None
    visibility_scope = _infer_visibility_scope(metadata, owner_user_id)
    story_url = _extract_story_url(metadata, fallback_url=content.source_url or content.url)
    article = metadata.get("article")
    article_meta = article if isinstance(article, dict) else {}

    summary_text = _clean_string(summary_meta.get("summary"))
    summary_key_points = _normalize_key_points(summary_meta.get("key_points"))
    materialized_summary_title = _clean_title(summary_meta.get("title"))
    summary_title = materialized_summary_title or _clean_title(content.title)
    has_materialized_summary = _has_materialized_summary(
        summary_key_points=summary_key_points,
        summary_text=summary_text,
    )
    status = (
        NewsItemStatus.READY
        if content.status == "completed" and has_materialized_summary
        else NewsItemStatus.NEW
    )

    normalized_metadata = normalize_news_metadata_titles(
        metadata,
        article_title=(_clean_title(article_meta.get("title")) or _clean_title(content.title)),
        summary_title=materialized_summary_title,
    )

    return NewsItemUpsertInput(
        visibility_scope=visibility_scope,
        owner_user_id=owner_user_id,
        platform=_clean_string(content.platform) or _clean_string(metadata.get("platform")),
        source_type=_clean_string(metadata.get("source_type"))
        or _clean_string(
            metadata.get("aggregator", {}).get("name")
            if isinstance(metadata.get("aggregator"), dict)
            else None
        )
        or _clean_string(content.platform),
        source_label=_clean_string(metadata.get("source_label"))
        or _clean_string(content.source)
        or _source_label_from_platform(_clean_string(content.platform)),
        source_external_id=_clean_string(
            metadata.get("aggregator", {}).get("external_id")
            if isinstance(metadata.get("aggregator"), dict)
            else None
        )
        or _clean_string(metadata.get("tweet_id")),
        user_scraper_config_id=None,
        user_integration_connection_id=None,
        canonical_item_url=_extract_item_url(
            metadata, fallback_url=content.source_url or content.url
        ),
        canonical_story_url=story_url,
        article_url=story_url,
        article_title=(
            _clean_title(article_meta.get("title"))
            or _clean_title(summary_meta.get("title"))
            or _clean_title(content.title)
        ),
        article_domain=_clean_string(article_meta.get("source_domain"))
        or _clean_string(content.source),
        discussion_url=_normalize_url(metadata.get("discussion_url")),
        summary_title=summary_title,
        summary_key_points=summary_key_points,
        summary_text=summary_text,
        raw_metadata=normalized_metadata,
        status=status,
        published_at=_normalize_datetime(content.publication_date)
        or _normalize_datetime(metadata.get("tweet_created_at")),
        ingested_at=_normalize_datetime(content.created_at) or _utcnow_naive(),
        legacy_content_id=content.id,
    )


def upsert_news_item(db: Session, payload: NewsItemUpsertInput) -> tuple[NewsItem, bool]:
    """Create or update one news item.

    Args:
        db: Active SQLAlchemy session.
        payload: Normalized item payload.

    Returns:
        Tuple of ``(news_item, created)``.
    """
    ingest_key = _build_ingest_key(payload)
    existing = _find_existing_news_item(db, payload)

    if existing is not None:
        merged_raw_metadata = merge_news_metadata(existing.raw_metadata, payload.raw_metadata)
        article_metadata = merged_raw_metadata.get("article")
        summary_metadata = merged_raw_metadata.get("summary")
        resolved_article_title = resolve_title_candidate(
            article_metadata.get("title") if isinstance(article_metadata, dict) else None,
            payload.article_title,
        )
        resolved_summary_title = resolve_title_candidate(
            summary_metadata.get("title") if isinstance(summary_metadata, dict) else None,
            summary_text=(
                payload.summary_text
                or existing.summary_text
                or (
                    _clean_string(summary_metadata.get("summary"))
                    if isinstance(summary_metadata, dict)
                    else None
                )
            ),
        )
        merged_raw_metadata = normalize_news_metadata_titles(
            merged_raw_metadata,
            article_title=resolved_article_title,
            summary_title=(
                resolved_summary_title
                if isinstance(summary_metadata, dict) and summary_metadata.get("title") is not None
                else existing.summary_title
            ),
        )

        existing.ingest_key = ingest_key
        existing.visibility_scope = payload.visibility_scope.value
        existing.owner_user_id = payload.owner_user_id
        existing.platform = payload.platform
        existing.source_type = payload.source_type
        existing.source_label = payload.source_label
        existing.source_external_id = payload.source_external_id
        existing.user_scraper_config_id = payload.user_scraper_config_id
        existing.user_integration_connection_id = payload.user_integration_connection_id
        existing.canonical_item_url = payload.canonical_item_url or existing.canonical_item_url
        existing.canonical_story_url = payload.canonical_story_url or existing.canonical_story_url
        existing.article_url = payload.article_url or existing.article_url
        existing.article_domain = payload.article_domain or existing.article_domain
        existing.discussion_url = payload.discussion_url or existing.discussion_url
        if payload.summary_key_points:
            existing.summary_key_points = payload.summary_key_points
        existing.summary_text = payload.summary_text or existing.summary_text
        existing.raw_metadata = merged_raw_metadata
        if existing.status != NewsItemStatus.READY.value or payload.status == NewsItemStatus.READY:
            existing.status = payload.status.value
        existing.published_at = payload.published_at or existing.published_at
        existing.ingested_at = payload.ingested_at or existing.ingested_at or _utcnow_naive()
        existing.legacy_content_id = payload.legacy_content_id or existing.legacy_content_id
        existing.updated_at = _utcnow_naive()
        db.flush()
        return existing, False

    record_raw_metadata = normalize_news_metadata_titles(
        payload.raw_metadata,
        article_title=payload.article_title,
    )
    record = NewsItem(
        ingest_key=ingest_key,
        visibility_scope=payload.visibility_scope.value,
        owner_user_id=payload.owner_user_id,
        platform=payload.platform,
        source_type=payload.source_type,
        source_label=payload.source_label,
        source_external_id=payload.source_external_id,
        user_scraper_config_id=payload.user_scraper_config_id,
        user_integration_connection_id=payload.user_integration_connection_id,
        canonical_item_url=payload.canonical_item_url,
        canonical_story_url=payload.canonical_story_url,
        article_url=payload.article_url,
        article_domain=payload.article_domain,
        discussion_url=payload.discussion_url,
        summary_key_points=payload.summary_key_points,
        summary_text=payload.summary_text,
        raw_metadata=record_raw_metadata,
        status=payload.status.value,
        legacy_content_id=payload.legacy_content_id,
        published_at=payload.published_at,
        ingested_at=payload.ingested_at or _utcnow_naive(),
        created_at=_utcnow_naive(),
    )
    db.add(record)
    db.flush()
    return record, True


def should_enqueue_news_item_enrichment(
    *,
    news_item: NewsItem,
    was_created: bool,
) -> bool:
    """Return whether one short-form item should enter the enrichment pipeline."""
    return (
        was_created
        and news_item.legacy_content_id is None
        and news_item.status != NewsItemStatus.READY.value
    )


def backfill_news_items_from_contents(
    db: Session,
    *,
    limit: int | None = None,
    only_missing: bool = True,
    content_ids: list[int] | None = None,
) -> NewsBackfillStats:
    """Backfill ``news_items`` from legacy ``contents`` news rows.

    Args:
        db: Active SQLAlchemy session.
        limit: Optional maximum number of rows to process.
        only_missing: When true, skip rows already linked by ``legacy_content_id``.
        content_ids: Optional explicit legacy ``contents.id`` values to backfill.

    Returns:
        Aggregate counters for the pass.
    """
    query = (
        db.query(Content)
        .filter(Content.content_type == ContentType.NEWS.value)
        .order_by(Content.id.asc())
    )
    if content_ids:
        query = query.filter(Content.id.in_(content_ids))
    if limit is not None:
        query = query.limit(limit)

    created = 0
    updated = 0
    skipped = 0
    for content in query:
        if only_missing:
            existing = db.query(NewsItem).filter(NewsItem.legacy_content_id == content.id).first()
            if existing is not None:
                skipped += 1
                continue

        payload = build_news_item_upsert_input_from_content(content)
        if payload is None:
            skipped += 1
            continue

        _, was_created = upsert_news_item(db, payload)
        if was_created:
            created += 1
        else:
            updated += 1

    return NewsBackfillStats(created=created, updated=updated, skipped=skipped)
