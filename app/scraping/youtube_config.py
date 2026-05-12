"""YouTube yt-dlp runtime configuration helpers."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Final

import yaml
from pydantic import BaseModel, Field, HttpUrl, ValidationError, ValidationInfo, field_validator

from app.core.logging import get_logger

PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[2]

logger = get_logger(__name__)


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
    """Runtime client/auth configuration for YouTube yt-dlp calls."""

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


def _resolve_config_path(config_path: str | Path) -> Path:
    provided = Path(config_path)
    if provided.is_absolute():
        return provided
    return PROJECT_ROOT / provided


def _load_config(config_path: Path) -> tuple[list[YouTubeChannelConfig], YouTubeClientConfig]:
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


def load_youtube_client_config(
    config_path: str | Path = "config/youtube.yml",
) -> YouTubeClientConfig:
    """Load client configuration for YouTube yt-dlp settings."""
    resolved = _resolve_config_path(config_path)
    _, client_config = _load_config(resolved)
    return client_config


def load_youtube_channels(
    config_path: str | Path = "config/youtube.yml",
) -> list[YouTubeChannelConfig]:
    """Load configured YouTube channel entries."""
    resolved = _resolve_config_path(config_path)
    channels, _ = _load_config(resolved)
    return channels
