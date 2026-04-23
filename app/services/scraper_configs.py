"""Service helpers for per-user scraper configurations."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.constants import CONTENT_STATUS_DIGEST_SOURCE, CONTENT_STATUS_INBOX
from app.core.logging import get_logger
from app.core.settings import get_settings
from app.models.internal.scraper_configs import (
    ALLOWED_SCRAPER_TYPES,
    CreateUserScraperConfig,
    UpdateUserScraperConfig,
)
from app.models.metadata import ContentStatus, ContentType
from app.models.schema import (
    Content,
    ContentReadStatus,
    ContentStatusEntry,
    ProcessingTask,
    UserScraperConfig,
)
from app.models.user import User
from app.utils.dates import parse_date_with_tz

logger = get_logger(__name__)
settings = get_settings()


class ScraperConfigAlreadyExistsError(ValueError):
    """Raised when a user already has a scraper config for the given feed.

    Subclasses ``ValueError`` so legacy ``except ValueError`` callers keep
    working; new callers should match this type for an explicit idempotency
    branch.
    """


def _normalize_feed_url(config: dict[str, Any]) -> str:
    feed_url = (config.get("feed_url") or "").strip()
    return feed_url


def _extract_limit(config: dict[str, Any], default_limit: int) -> int:
    limit = config.get("limit")
    if isinstance(limit, int) and 1 <= limit <= 100:
        return limit
    return default_limit


def list_user_scraper_configs(
    db: Session, user_id: int, allowed_types: set[str] | None = None
) -> list[UserScraperConfig]:
    """Return scraper configs for a user, optionally filtered by types."""
    query = db.query(UserScraperConfig).filter(UserScraperConfig.user_id == user_id)
    if allowed_types:
        query = query.filter(UserScraperConfig.scraper_type.in_(allowed_types))
    return query.order_by(UserScraperConfig.created_at.desc()).all()


def list_active_configs_by_type(db: Session, scraper_type: str) -> list[UserScraperConfig]:
    """Return active scraper configs for a given type."""
    if scraper_type not in ALLOWED_SCRAPER_TYPES:
        return []
    return (
        db.query(UserScraperConfig)
        .filter(
            and_(
                UserScraperConfig.is_active.is_(True),
                UserScraperConfig.scraper_type == scraper_type,
            )
        )
        .all()
    )


def _normalize_feed_url_for_stats(feed_url: str | None) -> str | None:
    if not isinstance(feed_url, str):
        return None
    trimmed = feed_url.strip()
    if not trimmed:
        return None
    parsed = urlparse(trimmed)
    if not parsed.scheme or not parsed.netloc:
        return trimmed.rstrip("/")
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{path}"


def _extract_feed_domain(feed_url: str | None) -> str | None:
    if not isinstance(feed_url, str) or not feed_url.strip():
        return None
    parsed = urlparse(feed_url.strip())
    domain = (parsed.netloc or "").lower()
    return domain or None


def _coerce_config_id(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _require_config_id(config: UserScraperConfig) -> int:
    config_id = config.id
    if config_id is None:
        raise ValueError("Scraper config is missing an id")
    return int(config_id)


def _config_data(config: UserScraperConfig) -> dict[str, Any]:
    return config.config if isinstance(config.config, dict) else {}


def _coerce_publication_date(content: Content) -> datetime:
    if content.publication_date is not None:
        return content.publication_date

    metadata = content.content_metadata or {}
    parsed = parse_date_with_tz(metadata.get("publication_date"))
    if parsed is not None:
        return parsed.replace(tzinfo=None)

    if content.created_at is not None:
        return content.created_at
    return datetime.now(UTC).replace(tzinfo=None)


def _estimate_next_expected_at(
    publication_dates: list[datetime],
) -> tuple[datetime | None, float | None, int]:
    unique_dates = sorted({value for value in publication_dates if value is not None}, reverse=True)
    if len(unique_dates) < 2:
        return None, None, 0

    intervals_hours: list[float] = []
    for newer, older in zip(unique_dates, unique_dates[1:5], strict=False):
        delta_hours = (newer - older).total_seconds() / 3600
        if delta_hours <= 0:
            continue
        intervals_hours.append(delta_hours)

    if not intervals_hours:
        return None, None, 0

    average_interval_hours = sum(intervals_hours) / len(intervals_hours)
    predicted = unique_dates[0] + timedelta(hours=average_interval_hours)
    return predicted, average_interval_hours, len(intervals_hours)


def _match_scraper_config_for_content(
    content: Content,
    *,
    configs_by_id: dict[int, UserScraperConfig],
    configs_by_feed_url: dict[str, list[UserScraperConfig]],
    configs_by_source: dict[str, list[UserScraperConfig]],
) -> UserScraperConfig | None:
    metadata = content.content_metadata or {}

    config_id = _coerce_config_id(metadata.get("feed_config_id"))
    if config_id is not None:
        matched = configs_by_id.get(config_id)
        if matched is not None:
            return matched

    normalized_feed_url = _normalize_feed_url_for_stats(metadata.get("feed_url"))
    if normalized_feed_url is not None:
        feed_matches = configs_by_feed_url.get(normalized_feed_url, [])
        if len(feed_matches) == 1:
            return feed_matches[0]

    source_value = content.source or metadata.get("source")
    if isinstance(source_value, str):
        normalized_source = source_value.strip().lower()
        if normalized_source:
            source_matches = configs_by_source.get(normalized_source, [])
            if len(source_matches) == 1:
                return source_matches[0]

    return None


def get_scraper_config_stats(
    db: Session, *, user_id: int, configs: Iterable[UserScraperConfig]
) -> dict[int, dict[str, Any]]:
    """Return derived per-config stats for scraper sources belonging to a user."""
    config_list = list(configs)
    if not config_list:
        return {}

    long_form_types = {ContentType.ARTICLE.value, ContentType.PODCAST.value}
    processing_statuses = {
        ContentStatus.NEW.value,
        ContentStatus.PENDING.value,
        ContentStatus.PROCESSING.value,
        ContentStatus.AWAITING_IMAGE.value,
    }
    completed_status = ContentStatus.COMPLETED.value
    processing_cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(
        minutes=settings.checkout_timeout_minutes
    )

    configs_by_id: dict[int, UserScraperConfig] = {
        _require_config_id(config): config for config in config_list
    }
    configs_by_feed_url: dict[str, list[UserScraperConfig]] = defaultdict(list)
    configs_by_source: dict[str, list[UserScraperConfig]] = defaultdict(list)

    for config in config_list:
        config_data = _config_data(config)
        config_feed_url = config.feed_url or config_data.get("feed_url")
        normalized_feed_url = _normalize_feed_url_for_stats(config_feed_url)
        if normalized_feed_url is not None:
            configs_by_feed_url[normalized_feed_url].append(config)

        candidate_labels = [
            config.display_name,
            config_data.get("name"),
            _extract_feed_domain(config.feed_url or config_data.get("feed_url")),
        ]
        for label in candidate_labels:
            if isinstance(label, str):
                normalized_label = label.strip().lower()
                if normalized_label:
                    configs_by_source[normalized_label].append(config)

    content_rows = (
        db.query(Content)
        .join(ContentStatusEntry, ContentStatusEntry.content_id == Content.id)
        .filter(ContentStatusEntry.user_id == user_id)
        .filter(ContentStatusEntry.status == CONTENT_STATUS_INBOX)
        .filter(
            (Content.content_type.in_(long_form_types))
            | ((Content.platform == "youtube") & (Content.content_type != ContentType.NEWS.value))
        )
        .all()
    )

    active_task_content_ids = {
        content_id
        for (content_id,) in db.query(ProcessingTask.content_id)
        .filter(ProcessingTask.content_id.is_not(None))
        .filter(
            ProcessingTask.status.in_([ContentStatus.PENDING.value, ContentStatus.PROCESSING.value])
        )
        .all()
        if content_id is not None
    }
    read_content_ids = {
        content_id
        for (content_id,) in db.query(ContentReadStatus.content_id)
        .filter(ContentReadStatus.user_id == user_id)
        .all()
    }

    stats_by_config: dict[int, dict[str, Any]] = {
        _require_config_id(config): {
            "total_count": 0,
            "completed_count": 0,
            "unread_count": 0,
            "processing_count": 0,
            "latest_processed_at": None,
            "latest_publication_at": None,
            "next_expected_at": None,
            "average_interval_hours": None,
            "interval_sample_size": 0,
            "_publication_dates": [],
        }
        for config in config_list
    }

    for content in content_rows:
        matched_config = _match_scraper_config_for_content(
            content,
            configs_by_id=configs_by_id,
            configs_by_feed_url=configs_by_feed_url,
            configs_by_source=configs_by_source,
        )
        if matched_config is None:
            continue

        matched_config_id = _require_config_id(matched_config)
        stats = stats_by_config[matched_config_id]
        stats["total_count"] += 1

        publication_date = _coerce_publication_date(content)
        if (
            stats["latest_publication_at"] is None
            or publication_date > stats["latest_publication_at"]
        ):
            stats["latest_publication_at"] = publication_date
        stats["_publication_dates"].append(publication_date)

        if content.processed_at is not None and (
            stats["latest_processed_at"] is None
            or content.processed_at > stats["latest_processed_at"]
        ):
            stats["latest_processed_at"] = content.processed_at

        is_completed_visible = content.status == completed_status and (
            content.classification is None or content.classification != "skip"
        )
        if is_completed_visible:
            stats["completed_count"] += 1
            if content.id not in read_content_ids:
                stats["unread_count"] += 1

        is_active_processing = content.status in processing_statuses and (
            content.id in active_task_content_ids
            or (
                content.checked_out_by is not None
                and content.checked_out_at is not None
                and content.checked_out_at >= processing_cutoff
            )
        )
        if is_active_processing:
            stats["processing_count"] += 1

    for _config_id, stats in stats_by_config.items():
        next_expected_at, average_interval_hours, interval_sample_size = _estimate_next_expected_at(
            stats.pop("_publication_dates")
        )
        stats["next_expected_at"] = next_expected_at
        stats["average_interval_hours"] = average_interval_hours
        stats["interval_sample_size"] = interval_sample_size

    return stats_by_config


def create_user_scraper_config(
    db: Session, user_id: int, data: CreateUserScraperConfig
) -> UserScraperConfig:
    """Create a new scraper config for a user."""
    feed_url = _normalize_feed_url(data.config)
    if data.scraper_type not in ALLOWED_SCRAPER_TYPES:
        raise ValueError("Unsupported scraper_type")

    normalized_config = {**data.config, "feed_url": feed_url}

    existing = (
        db.query(UserScraperConfig)
        .filter(
            and_(
                UserScraperConfig.user_id == user_id,
                UserScraperConfig.scraper_type == data.scraper_type,
                UserScraperConfig.feed_url == feed_url,
            )
        )
        .first()
    )
    if existing:
        raise ScraperConfigAlreadyExistsError("Scraper config already exists for this feed")

    record = UserScraperConfig(
        user_id=user_id,
        scraper_type=data.scraper_type,
        display_name=data.display_name,
        config=normalized_config,
        feed_url=feed_url,
        is_active=data.is_active,
    )
    db.add(record)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ScraperConfigAlreadyExistsError(
            "Scraper config already exists for this feed"
        ) from exc

    db.refresh(record)
    return record


def update_user_scraper_config(
    db: Session, user_id: int, config_id: int, data: UpdateUserScraperConfig
) -> UserScraperConfig:
    """Update an existing scraper config for a user."""
    record = (
        db.query(UserScraperConfig)
        .filter(
            and_(
                UserScraperConfig.id == config_id,
                UserScraperConfig.user_id == user_id,
            )
        )
        .first()
    )
    if not record:
        raise ValueError("Scraper config not found")

    if data.display_name is not None:
        record.display_name = data.display_name
    if data.config is not None:
        normalized_feed_url = _normalize_feed_url(data.config)
        normalized_config = {**data.config, "feed_url": normalized_feed_url}
        record.config = normalized_config
        record.feed_url = normalized_feed_url
    if data.is_active is not None:
        record.is_active = data.is_active

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ScraperConfigAlreadyExistsError(
            "Scraper config already exists for this feed"
        ) from exc

    db.refresh(record)
    return record


def delete_user_scraper_config(db: Session, user_id: int, config_id: int) -> None:
    """Delete a scraper config for a user."""
    record = (
        db.query(UserScraperConfig)
        .filter(
            and_(
                UserScraperConfig.id == config_id,
                UserScraperConfig.user_id == user_id,
            )
        )
        .first()
    )
    if not record:
        raise ValueError("Scraper config not found")

    db.delete(record)
    db.commit()


def build_feed_payloads(
    configs: Iterable[UserScraperConfig], default_limit: int = 10
) -> list[dict[str, Any]]:
    """Convert UserScraperConfig rows into scraper feed payloads."""
    feeds: list[dict[str, Any]] = []
    for config in configs:
        config_data = _config_data(config)
        feed_url = _normalize_feed_url(config_data)
        if not feed_url:
            logger.warning("Skipping config without feed_url. id=%s", config.id)
            continue
        limit = _extract_limit(config_data, default_limit)
        display_name = config.display_name
        config_name = config_data.get("name")
        feeds.append(
            {
                "url": feed_url,
                "name": display_name or config_name or "Custom feed",
                "display_name": display_name,
                "config_name": config_name,
                "limit": limit,
                "user_id": config.user_id,
                "config_id": config.id,
            }
        )
    return feeds


def ensure_inbox_status(
    db: Session, user_id: int | None, content_id: int, content_type: str | None = None
) -> bool:
    """Ensure a content_status row exists for this user/content."""
    if user_id is None:
        return False
    if content_type and not should_add_to_inbox(content_type):
        return False

    existing = (
        db.query(ContentStatusEntry)
        .filter(
            and_(
                ContentStatusEntry.user_id == user_id,
                ContentStatusEntry.content_id == content_id,
            )
        )
        .first()
    )
    if existing:
        if existing.status == CONTENT_STATUS_DIGEST_SOURCE:
            existing.status = CONTENT_STATUS_INBOX
            return True
        return False

    db.add(
        ContentStatusEntry(
            user_id=user_id,
            content_id=content_id,
            status=CONTENT_STATUS_INBOX,
        )
    )
    return True


def should_add_to_inbox(content_type: str | None) -> bool:
    """Return True when a content type should be added to the inbox."""
    if not content_type:
        return False
    return content_type in ("article", "podcast", "news", "unknown")


def list_active_user_ids(db: Session) -> list[int]:
    """Return active user ids for inbox backfills."""
    rows = db.query(User.id).filter(User.is_active.is_(True)).all()
    return [row[0] for row in rows]
