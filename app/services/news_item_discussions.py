"""Per-news-item discussion refresh, storage, and summarization."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, exists, func, or_, select
from sqlalchemy import cast as sa_cast
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session, aliased

from app.constants import AGGREGATOR_SCRAPER_TYPE, SUPPORTED_AGGREGATOR_KEYS
from app.core.logging import get_logger
from app.core.settings import get_settings
from app.models.contracts import NewsItemStatus, NewsItemVisibilityScope
from app.models.db import NewsItem, NewsItemDiscussion, User, UserScraperConfig
from app.services.briefing.read_marks import bump_briefing_version_for_news_item
from app.services.discussion_comments import (
    DiscussionFetchError,
    NormalizedDiscussion,
    TerminalDiscussionUnavailable,
    extract_hackernews_item_id,
    extract_reddit_submission_id,
    fetch_hackernews_comments,
    fetch_reddit_comments,
    is_hackernews_discussion,
    is_reddit_discussion,
    normalize_reddit_discussion_url,
)
from app.services.gateways.object_storage_gateway import (
    ObjectStorageGateway,
    get_object_storage_gateway,
)
from app.services.llm_summarization import ContentSummarizer
from app.services.news_discussion_summaries import (
    DISCUSSION_SUMMARY_MATERIAL_COMMENT_THRESHOLD,
    DiscussionSummaryPlanMode,
    build_discussion_summary_input,
    execute_discussion_summary_plan,
    plan_discussion_summary,
    store_seen_summary_tracking,
    store_summarized_summary_tracking,
)
from app.utils.news_titles import get_news_article_title, get_news_summary_title
from app.utils.url_utils import normalize_http_url

logger = get_logger(__name__)

DISCUSSION_REFRESH_TTL = timedelta(hours=1)
DISCUSSION_REFRESH_LEASE_TTL = timedelta(minutes=15)
REFRESH_STATUS_PROCESSING = "processing"
REFRESH_STATUS_GONE = "gone"
REFRESH_TTL_HOLD_STATUSES = frozenset({"completed", "failed", REFRESH_STATUS_PROCESSING})
REFRESH_EMPTY_FETCH_BLOCK_STATUSES = ("failed", REFRESH_STATUS_PROCESSING)
TERMINAL_REFRESH_STATUSES = frozenset({REFRESH_STATUS_GONE, "unsupported"})
NEWS_DISCUSSION_SUMMARY_VERSION = 1
DEFAULT_DISCUSSION_REFRESH_ENQUEUE_LIMIT = 100
MAX_STORED_COMMENTS = 1_000

SUPPORTED_DISCUSSION_PLATFORMS = frozenset({"hackernews", "reddit"})


@dataclass(frozen=True)
class NewsItemDiscussionRefreshResult:
    """Outcome for one news-item discussion refresh."""

    success: bool
    status: str
    error_message: str | None = None
    refreshed: bool = False
    summarized: bool = False
    retryable: bool = True


TerminalNewsItemDiscussionUnavailable = TerminalDiscussionUnavailable


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _clean_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned or None


def _coerce_non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer():
        integer = int(value)
        return integer if integer >= 0 else None
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        if cleaned.isdigit():
            return int(cleaned)
    return None


def _normalize_platform(value: Any) -> str:
    cleaned = _clean_string(value)
    if not cleaned:
        return ""
    normalized = cleaned.lower()
    if normalized == "hn":
        return "hackernews"
    return normalized


def _is_hackernews_url(url: str | None) -> bool:
    return is_hackernews_discussion("", url)


def _is_reddit_url(url: str | None) -> bool:
    return is_reddit_discussion("", url)


def _is_supported_platform(platform: str, discussion_url: str | None) -> bool:
    if platform in SUPPORTED_DISCUSSION_PLATFORMS:
        return True
    if _is_hackernews_url(discussion_url):
        return True
    return _is_reddit_url(discussion_url)


def is_supported_news_item_discussion(item: NewsItem) -> bool:
    """Return whether the news item's discussion should use the new pipeline."""
    platform = _normalize_platform(item.platform)
    discussion_url = normalize_http_url(item.discussion_url or item.canonical_item_url)
    return _is_supported_platform(platform, discussion_url)


