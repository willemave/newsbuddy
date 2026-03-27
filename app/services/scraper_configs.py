"""Service helpers for per-user scraper configurations."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.constants import CONTENT_STATUS_DIGEST_SOURCE, CONTENT_STATUS_INBOX, DEFAULT_NEW_FEED_LIMIT
from app.core.logging import get_logger
from app.models.schema import ContentStatusEntry, UserScraperConfig
from app.models.user import User

logger = get_logger(__name__)

ALLOWED_SCRAPER_TYPES = {"substack", "atom", "podcast_rss", "youtube", "reddit"}


class CreateUserScraperConfig(BaseModel):
    """Payload for creating a scraper config."""

    scraper_type: Literal["substack", "atom", "podcast_rss", "youtube", "reddit"]
    display_name: str | None = Field(None, max_length=255)
    config: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True

    @model_validator(mode="after")
    def validate_config(self) -> CreateUserScraperConfig:
        if self.scraper_type == "youtube":
            self.config = _normalize_youtube_config(self.config)
        elif self.scraper_type == "reddit":
            self.config = _normalize_reddit_config(self.config)
        else:
            self.config = _normalize_feed_config(self.config)
        if "limit" not in self.config:
            self.config["limit"] = DEFAULT_NEW_FEED_LIMIT
        return self


class UpdateUserScraperConfig(BaseModel):
    """Payload for updating a scraper config."""

    display_name: str | None = Field(None, max_length=255)
    config: dict[str, Any] | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def validate_config(self) -> UpdateUserScraperConfig:
        if self.config is None:
            return self
        self.config = _normalize_update_config(self.config)
        return self


def _normalize_feed_config(config: dict[str, Any]) -> dict[str, Any]:
    feed_url = config.get("feed_url")
    if not isinstance(feed_url, str) or not feed_url.strip():
        raise ValueError("config.feed_url is required")
    config["feed_url"] = feed_url.strip()

    limit = config.get("limit")
    if limit is not None and (not isinstance(limit, int) or not 1 <= limit <= 100):
        raise ValueError("config.limit must be an integer between 1 and 100")
    return config


def _normalize_youtube_config(config: dict[str, Any]) -> dict[str, Any]:
    channel_id = config.get("channel_id")
    playlist_id = config.get("playlist_id")
    feed_url = config.get("feed_url") or config.get("url")

    if isinstance(channel_id, str):
        channel_id = channel_id.strip()
    if isinstance(playlist_id, str):
        playlist_id = playlist_id.strip()

    if not feed_url:
        if playlist_id:
            feed_url = f"https://www.youtube.com/playlist?list={playlist_id}"
        elif channel_id:
            feed_url = f"https://www.youtube.com/channel/{channel_id}"

    if not isinstance(feed_url, str) or not feed_url.strip():
        raise ValueError("youtube config requires feed_url, channel_id, or playlist_id")

    config["feed_url"] = feed_url.strip()
    if channel_id:
        config["channel_id"] = channel_id
    if playlist_id:
        config["playlist_id"] = playlist_id

    limit = config.get("limit")
    if limit is not None and (not isinstance(limit, int) or not 1 <= limit <= 100):
        raise ValueError("config.limit must be an integer between 1 and 100")
    return config


def _normalize_reddit_config(config: dict[str, Any]) -> dict[str, Any]:
    subreddit = (config.get("subreddit") or config.get("name") or "").strip()
    subreddit = subreddit.removeprefix("r/").strip("/")
    if not subreddit:
        raise ValueError("config.subreddit is required")

    config["subreddit"] = subreddit
    config["feed_url"] = f"https://www.reddit.com/r/{subreddit}/"

    limit = config.get("limit")
    if limit is not None and (not isinstance(limit, int) or not 1 <= limit <= 100):
        raise ValueError("config.limit must be an integer between 1 and 100")
    return config


def _normalize_update_config(config: dict[str, Any]) -> dict[str, Any]:
    feed_url = config.get("feed_url")
    channel_id = config.get("channel_id")
    playlist_id = config.get("playlist_id")
    subreddit = config.get("subreddit") or config.get("name")

    if not feed_url and (channel_id or playlist_id):
        return _normalize_youtube_config(config)
    if subreddit and not feed_url:
        return _normalize_reddit_config(config)

    return _normalize_feed_config(config)


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
        raise ValueError("Scraper config already exists for this feed")

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
        raise ValueError("Scraper config already exists for this feed") from exc

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
        raise ValueError("Scraper config already exists for this feed") from exc

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
        feed_url = _normalize_feed_url(config.config)
        if not feed_url:
            logger.warning("Skipping config without feed_url. id=%s", config.id)
            continue
        limit = _extract_limit(config.config, default_limit)
        display_name = config.display_name
        config_name = config.config.get("name")
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
