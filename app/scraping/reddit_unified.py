import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import praw
import prawcore
import yaml

from app.core.db import get_db
from app.core.logging import get_logger
from app.core.settings import get_settings
from app.models.metadata import ContentType
from app.scraping.base import BaseScraper
from app.services.scraper_configs import list_active_configs_by_type
from app.utils.error_logger import log_scraper_event
from app.utils.paths import resolve_config_directory, resolve_config_path

logger = get_logger(__name__)
settings = get_settings()
REDDIT_USER_AGENT = settings.reddit_user_agent or "news_app.scraper/1.0 (by u/anonymous)"
_MISSING_CONFIG_WARNINGS: set[str] = set()


@dataclass(frozen=True)
class RedditTarget:
    """One subreddit scrape target with audience ownership."""

    subreddit: str
    limit: int
    visibility_scope: str
    owner_user_id: int | None = None
    user_scraper_config_id: int | None = None


def _resolve_reddit_config_path(config_path: str | Path | None) -> Path:
    if config_path is None:
        return resolve_config_path("REDDIT_CONFIG_PATH", "reddit.yml")

    candidate = Path(config_path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve(strict=False)

    base_dir = resolve_config_directory()
    return (base_dir / candidate).resolve(strict=False)


def _emit_missing_config_warning(resolved_path: Path) -> None:
    key = str(resolved_path.resolve(strict=False))
    if key in _MISSING_CONFIG_WARNINGS:
        return
    _MISSING_CONFIG_WARNINGS.add(key)
    log_scraper_event(
        service="Reddit",
        event="config_missing",
        level=logging.WARNING,
        metric="scrape_config_missing",
        path=str(resolved_path.resolve(strict=False)),
    )


class RedditUnifiedScraper(BaseScraper):
    """Unified scraper for Reddit using the new architecture."""

    def __init__(self, config_path: str | Path | None = None):
        super().__init__("Reddit")
        self.config_path = _resolve_reddit_config_path(config_path)
        self.targets = self._load_subreddit_config()
        self._reddit_client: praw.Reddit | None = None

    def _load_subreddit_config(self) -> list[RedditTarget]:
        """Load subreddit configuration from user scraper configs only."""
        file_targets = self._load_subreddits_from_file()
        db_targets = self._load_subreddits_from_db()
        merged = db_targets
        logger.info(
            "Loaded %s Reddit subreddits (db=%s, file_ignored=%s)",
            len(merged),
            len(db_targets),
            len(file_targets),
        )
        return merged

    def _load_subreddits_from_db(self) -> list[RedditTarget]:
        """Load subreddit configuration from user scraper configs."""
        targets: list[RedditTarget] = []
        with get_db() as db:
            configs = list_active_configs_by_type(db, "reddit")
            for config in configs:
                payload = config.config or {}
                name = payload.get("subreddit")
                limit = payload.get("limit", 10)
                if not isinstance(name, str) or not name.strip():
                    continue
                cleaned = name.strip().lstrip("r/").strip("/")
                if cleaned.lower() == "front":
                    logger.info("Skipping 'front' subreddit; front page scraping disabled")
                    continue
                if not isinstance(limit, int) or limit <= 0:
                    logger.warning("Invalid limit for subreddit %s: %s", cleaned, limit)
                    limit = 10
                targets.append(
                    RedditTarget(
                        subreddit=cleaned,
                        limit=limit,
                        visibility_scope="user",
                        owner_user_id=config.user_id,
                        user_scraper_config_id=config.id,
                    )
                )

        return targets

    def _load_subreddits_from_file(self) -> list[RedditTarget]:
        """Load subreddit configuration from YAML file for observability only."""
        config_path = self.config_path
        if not config_path.exists():
            _emit_missing_config_warning(config_path)
            return []

        try:
            with open(config_path, encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}

            configured = config.get("subreddits", [])
            configured_count = len(configured) if isinstance(configured, list) else 0
            if configured_count > 0:
                logger.info(
                    "Ignoring %s file-configured Reddit targets; "
                    "only user-scoped Reddit scraping is enabled",
                    configured_count,
                )
            return []

        except Exception as e:
            log_scraper_event(
                service="Reddit",
                event="config_load_failed",
                level=logging.ERROR,
                path=str(config_path),
                error=str(e),
            )
            return []

    def scrape(self) -> list[dict[str, Any]]:
        """Scrape Reddit posts from multiple subreddits."""
        all_items = []

        client = self._get_reddit_client()
        if client is None:
            return []

        for target in self.targets:
            try:
                items = self._scrape_subreddit(client, target)
                all_items.extend(items)
                logger.info("Scraped %s items from r/%s", len(items), target.subreddit)
            except prawcore.PrawcoreException as error:
                logger.exception(
                    "Error scraping r/%s: %s",
                    target.subreddit,
                    error,
                    extra={
                        "component": "reddit_scraper",
                        "operation": "scrape_subreddit",
                        "context_data": {"subreddit": target.subreddit},
                    },
                )
                continue
            except Exception as error:  # pragma: no cover - defensive
                logger.exception(
                    "Unexpected error scraping r/%s: %s",
                    target.subreddit,
                    error,
                    extra={
                        "component": "reddit_scraper",
                        "operation": "scrape_subreddit",
                        "context_data": {"subreddit": target.subreddit},
                    },
                )
                continue

        logger.info(f"Total Reddit items scraped: {len(all_items)}")
        return all_items

    def _scrape_subreddit(
        self,
        client: praw.Reddit,
        target: RedditTarget,
    ) -> list[dict[str, Any]]:
        """Scrape a specific subreddit."""
        items = []
        subreddit_name = target.subreddit
        limit = target.limit

        try:
            subreddit = client.subreddit("popular" if subreddit_name == "front" else subreddit_name)

            for submission in subreddit.new(limit=min(limit, 100)):
                # Skip self posts and posts without external URLs
                if submission.is_self and subreddit_name != "front":
                    continue

                if not self._is_external_url(
                    submission.url, allow_reddit_media=subreddit_name == "front"
                ):
                    continue

                # Skip deleted/removed posts
                if submission.removed_by_category or not submission.title:
                    continue

                normalized_url = self._normalize_url(submission.url)
                discussion_url = f"https://www.reddit.com{submission.permalink}"

                try:
                    source_domain = urlparse(normalized_url).netloc or None
                except Exception:
                    source_domain = None

                timestamp = datetime.now(UTC).isoformat()

                item = {
                    "url": normalized_url,
                    "title": submission.title,
                    "content_type": ContentType.NEWS,
                    "is_aggregate": False,
                    "owner_user_id": target.owner_user_id,
                    "visibility_scope": target.visibility_scope,
                    "user_scraper_config_id": target.user_scraper_config_id,
                    "metadata": {
                        "platform": "reddit",  # Scraper identifier
                        "source": submission.subreddit.display_name,
                        "source_type": (
                            "user_reddit" if target.visibility_scope == "user" else "reddit"
                        ),
                        "source_label": submission.subreddit.display_name,
                        "article": {
                            "url": normalized_url,
                            "title": submission.title,
                            "source_domain": source_domain,
                        },
                        "aggregator": {
                            "name": "Reddit",
                            "title": submission.title,
                            "external_id": submission.id,
                            "author": getattr(submission, "author", None)
                            and submission.author.name,
                            "metadata": {
                                "score": submission.score,
                                "comments_count": submission.num_comments,
                                "upvote_ratio": submission.upvote_ratio,
                                "subreddit": submission.subreddit.display_name,
                                "over_18": submission.over_18,
                            },
                        },
                        "items": [
                            {
                                "title": submission.title,
                                "url": normalized_url,
                                "summary": None,
                                "source": submission.domain,
                                "author": getattr(submission, "author", None)
                                and submission.author.name,
                                "score": submission.score,
                                "comments_url": discussion_url,
                                "metadata": {
                                    "score": submission.score,
                                    "comments_count": submission.num_comments,
                                    "upvote_ratio": submission.upvote_ratio,
                                    "reddit_id": submission.id,
                                },
                            }
                        ],
                        "discussion_url": discussion_url,
                        "excerpt": submission.selftext or None,
                        "discovery_time": timestamp,
                        "scraped_at": timestamp,
                    },
                }

                items.append(item)

                if len(items) >= limit:
                    break

        except prawcore.PrawcoreException as error:
            logger.exception(
                "Error fetching from r/%s: %s",
                subreddit_name,
                error,
                extra={
                    "component": "reddit_scraper",
                    "operation": "fetch_subreddit",
                    "context_data": {"subreddit": subreddit_name},
                },
            )

        return items

    def _is_external_url(self, url: str, allow_reddit_media: bool = False) -> bool:
        """Check if URL is external (not a Reddit self-post or internal link)."""
        if not url:
            return False

        media_domains = {"i.redd.it", "v.redd.it", "preview.redd.it"}
        reddit_domains = {"reddit.com", "www.reddit.com", "old.reddit.com", "redd.it"}

        try:
            from urllib.parse import urlparse

            parsed = urlparse(url)
            netloc = (parsed.netloc or "").lower()
            if parsed.scheme not in {"http", "https"} or not netloc:
                return False

            if allow_reddit_media:
                if netloc in media_domains or netloc == "redd.it":
                    return True
                if netloc in reddit_domains and "/gallery/" in parsed.path.lower():
                    return True

            return netloc not in reddit_domains
        except Exception:
            return False

    def _get_reddit_client(self) -> praw.Reddit | None:
        if self._reddit_client:
            return self._reddit_client

        client_id = settings.reddit_client_id
        client_secret = settings.reddit_client_secret
        if not client_id or not client_secret:
            logger.warning("Reddit credentials not configured; skipping Reddit scraper")
            return None

        requestor_kwargs: dict[str, Any] = {}
        proxies: dict[str, str] = {}
        for scheme in ("http", "https"):
            env_value = os.getenv(f"{scheme.upper()}_PROXY") or os.getenv(f"{scheme}_proxy")
            if env_value:
                proxies[scheme] = env_value
        if proxies:
            requestor_kwargs["proxies"] = proxies

        reddit_kwargs: dict[str, Any] = {
            "client_id": client_id,
            "client_secret": client_secret,
            "user_agent": REDDIT_USER_AGENT,
            "check_for_updates": False,
            "timeout": 30,
        }

        if requestor_kwargs:
            reddit_kwargs["requestor_kwargs"] = requestor_kwargs

        if not settings.reddit_read_only:
            if settings.reddit_username and settings.reddit_password:
                reddit_kwargs.update(
                    username=settings.reddit_username,
                    password=settings.reddit_password,
                )
            else:
                logger.warning("REDDIT_READ_ONLY=false but credentials missing; using read-only")

        reddit = praw.Reddit(**reddit_kwargs)
        reddit.read_only = settings.reddit_read_only or not (
            settings.reddit_username and settings.reddit_password
        )

        self._reddit_client = reddit
        return reddit
