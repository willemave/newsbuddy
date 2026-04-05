"""Shared schemas and normalization helpers for scraper configs."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.constants import DEFAULT_NEW_FEED_LIMIT
from app.services.feed_detection import FeedDetector

ALLOWED_SCRAPER_TYPES = {"substack", "atom", "podcast_rss", "youtube", "reddit"}
FEED_VALIDATOR = FeedDetector(use_llm=False, use_exa_search=False)


def _validate_limit(limit: object) -> None:
    """Validate an optional scraper limit value."""

    if limit is not None and (not isinstance(limit, int) or not 1 <= limit <= 100):
        raise ValueError("config.limit must be an integer between 1 and 100")


class CreateUserScraperConfig(BaseModel):
    """Payload for creating a scraper config."""

    scraper_type: Literal["substack", "atom", "podcast_rss", "youtube", "reddit"]
    display_name: str | None = Field(None, max_length=255)
    config: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True

    @model_validator(mode="after")
    def validate_config(self) -> CreateUserScraperConfig:
        if self.scraper_type == "youtube":
            self.config = normalize_youtube_config(self.config)
        elif self.scraper_type == "reddit":
            self.config = normalize_reddit_config(self.config)
        else:
            self.config = normalize_feed_config(self.config)
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
        self.config = normalize_update_config(self.config)
        return self


def normalize_feed_config(config: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a feed-based scraper config."""

    feed_url = config.get("feed_url")
    if not isinstance(feed_url, str) or not feed_url.strip():
        raise ValueError("config.feed_url is required")
    validated_feed = FEED_VALIDATOR.validate_feed_url(feed_url.strip())
    if not validated_feed:
        raise ValueError("config.feed_url must be a valid RSS/Atom feed URL")
    config["feed_url"] = validated_feed["feed_url"]
    _validate_limit(config.get("limit"))
    return config


def normalize_youtube_config(config: dict[str, Any]) -> dict[str, Any]:
    """Normalize a YouTube scraper config."""

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
    _validate_limit(config.get("limit"))
    return config


def normalize_reddit_config(config: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Reddit scraper config."""

    subreddit = (config.get("subreddit") or config.get("name") or "").strip()
    subreddit = subreddit.removeprefix("r/").strip("/")
    if not subreddit:
        raise ValueError("config.subreddit is required")

    config["subreddit"] = subreddit
    config["feed_url"] = f"https://www.reddit.com/r/{subreddit}/"
    _validate_limit(config.get("limit"))
    return config


def normalize_update_config(config: dict[str, Any]) -> dict[str, Any]:
    """Normalize an update payload based on the submitted config shape."""

    feed_url = config.get("feed_url")
    channel_id = config.get("channel_id")
    playlist_id = config.get("playlist_id")
    subreddit = config.get("subreddit") or config.get("name")

    if not feed_url and (channel_id or playlist_id):
        return normalize_youtube_config(config)
    if subreddit and not feed_url:
        return normalize_reddit_config(config)

    return normalize_feed_config(config)
