"""Unified YouTube channel scraper aligned with podcast ingestion flow."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar, Final

import yaml
from pydantic import BaseModel, Field, HttpUrl, ValidationError, ValidationInfo, field_validator

from app.core.logging import get_logger
from app.models.contracts import ContentType
from app.scraping.base import BaseScraper

try:  # pragma: no cover - import guard for optional dependency in tests
    import yt_dlp
except ImportError:  # pragma: no cover
    yt_dlp = None

PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[2]

logger = get_logger(__name__)


class _YtDlpLogger:
    def __init__(self, base_logger):
        self._logger = base_logger

    def debug(self, msg: str) -> None:
        self._logger.debug(msg)

    def warning(self, msg: str) -> None:
        self._logger.warning(msg)

    def error(self, msg: str) -> None:
        self._logger.warning(msg)


class YouTubeChannelConfig(BaseModel):
    """Configuration for a single YouTube channel or playlist."""

    name: str = Field(..., min_length=1, max_length=200)
    url: HttpUrl | str | None = None
    channel_id: str | None = Field(None, min_length=5)
    playlist_id: str | None = Field(None, min_length=5)
    limit: int = Field(default=10, ge=1, le=50)
    max_age_days: int | None = Field(default=30, ge=0, le=365)
    language: str | None = Field(default=None, min_length=2, max_length=8)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: HttpUrl | str | None) -> str | None:
        if value is None:
            return None
        return str(value)

    @field_validator("playlist_id")
    @classmethod
    def normalize_playlist(cls, value: str | None) -> str | None:
        if value:
            return value.strip()
        return value

    @field_validator("channel_id")
    @classmethod
    def normalize_channel(cls, value: str | None) -> str | None:
        if value:
            return value.strip()
        return value

    @field_validator("url")
    @classmethod
    def ensure_path_components(cls, value: str | None, info: ValidationInfo) -> str | None:
        if not value and not info.data.get("channel_id") and not info.data.get("playlist_id"):
            raise ValueError("Provide either url, channel_id, or playlist_id")
        return value

    @property
    def target_url(self) -> str:
        """Resolve to a concrete URL that yt-dlp understands."""

        if self.playlist_id:
            return f"https://www.youtube.com/playlist?list={self.playlist_id}"
        if self.channel_id:
            return f"https://www.youtube.com/channel/{self.channel_id}"
        return str(self.url)


class YouTubeClientConfig(BaseModel):
    """Runtime client/auth configuration for the YouTube scraper."""

    cookies_path: str | Path | None = None
    po_token_provider: str | None = None
    po_token_base_url: HttpUrl | str | None = Field(default="http://127.0.0.1:4416")
    throttle_seconds: float = Field(default=6.0, ge=0.0, le=60.0)
    player_client: str = Field(default="mweb", min_length=2, max_length=32)

    SUPPORTED_PROVIDERS: ClassVar[set[str]] = {"bgutilhttp", "webpoclient"}

    @field_validator("cookies_path")
    @classmethod
    def normalize_cookies_path(cls, value: str | Path | None) -> str | None:
        if value in (None, ""):
            return None
        return str(value)

    @field_validator("po_token_provider")
    @classmethod
    def normalize_provider(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        normalized = value.strip().lower()
        if normalized in {"none", "null"}:
            return None
        if normalized not in cls.SUPPORTED_PROVIDERS:
            supported = sorted(cls.SUPPORTED_PROVIDERS)
            raise ValueError(f"Unsupported po_token_provider '{value}'. Use: {supported}")
        return normalized

    @field_validator("po_token_base_url")
    @classmethod
    def normalize_base_url(cls, value: HttpUrl | str | None) -> str | None:
        if value in (None, ""):
            return None
        return str(value)

    @field_validator("player_client")
    @classmethod
    def normalize_player_client(cls, value: str) -> str:
        return value.strip()

    def resolved_cookies_path(self) -> Path | None:
        if self.cookies_path is None:
            return None
        candidate = Path(self.cookies_path).expanduser()
        if not candidate.is_absolute():
            candidate = PROJECT_ROOT / candidate
        return candidate


class YouTubeUnifiedScraper(BaseScraper):
    """Scraper that ingests recent videos from configured YouTube channels."""

    _LISTING_OPTS: Final[dict[str, Any]] = {
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "socket_timeout": 15,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.6613.120 Safari/537.36"
            )
        },
    }

    _VIDEO_OPTS: Final[dict[str, Any]] = {
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "skip_download": True,
        "writesubtitles": False,
        "writeautomaticsub": False,
        "socket_timeout": 15,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.6613.120 Safari/537.36"
            )
        },
    }

    def __init__(
        self,
        config_path: str | Path = "config/youtube.yml",
        channels: list[YouTubeChannelConfig] | None = None,
        client_config: YouTubeClientConfig | None = None,
    ):
        super().__init__("YouTube")
        self.config_path = self._resolve_config_path(config_path)

        discovered_channels, discovered_client = self._load_config(self.config_path)

        self.channels = channels or discovered_channels
        self.client_config = client_config or discovered_client
        self._cookiefile: str | None

        cookies_path = self.client_config.resolved_cookies_path()
        if cookies_path and cookies_path.exists():
            self._cookiefile = str(cookies_path)
        else:
            if cookies_path and not cookies_path.exists():
                logger.warning("YouTube cookies not found at %s", cookies_path)
            self._cookiefile = None

        self._throttle_seconds = self.client_config.throttle_seconds

    def scrape(self) -> list[dict[str, Any]]:
        if not self.channels:
            logger.warning("No YouTube channels configured")
            return []

        results: list[dict[str, Any]] = []

        for channel in self.channels:
            channel_results = self._scrape_channel(channel)

            if channel_results is None:
                logger.warning("Channel %s returned None; skipping", channel.name)
                continue

            if not isinstance(channel_results, list):
                logger.error(
                    "Channel %s returned unexpected result type %s; skipping",
                    channel.name,
                    type(channel_results).__name__,
                )
                continue

            results.extend(channel_results)

        logger.info(
            "YouTube scraper gathered %s items across %s channels",
            len(results),
            len(self.channels),
        )
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_listing_opts(self, playlist_end: int) -> dict[str, Any]:
        opts = self._build_ytdlp_options(self._LISTING_OPTS)
        opts["playlistend"] = playlist_end
        return opts

    def _build_video_opts(self) -> dict[str, Any]:
        return self._build_ytdlp_options(self._VIDEO_OPTS)

    def _build_ytdlp_options(self, base_opts: dict[str, Any]) -> dict[str, Any]:
        opts = {**base_opts}
        if "http_headers" in base_opts:
            opts["http_headers"] = dict(base_opts["http_headers"])
        opts["logger"] = _YtDlpLogger(logger)

        extractor_args = self._build_extractor_args()
        if extractor_args:
            opts["extractor_args"] = extractor_args

        if self._cookiefile:
            opts["cookiefile"] = self._cookiefile

        return opts

    def _build_extractor_args(self) -> dict[str, dict[str, list[str]]]:
        extractor_args: dict[str, dict[str, list[str]]] = {
            "youtube": {
                "player_client": [self.client_config.player_client],
                "player_skip": ["configs"],
            }
        }

        provider = self.client_config.po_token_provider
        if provider:
            provider_key = f"youtubepot-{provider}"
            provider_args: dict[str, list[str]] = {}
            if self.client_config.po_token_base_url:
                provider_args["base_url"] = [str(self.client_config.po_token_base_url)]
            extractor_args[provider_key] = provider_args

        return extractor_args

    def _throttle_if_needed(self, iteration: int) -> None:
        if iteration == 0:
            return
        if self._throttle_seconds > 0:
            time.sleep(self._throttle_seconds)

    def _scrape_channel(self, channel: YouTubeChannelConfig) -> list[dict[str, Any]]:
        logger.info("Scraping YouTube channel %s", channel.name)

        try:
            entries = self._extract_channel_entries(channel)
        except Exception as exc:  # pragma: no cover - safety net
            logger.exception(
                "Error listing channel %s: %s",
                channel.name,
                exc,
                extra={
                    "component": "youtube_scraper",
                    "operation": "channel_listing",
                    "context_data": {"channel": channel.name, "target_url": channel.target_url},
                },
            )
            return []

        if not entries:
            logger.info("No entries returned for channel %s", channel.name)
            return []

        items: list[dict[str, Any]] = []
        seen_urls: set[str] = set()

        for idx, entry in enumerate(entries):
            if len(items) >= channel.limit:
                break

            video_url = self._resolve_video_url(entry)
            if not video_url or video_url in seen_urls:
                continue

            video_info = entry if self._entry_has_core_details(entry) else None

            if video_info is None:
                try:
                    self._throttle_if_needed(idx)
                    video_info = self._extract_video_info(video_url)
                except Exception as exc:  # pragma: no cover - yt-dlp errors hard to replicate
                    logger.exception(
                        "Error extracting video metadata for %s: %s",
                        video_url,
                        exc,
                        extra={
                            "component": "youtube_scraper",
                            "operation": "video_metadata",
                            "context_data": {"channel": channel.name, "video_url": video_url},
                        },
                    )
                    continue
                if video_info is None:
                    continue

            if not self._passes_filters(video_info, channel.max_age_days):
                continue

            item = self._build_scraped_item(channel, video_url, video_info)
            if item:
                items.append(item)
                seen_urls.add(video_url)

        logger.info(
            "Prepared %s videos for channel %s (limit %s)",
            len(items),
            channel.name,
            channel.limit,
        )
        return items

    def _extract_channel_entries(self, channel: YouTubeChannelConfig) -> list[dict[str, Any]]:
        logger.debug("Fetching channel listing for %s", channel.target_url)
        if yt_dlp is None:  # pragma: no cover - runtime safeguard when dependency missing
            raise RuntimeError("yt-dlp is required to run the YouTube scraper")
        listing_opts = self._build_listing_opts(channel.limit)
        try:
            with yt_dlp.YoutubeDL(listing_opts) as ydl:
                info = ydl.extract_info(channel.target_url, download=False)
        except yt_dlp.utils.DownloadError as exc:  # pragma: no cover - network/error handling
            error_str = str(exc)
            if _should_skip_download_error(error_str):
                logger.warning("Skipping channel listing for %s: %s", channel.name, error_str)
                return []
            raise

        entries = info.get("entries", []) if isinstance(info, dict) else []
        flattened: list[dict[str, Any]] = []

        for entry in entries:
            if isinstance(entry, dict):
                flattened.append(entry)
            elif hasattr(entry, "__dict__"):
                flattened.append(dict(entry.__dict__))

        return flattened

    def _extract_video_info(self, video_url: str) -> dict[str, Any] | None:
        if yt_dlp is None:  # pragma: no cover - runtime safeguard when dependency missing
            raise RuntimeError("yt-dlp is required to run the YouTube scraper")
        try:
            with yt_dlp.YoutubeDL(self._build_video_opts()) as ydl:
                return ydl.extract_info(video_url, download=False)
        except yt_dlp.utils.DownloadError as exc:  # pragma: no cover - network/error handling
            error_str = str(exc)
            if _should_skip_download_error(error_str):
                logger.warning("Skipping video %s: %s", video_url, error_str)
                return None
            raise

    def _passes_filters(self, video_info: dict[str, Any], max_age_days: int | None) -> bool:
        if max_age_days is None or max_age_days == 0:
            return True

        publication_date = self._extract_publication_datetime(video_info)
        if not publication_date:
            return True

        cutoff = datetime.now(tz=self._utc()) - timedelta(days=float(max_age_days))
        return publication_date >= cutoff

    def _build_scraped_item(
        self,
        channel: YouTubeChannelConfig,
        video_url: str,
        video_info: dict[str, Any],
    ) -> dict[str, Any] | None:
        title = video_info.get("title") or channel.name
        publication_date = self._extract_publication_datetime(video_info)

        metadata = {
            "platform": "youtube",
            "source": f"youtube:{channel.name}",
            "channel_name": channel.name,
            "channel_id": video_info.get("channel_id"),
            "channel_url": channel.target_url,
            "video_id": video_info.get("id"),
            "video_url": video_url,
            "audio_url": video_url,
            "thumbnail_url": video_info.get("thumbnail"),
            "duration_seconds": video_info.get("duration"),
            "view_count": video_info.get("view_count"),
            "like_count": video_info.get("like_count"),
            "language": channel.language,
            "youtube_video": True,
            "has_transcript": bool(
                video_info.get("subtitles") or video_info.get("automatic_captions")
            ),
            "publication_date": publication_date.isoformat() if publication_date else None,
        }

        description = video_info.get("description")
        if description:
            metadata["description"] = description[:2000]

        return {
            "url": self._normalize_url(video_url),
            "title": title,
            "content_type": ContentType.PODCAST,
            "metadata": metadata,
        }

    @staticmethod
    def _resolve_video_url(entry: dict[str, Any]) -> str | None:
        webpage_url = entry.get("webpage_url")
        if isinstance(webpage_url, str) and webpage_url:
            return webpage_url
        raw_url = entry.get("url")
        if isinstance(raw_url, str) and raw_url.startswith("http"):
            return raw_url
        entry_id = entry.get("id")
        if isinstance(entry_id, str) and entry_id:
            return f"https://www.youtube.com/watch?v={entry_id}"
        return None

    @staticmethod
    def _entry_has_core_details(entry: dict[str, Any]) -> bool:
        return any(
            key in entry and entry[key] is not None
            for key in ("duration", "timestamp", "upload_date")
        )

    @staticmethod
    def _extract_publication_datetime(video_info: dict[str, Any]) -> datetime | None:
        utc = YouTubeUnifiedScraper._utc()

        if "upload_date" in video_info and video_info["upload_date"]:
            try:
                return datetime.strptime(video_info["upload_date"], "%Y%m%d").replace(tzinfo=utc)
            except ValueError:
                pass

        if "timestamp" in video_info and video_info["timestamp"]:
            try:
                return datetime.fromtimestamp(int(video_info["timestamp"]), tz=utc)
            except (ValueError, TypeError, OverflowError):
                return None

        if "release_timestamp" in video_info and video_info["release_timestamp"]:
            try:
                return datetime.fromtimestamp(int(video_info["release_timestamp"]), tz=utc)
            except (ValueError, TypeError, OverflowError):
                return None

        return None

    @staticmethod
    def _resolve_config_path(config_path: str | Path) -> Path:
        provided = Path(config_path)
        if provided.is_absolute():
            return provided
        return PROJECT_ROOT / provided

    @classmethod
    def _load_config(
        cls, config_path: Path
    ) -> tuple[list[YouTubeChannelConfig], YouTubeClientConfig]:
        if not config_path.exists():
            logger.warning("YouTube config file not found at %s", config_path)
            return [], YouTubeClientConfig()

        try:
            with open(config_path, encoding="utf-8") as fh:
                raw_config = yaml.safe_load(fh) or {}
        except Exception as exc:
            logger.error("Failed to read YouTube config %s: %s", config_path, exc)
            return [], YouTubeClientConfig()

        client_section = raw_config.get("client") or {}
        try:
            client_config = YouTubeClientConfig.model_validate(client_section)
        except ValidationError as exc:
            logger.error("Invalid YouTube client config: %s", exc)
            client_config = YouTubeClientConfig()

        channel_entries = raw_config.get("channels", [])
        channels: list[YouTubeChannelConfig] = []

        for entry in channel_entries:
            try:
                channel = YouTubeChannelConfig.model_validate(entry)
                channels.append(channel)
            except ValidationError as exc:
                logger.error("Invalid YouTube channel config %s: %s", entry, exc)

        return channels, client_config

    @classmethod
    def _load_channels(cls, config_path: Path) -> list[YouTubeChannelConfig]:
        channels, _ = cls._load_config(config_path)
        return channels

    @staticmethod
    def _utc():
        try:  # pragma: no branch - executed on import
            from datetime import UTC as stdlib_utc

            return stdlib_utc
        except ImportError:  # pragma: no cover - python <3.11
            return stdlib_utc


def load_youtube_client_config(
    config_path: str | Path = "config/youtube.yml",
) -> YouTubeClientConfig:
    """Public helper to load client configuration for yt-dlp settings."""
    resolved = YouTubeUnifiedScraper._resolve_config_path(config_path)
    _, client_config = YouTubeUnifiedScraper._load_config(resolved)
    return client_config


def _should_skip_download_error(error_message: str) -> bool:
    lowered = error_message.lower()
    if "sign in to confirm" in lowered:
        return True
    if "requires authentication" in lowered:
        return True
    return "premieres in" in lowered


def load_youtube_channels(
    config_path: str | Path = "config/youtube.yml",
) -> list[YouTubeChannelConfig]:
    """Public helper to load channel configuration for testing or scripts."""

    resolved = YouTubeUnifiedScraper._resolve_config_path(config_path)
    return YouTubeUnifiedScraper._load_channels(resolved)