def _extract_nested(mapping: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = mapping
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _extract_comment_count_from_metadata(raw_metadata: dict[str, Any]) -> int | None:
    for candidate in (
        raw_metadata.get("comment_count"),
        raw_metadata.get("comments_count"),
        _extract_nested(raw_metadata, ("aggregator", "metadata", "comments_count")),
        _extract_nested(raw_metadata, ("aggregator", "metadata", "comment_count")),
        _extract_nested(raw_metadata, ("items", "metadata", "comments_count")),
    ):
        value = _coerce_non_negative_int(candidate)
        if value is not None:
            return value

    items = raw_metadata.get("items")
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            value = _coerce_non_negative_int(_extract_nested(item, ("metadata", "comments_count")))
            if value is not None:
                return value
    return None


def _extract_score_from_metadata(raw_metadata: dict[str, Any]) -> int | None:
    for candidate in (
        raw_metadata.get("score"),
        _extract_nested(raw_metadata, ("aggregator", "metadata", "score")),
    ):
        value = _coerce_non_negative_int(candidate)
        if value is not None:
            return value
    return None


def _extract_aggregator(raw_metadata: dict[str, Any]) -> dict[str, Any]:
    aggregator = raw_metadata.get("aggregator")
    return aggregator if isinstance(aggregator, dict) else {}


def _resolve_external_id(
    *,
    platform: str,
    item: NewsItem,
    raw_metadata: dict[str, Any],
    discussion_url: str | None,
) -> str | None:
    aggregator = _extract_aggregator(raw_metadata)
    external_id = (
        _clean_string(item.source_external_id)
        or _clean_string(aggregator.get("external_id"))
        or _clean_string(raw_metadata.get("source_external_id"))
    )
    if external_id:
        return external_id
    if platform == "hackernews" and discussion_url:
        return extract_hackernews_item_id(discussion_url)
    if platform == "reddit" and discussion_url:
        return extract_reddit_submission_id(discussion_url)
    return None


def _resolve_discussion_url(platform: str, item: NewsItem) -> str | None:
    for candidate in (item.discussion_url, item.canonical_item_url):
        normalized = normalize_http_url(candidate)
        if normalized:
            if platform == "reddit":
                return normalize_reddit_discussion_url(normalized) or normalized
            return normalized
    return None


def _thread_title(item: NewsItem, raw_metadata: dict[str, Any]) -> str | None:
    aggregator = _extract_aggregator(raw_metadata)
    return (
        get_news_summary_title(raw_metadata)
        or get_news_article_title(raw_metadata)
        or _clean_string(aggregator.get("title"))
        or _clean_string(item.summary_title)
        or _clean_string(item.article_title)
    )


def sync_news_item_discussion_from_news_item(
    db: Session,
    news_item: NewsItem,
) -> NewsItemDiscussion | None:
    """Create or update the latest discussion row with scrape-time count metadata.

    This is intentionally count-only. Full comments and summaries are fetched by
    queued refresh tasks, explicit refreshes, or the catch-up scraper when the TTL allows it.
    """
    row = (
        db.query(NewsItemDiscussion).filter(NewsItemDiscussion.news_item_id == news_item.id).first()
    )
    return _sync_news_item_discussion(db, news_item=news_item, row=row)


def sync_news_item_discussions_from_news_items(
    db: Session,
    news_items: list[NewsItem],
) -> list[NewsItemDiscussion | None]:
    """Synchronize a batch after preloading existing discussion rows once."""
    if not news_items:
        return []
    news_item_ids = [int(item.id) for item in news_items if item.id is not None]
    existing_by_news_item_id = {
        int(row.news_item_id): row
        for row in db.query(NewsItemDiscussion)
        .filter(NewsItemDiscussion.news_item_id.in_(news_item_ids))
        .all()
        if row.news_item_id is not None
    }
    results: list[NewsItemDiscussion | None] = []
    for news_item in news_items:
        row = _sync_news_item_discussion(
            db,
            news_item=news_item,
            row=(
                existing_by_news_item_id.get(int(news_item.id))
                if news_item.id is not None
                else None
            ),
        )
        results.append(row)
        if row is not None and news_item.id is not None:
            existing_by_news_item_id[int(news_item.id)] = row
    return results


def _sync_news_item_discussion(
    db: Session,
    *,
    news_item: NewsItem,
    row: NewsItemDiscussion | None,
) -> NewsItemDiscussion | None:
    raw_metadata = dict(news_item.raw_metadata or {})
    platform = _normalize_platform(news_item.platform or raw_metadata.get("platform"))
    discussion_url = _resolve_discussion_url(platform, news_item)

    if not _is_supported_platform(platform, discussion_url):
        return None
    if platform not in SUPPORTED_DISCUSSION_PLATFORMS:
        platform = "hackernews" if _is_hackernews_url(discussion_url) else "reddit"

    external_id = _resolve_external_id(
        platform=platform,
        item=news_item,
        raw_metadata=raw_metadata,
        discussion_url=discussion_url,
    )
    if external_id is None and discussion_url is None:
        return None

    if row is None:
        row = NewsItemDiscussion(
            news_item_id=news_item.id,
            platform=platform,
            summary_status="not_ready",
            last_refresh_status="pending",
        )
        db.add(row)

    row.platform = platform
    row.external_id = external_id
    row.discussion_url = discussion_url
    row.title = _thread_title(news_item, raw_metadata) or row.title
    aggregator = _extract_aggregator(raw_metadata)
    row.author = _clean_string(aggregator.get("author")) or row.author
    score = _extract_score_from_metadata(raw_metadata)
    if score is not None:
        row.score = score
    comment_count = _extract_comment_count_from_metadata(raw_metadata)
    if comment_count is not None:
        row.comment_count = comment_count
    row.last_count_checked_at = _utcnow_naive()
    return row


def _ready_representative_news_item_clause():
    return and_(
        NewsItem.status == NewsItemStatus.READY.value,
        NewsItem.representative_news_item_id.is_(None),
    )


def _visible_to_active_user_clause():
    """Return a SQL clause matching rows visible in at least one active user's feed."""
    active_owner_exists = exists(
        select(User.id).where(
            User.id == NewsItem.owner_user_id,
            User.is_active.is_(True),
        )
    )

    aggregator_key = sa_cast(UserScraperConfig.config, JSONB)["key"].astext
    active_aggregator_subscription_exists = exists(
        select(UserScraperConfig.id)
        .join(User, User.id == UserScraperConfig.user_id)
        .where(User.is_active.is_(True))
        .where(UserScraperConfig.scraper_type == AGGREGATOR_SCRAPER_TYPE)
        .where(UserScraperConfig.is_active.is_(True))
        .where(func.lower(aggregator_key).in_(sorted(SUPPORTED_AGGREGATOR_KEYS)))
        .where(func.lower(aggregator_key) == NewsItem.platform)
    )

    fallback_user = aliased(User)
    fallback_config = aliased(UserScraperConfig)
    fallback_news_item = aliased(NewsItem)
    fallback_config_key = sa_cast(fallback_config.config, JSONB)["key"].astext

    fallback_user_has_aggregator = exists(
        select(fallback_config.id)
        .where(fallback_config.user_id == fallback_user.id)
        .where(fallback_config.scraper_type == AGGREGATOR_SCRAPER_TYPE)
        .where(fallback_config.is_active.is_(True))
        .where(func.lower(fallback_config_key).in_(sorted(SUPPORTED_AGGREGATOR_KEYS)))
    )
    fallback_user_has_scoped_scraper_news = exists(
        select(fallback_news_item.id)
        .where(fallback_news_item.visibility_scope == NewsItemVisibilityScope.USER.value)
        .where(fallback_news_item.owner_user_id == fallback_user.id)
        .where(fallback_news_item.user_scraper_config_id.is_not(None))
        .where(
            fallback_news_item.status.in_(
                [
                    NewsItemStatus.NEW.value,
                    NewsItemStatus.PROCESSING.value,
                    NewsItemStatus.READY.value,
                ]
            )
        )
    )
    active_fallback_user_exists = exists(
        select(fallback_user.id)
        .where(fallback_user.is_active.is_(True))
        .where(~fallback_user_has_aggregator)
        .where(~fallback_user_has_scoped_scraper_news)
    )

    user_scoped_clause = and_(
        NewsItem.visibility_scope == NewsItemVisibilityScope.USER.value,
        active_owner_exists,
    )
    subscribed_global_clause = and_(
        NewsItem.visibility_scope == NewsItemVisibilityScope.GLOBAL.value,
        active_aggregator_subscription_exists,
    )
    fallback_global_clause = and_(
        NewsItem.visibility_scope == NewsItemVisibilityScope.GLOBAL.value,
        or_(NewsItem.source_type.is_(None), NewsItem.source_type != "reddit"),
        active_fallback_user_exists,
    )
    return or_(user_scoped_clause, subscribed_global_clause, fallback_global_clause)


def _supported_discussion_news_item_clause():
    return or_(
        NewsItem.platform.in_(["hackernews", "reddit", "hn"]),
        NewsItem.discussion_url.ilike("%ycombinator.com%"),
        NewsItem.discussion_url.ilike("%reddit.com%"),
        NewsItem.canonical_item_url.ilike("%ycombinator.com%"),
        NewsItem.canonical_item_url.ilike("%reddit.com%"),
    )


def is_news_item_discussion_visible_to_active_user(db: Session, *, news_item_id: int) -> bool:
    """Return whether a news item is currently visible in at least one active user's feed."""
    return (
        db.query(NewsItem.id)
        .filter(NewsItem.id == news_item_id)
        .filter(_ready_representative_news_item_clause())
        .filter(_visible_to_active_user_clause())
        .first()
        is not None
    )


def should_enqueue_news_item_discussion_refresh(
    db: Session,
    *,
    row: NewsItemDiscussion,
    now: datetime | None = None,
) -> bool:
    """Return whether a scrape should enqueue a full discussion refresh."""
    if row.news_item_id is None:
        return False
    if row.platform not in SUPPORTED_DISCUSSION_PLATFORMS:
        return False
    if not row.external_id or not row.discussion_url:
        return False

    current_time = now or _utcnow_naive()
    if row.next_refresh_after is not None and row.next_refresh_after > current_time:
        return False
    if row.last_refresh_status in TERMINAL_REFRESH_STATUSES:
        return False
    return (
        db.query(NewsItem.id)
        .filter(NewsItem.id == row.news_item_id)
        .filter(_ready_representative_news_item_clause())
        .filter(_visible_to_active_user_clause())
        .filter(_supported_discussion_news_item_clause())
        .first()
        is not None
    )


def news_item_discussion_refresh_ids(
    db: Session,
    *,
    rows: Iterable[NewsItemDiscussion | None],
    now: datetime | None = None,
) -> set[int]:
    """Return refresh-eligible ids using one visibility query for a batch."""
    current_time = now or _utcnow_naive()
    candidate_ids = {
        int(row.news_item_id)
        for row in rows
        if row is not None
        and row.news_item_id is not None
        and row.platform in SUPPORTED_DISCUSSION_PLATFORMS
        and bool(row.external_id)
        and bool(row.discussion_url)
        and not (row.next_refresh_after is not None and row.next_refresh_after > current_time)
        and row.last_refresh_status not in TERMINAL_REFRESH_STATUSES
    }
    if not candidate_ids:
        return set()
    return {
        int(news_item_id)
        for (news_item_id,) in db.query(NewsItem.id)
        .filter(NewsItem.id.in_(candidate_ids))
        .filter(_ready_representative_news_item_clause())
        .filter(_visible_to_active_user_clause())
        .filter(_supported_discussion_news_item_clause())
        .all()
    }


def sync_missing_visible_news_item_discussions(db: Session, *, limit: int = 500) -> int:
    """Backfill discussion rows only for supported items visible to active users."""
    existing_ids = db.query(NewsItemDiscussion.news_item_id)
    candidates = (
        db.query(NewsItem)
        .filter(NewsItem.id.notin_(existing_ids))
        .filter(_ready_representative_news_item_clause())
        .filter(_visible_to_active_user_clause())
        .filter(_supported_discussion_news_item_clause())
        .order_by(
            func.coalesce(
                NewsItem.published_at,
                NewsItem.processed_at,
                NewsItem.ingested_at,
                NewsItem.created_at,
            ).desc(),
            NewsItem.id.desc(),
        )
        .limit(limit)
        .all()
    )
    created_or_updated = 0
    for item in candidates:
        if sync_news_item_discussion_from_news_item(db, item) is not None:
            created_or_updated += 1
    if created_or_updated:
        db.commit()
    return created_or_updated


def list_due_news_item_discussion_refresh_candidates(
    db: Session,
    *,
    limit: int = DEFAULT_DISCUSSION_REFRESH_ENQUEUE_LIMIT,
    now: datetime | None = None,
) -> list[int]:
    """Return prioritized visible news-item IDs needing full discussion refresh."""
    normalized_limit = max(1, min(limit, 1_000))
    current_time = now or _utcnow_naive()

    comment_delta = func.coalesce(NewsItemDiscussion.comment_count, 0) - func.coalesce(
        NewsItemDiscussion.fetched_comment_count,
        0,
    )
    missing_summary = or_(
        NewsItemDiscussion.summary.is_(None),
        NewsItemDiscussion.summary_status != "completed",
        NewsItemDiscussion.raw_comments_ref.is_(None),
    )
    sort_timestamp = func.coalesce(
        NewsItem.published_at,
        NewsItem.processed_at,
        NewsItem.ingested_at,
        NewsItem.created_at,
    )

    rows = (
        db.query(NewsItemDiscussion.news_item_id)
        .join(NewsItem, NewsItem.id == NewsItemDiscussion.news_item_id)
        .filter(NewsItemDiscussion.platform.in_(list(SUPPORTED_DISCUSSION_PLATFORMS)))
        .filter(
            or_(
                NewsItemDiscussion.last_refresh_status.is_(None),
                NewsItemDiscussion.last_refresh_status.notin_(list(TERMINAL_REFRESH_STATUSES)),
            )
        )
        .filter(_ready_representative_news_item_clause())
        .filter(_visible_to_active_user_clause())
        .filter(_supported_discussion_news_item_clause())
        .filter(
            or_(
                NewsItemDiscussion.next_refresh_after.is_(None),
                NewsItemDiscussion.next_refresh_after <= current_time,
            )
        )
        .order_by(
            case((missing_summary, 1), else_=0).desc(),
            comment_delta.desc(),
            sort_timestamp.desc(),
            NewsItemDiscussion.next_refresh_after.asc().nullsfirst(),
            NewsItemDiscussion.updated_at.asc(),
        )
        .limit(normalized_limit)
        .all()
    )
    return [int(news_item_id) for (news_item_id,) in rows if news_item_id is not None]


def _fetch_hackernews_comments(
    *,
    external_id: str,
    discussion_url: str,
) -> dict[str, Any]:
    discussion = fetch_hackernews_comments(
        external_id=external_id,
        discussion_url=discussion_url,
        comment_cap=MAX_STORED_COMMENTS,
    )
    return _news_item_provider_payload(discussion)


def _fetch_reddit_comments(
    *,
    external_id: str,
    discussion_url: str,
) -> dict[str, Any]:
    discussion = fetch_reddit_comments(
        external_id=external_id,
        discussion_url=discussion_url,
        comment_cap=MAX_STORED_COMMENTS,
    )
    return _news_item_provider_payload(discussion)


def _news_item_provider_payload(discussion: NormalizedDiscussion) -> dict[str, Any]:
    thread: dict[str, Any] = {
        "title": discussion.thread.title,
        "author": discussion.thread.author,
        "score": discussion.thread.score,
        "comment_count": discussion.thread.comment_count,
        "created_at": discussion.thread.created_at,
    }
    if discussion.platform == "reddit":
        thread["subreddit"] = discussion.thread.subreddit

    return {
        "platform": discussion.platform,
        "external_id": discussion.external_id,
        "discussion_url": discussion.discussion_url,
        "thread": thread,
        "comments": [comment.as_payload() for comment in discussion.comments],
        "links": [link.as_payload() for link in discussion.links],
        "stats": {
            "provider": discussion.provider,
            "declared_comment_count": discussion.thread.comment_count,
            "fetched_count": discussion.fetched_count,
            "total_seen": discussion.total_seen,
            "stored_comment_cap": discussion.comment_cap,
            "cap_reached": discussion.cap_reached,
        },
    }


def _fetch_discussion_comments(row: NewsItemDiscussion) -> dict[str, Any]:
    if not row.external_id:
        raise DiscussionFetchError("Discussion external id is missing", retryable=False)
    if not row.discussion_url:
        raise DiscussionFetchError("Discussion URL is missing", retryable=False)
    if row.platform == "hackernews":
        return _fetch_hackernews_comments(
            external_id=row.external_id,
            discussion_url=row.discussion_url,
        )
    if row.platform == "reddit":
        return _fetch_reddit_comments(
            external_id=row.external_id,
            discussion_url=row.discussion_url,
        )
    raise DiscussionFetchError(f"Unsupported discussion platform: {row.platform}", retryable=False)


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _build_storage_key(*, news_item_id: int, sha256: str) -> str:
    prefix = get_settings().storage.content_body_storage_prefix.strip("/")
    return f"{prefix}/news-item-discussions/{news_item_id}/comments-{sha256}.json"


def _persist_raw_comments(
    *,
    news_item_id: int,
    raw_payload: dict[str, Any],
    gateway: ObjectStorageGateway | None,
) -> tuple[dict[str, Any], str]:
    encoded = _canonical_json(raw_payload)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    storage_key = _build_storage_key(news_item_id=news_item_id, sha256=digest)
    storage_gateway = gateway or get_object_storage_gateway()
    stored = storage_gateway.put_text(
        key=storage_key,
        text=json.dumps(raw_payload, ensure_ascii=False, indent=2, sort_keys=True),
        content_type="application/json",
    )
    comments = raw_payload.get("comments")
    return (
        {
            "kind": "storage",
            "storage_provider": stored.provider,
            "storage_bucket": stored.bucket,
            "storage_key": stored.key,
            "content_format": "json",
            "sha256": digest,
            "byte_size": stored.size_bytes,
            "comment_count": len(comments) if isinstance(comments, list) else 0,
            "updated_at": _utcnow_naive().isoformat(),
        },
        digest,
    )


def _mark_refresh_failure(
    db: Session,
    *,
    row: NewsItemDiscussion,
    error_message: str,
    retryable: bool,
) -> NewsItemDiscussionRefreshResult:
    now = _utcnow_naive()
    row.last_refresh_status = "failed"
    row.last_refresh_error = error_message
    row.next_refresh_after = now + DISCUSSION_REFRESH_TTL
    if row.summary is None:
        row.summary_status = "failed"
    db.commit()
    return NewsItemDiscussionRefreshResult(
        success=False,
        status="failed",
        error_message=error_message,
        retryable=retryable,
    )


def _mark_terminal_refresh(
    db: Session,
    *,
    row: NewsItemDiscussion,
    status: str,
    error_message: str,
) -> NewsItemDiscussionRefreshResult:
    row.last_refresh_status = status
    row.last_refresh_error = error_message
    row.next_refresh_after = None
    if row.summary is None:
        row.summary_status = status
    db.commit()
    return NewsItemDiscussionRefreshResult(
        success=False,
        status=status,
        error_message=error_message,
        retryable=False,
    )


def _skipped_refresh_result(row: NewsItemDiscussion) -> NewsItemDiscussionRefreshResult:
    retryable = row.last_refresh_status != "failed"
    return NewsItemDiscussionRefreshResult(
        success=retryable,
        status="skipped",
        error_message=row.last_refresh_error,
        refreshed=False,
        summarized=False,
        retryable=retryable,
    )


def _refresh_deferred_by_ttl(row: NewsItemDiscussion, *, now: datetime) -> bool:
    """Return whether a recent result or active refresh should suppress a fetch."""
    if row.next_refresh_after is None or row.next_refresh_after <= now:
        return False
    return bool(
        row.raw_comments_ref is not None or row.last_refresh_status in REFRESH_TTL_HOLD_STATUSES
    )


def _claim_news_item_discussion_refresh(
    db: Session,
    *,
    row: NewsItemDiscussion,
    now: datetime,
    force: bool,
) -> bool:
    """Atomically claim a discussion refresh before network or LLM work starts."""
    db.flush()
    if row.id is None:
        raise ValueError("News item discussion row is missing an id")

    lease_expires_at = now + DISCUSSION_REFRESH_LEASE_TTL
    if force:
        claim_filter = or_(
            NewsItemDiscussion.last_refresh_status != REFRESH_STATUS_PROCESSING,
            NewsItemDiscussion.next_refresh_after.is_(None),
            NewsItemDiscussion.next_refresh_after <= now,
        )
    else:
        claim_filter = or_(
            NewsItemDiscussion.next_refresh_after.is_(None),
            NewsItemDiscussion.next_refresh_after <= now,
            and_(
                NewsItemDiscussion.raw_comments_ref.is_(None),
                NewsItemDiscussion.last_refresh_status.notin_(REFRESH_EMPTY_FETCH_BLOCK_STATUSES),
            ),
        )

    claimed = (
        db.query(NewsItemDiscussion)
        .filter(NewsItemDiscussion.id == row.id)
        .filter(claim_filter)
        .update(
            {
                NewsItemDiscussion.last_refresh_status: REFRESH_STATUS_PROCESSING,
                NewsItemDiscussion.last_refresh_error: None,
                NewsItemDiscussion.next_refresh_after: lease_expires_at,
            },
            synchronize_session=False,
        )
    )
    db.commit()
    db.refresh(row)
    return bool(claimed)


def refresh_news_item_discussion(
    db: Session,
    *,
    news_item_id: int,
    force: bool = False,
    gateway: ObjectStorageGateway | None = None,
    summarizer: ContentSummarizer | None = None,
) -> NewsItemDiscussionRefreshResult:
    """Refresh comments and summary for one news item when the TTL allows it."""
    item = db.query(NewsItem).filter(NewsItem.id == news_item_id).first()
    if item is None:
        return NewsItemDiscussionRefreshResult(
            success=False,
            status="failed",
            error_message="News item not found",
            retryable=False,
        )

    row = sync_news_item_discussion_from_news_item(db, item)
    if row is None:
        return NewsItemDiscussionRefreshResult(
            success=False,
            status="unsupported",
            error_message="News item does not have a supported discussion source",
            retryable=False,
        )

    now = _utcnow_naive()
    if not force and row.last_refresh_status in TERMINAL_REFRESH_STATUSES:
        db.commit()
        return NewsItemDiscussionRefreshResult(
            success=False,
            status=row.last_refresh_status or REFRESH_STATUS_GONE,
            error_message=row.last_refresh_error,
            retryable=False,
        )
    if not force and _refresh_deferred_by_ttl(row, now=now):
        db.commit()
        return _skipped_refresh_result(row)

    if not _claim_news_item_discussion_refresh(db, row=row, now=now, force=force):
        return _skipped_refresh_result(row)

    try:
        raw_payload = _fetch_discussion_comments(row)
    except TerminalNewsItemDiscussionUnavailable as exc:
        return _mark_terminal_refresh(
            db,
            row=row,
            status=exc.status,
            error_message=str(exc),
        )
    except DiscussionFetchError as exc:
        return _mark_refresh_failure(
            db,
            row=row,
            error_message=str(exc),
            retryable=exc.retryable,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "News item discussion fetch failed",
            extra={
                "component": "news_item_discussions",
                "operation": "refresh.fetch",
                "item_id": str(news_item_id),
                "context_data": {"platform": row.platform, "error": str(exc)},
            },
        )
        return _mark_refresh_failure(
            db,
            row=row,
            error_message=str(exc),
            retryable=True,
        )

    raw_thread = raw_payload.get("thread")
    thread = raw_thread if isinstance(raw_thread, dict) else {}
    row.title = _clean_string(thread.get("title")) or row.title
    row.author = _clean_string(thread.get("author")) or row.author
    score = _coerce_non_negative_int(thread.get("score"))
    if score is not None:
        row.score = score
    comment_count = _coerce_non_negative_int(thread.get("comment_count"))
    if comment_count is not None:
        row.comment_count = comment_count
    row.fetched_comment_count = _coerce_non_negative_int(
        _extract_nested(raw_payload, ("stats", "fetched_count"))
    )
    row.last_count_checked_at = now
    row.last_comments_fetched_at = now
    row.next_refresh_after = now + DISCUSSION_REFRESH_TTL

    raw_ref, raw_sha = _persist_raw_comments(
        news_item_id=news_item_id,
        raw_payload=raw_payload,
        gateway=gateway,
    )
    previous_sha = row.raw_comments_sha256
    row.raw_comments_ref = raw_ref
    row.raw_comments_sha256 = raw_sha
    row.last_refresh_status = "completed"
    row.last_refresh_error = None

    summarized = False
    summary_input = build_discussion_summary_input(row=row, raw_payload=raw_payload)
    summary_plan = plan_discussion_summary(
        row=row,
        summary_input=summary_input,
        previous_raw_sha=previous_sha,
        current_raw_sha=raw_sha,
    )

    if summary_input.comment_count == 0 and row.summary is None:
        row.summary_status = "not_ready"
    elif summary_plan.mode == DiscussionSummaryPlanMode.TRACK_SUMMARIZED:
        store_summarized_summary_tracking(
            row=row,
            summary_input=summary_input,
            incremental_update_count=row.summary_incremental_update_count or 0,
        )
        store_seen_summary_tracking(row=row, summary_input=summary_input)
    elif summary_plan.mode == DiscussionSummaryPlanMode.TRACK_SEEN:
        store_seen_summary_tracking(row=row, summary_input=summary_input)
        logger.info(
            "Skipping discussion summary update below material comment threshold",
            extra={
                "component": "news_item_discussions",
                "operation": "refresh.summarize.skip",
                "item_id": str(news_item_id),
                "context_data": {
                    "platform": row.platform,
                    "changed_comment_count": len(summary_plan.changed_comments),
                    "threshold": DISCUSSION_SUMMARY_MATERIAL_COMMENT_THRESHOLD,
                    "summary_input_sha256": summary_input.input_sha256,
                },
            },
        )

    if summary_plan.mode in {DiscussionSummaryPlanMode.FULL, DiscussionSummaryPlanMode.MERGE}:
        try:
            summary_execution = execute_discussion_summary_plan(
                db,
                row=row,
                news_item=item,
                summary_input=summary_input,
                plan=summary_plan,
                summarizer=summarizer,
            )
            summary = summary_execution.summary
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "News item discussion summarization failed",
                extra={
                    "component": "news_item_discussions",
                    "operation": "refresh.summarize",
                    "item_id": str(news_item_id),
                    "context_data": {"platform": row.platform, "error": str(exc)},
                },
            )
            row.summary_status = "failed"
            row.last_refresh_status = "failed"
            row.last_refresh_error = str(exc)
            db.commit()
            return NewsItemDiscussionRefreshResult(
                success=False,
                status="failed",
                error_message=str(exc),
                refreshed=True,
                summarized=False,
                retryable=True,
            )

        if summary.external_discussion_url is None and row.discussion_url:
            summary.external_discussion_url = row.discussion_url
        row.summary = summary.model_dump(mode="json", exclude_none=True)
        row.summary_status = "completed"
        row.summary_version = NEWS_DISCUSSION_SUMMARY_VERSION
        row.summary_model = getattr(summarizer, "model_hint", None) if summarizer else None
        row.summary_generated_at = _utcnow_naive()
        store_summarized_summary_tracking(
            row=row,
            summary_input=summary_input,
            incremental_update_count=(
                (row.summary_incremental_update_count or 0) + 1
                if summary_execution.mode == DiscussionSummaryPlanMode.MERGE
                else 0
            ),
        )
        store_seen_summary_tracking(row=row, summary_input=summary_input)
        summarized = True
        bump_briefing_version_for_news_item(db, news_item_id=news_item_id)

    db.commit()
    return NewsItemDiscussionRefreshResult(
        success=True,
        status="completed",
        refreshed=True,
        summarized=summarized,
        retryable=True,
    )
