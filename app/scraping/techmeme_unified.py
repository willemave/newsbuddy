"""Backward-compatible shim for the Techmeme aggregator scraper.

The implementation now lives in
``app.scraping.aggregators.techmeme.TechmemeAggregatorScraper`` and is configured
via ``config/aggregators.yml``. Existing imports of
``TechmemeScraper``/``TechmemeFeedSettings``/``TechmemeSettings`` continue to
work for tests and any in-flight callers.
"""

from __future__ import annotations

from pathlib import Path

import feedparser  # re-exported so legacy tests can patch ``techmeme_unified.feedparser.parse``
from pydantic import BaseModel, Field, HttpUrl

from app.core.logging import get_logger
from app.scraping.aggregators.config import RssClusterAggregator
from app.scraping.aggregators.techmeme import TechmemeAggregatorScraper

__feedparser__ = feedparser  # silence "imported but unused" without changing the module API

logger = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "techmeme.yml"
TECHMEME_DOMAIN = "techmeme.com"


class TechmemeFeedSettings(BaseModel):
    """Legacy feed-level settings for the Techmeme scraper."""

    url: HttpUrl | str = Field(default="https://www.techmeme.com/feed.xml")
    limit: int = Field(default=20, ge=1, le=50)
    include_related: bool = Field(default=True)
    max_related: int = Field(default=6, ge=0, le=20)


class TechmemeSettings(BaseModel):
    """Legacy top-level Techmeme scraper configuration."""

    feed: TechmemeFeedSettings = Field(default_factory=TechmemeFeedSettings)


def load_techmeme_config(config_path: str | Path = DEFAULT_CONFIG_PATH) -> TechmemeSettings:
    """Load the legacy ``config/techmeme.yml`` file (defaults if missing)."""
    import yaml

    resolved_path = Path(config_path)
    if not resolved_path.is_absolute():
        resolved_path = PROJECT_ROOT / resolved_path

    if not resolved_path.exists():
        logger.warning("Techmeme legacy config file not found at %s; using defaults", resolved_path)
        return TechmemeSettings()

    try:
        with open(resolved_path, encoding="utf-8") as fh:
            raw_config = yaml.safe_load(fh) or {}
        return TechmemeSettings.model_validate(raw_config)
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("Error loading legacy Techmeme config: %s", exc, exc_info=True)
        return TechmemeSettings()


class TechmemeScraper(TechmemeAggregatorScraper):
    """Backward-compatible Techmeme scraper.

    Adapts the legacy ``TechmemeSettings`` shape (used by tests) into the
    new ``RssClusterAggregator`` config consumed by ``TechmemeAggregatorScraper``.
    """

    def __init__(self, config_path: str | Path = DEFAULT_CONFIG_PATH) -> None:
        legacy = load_techmeme_config(config_path)
        super().__init__(
            settings=RssClusterAggregator(
                key=self.KEY,
                name=self.DISPLAY_NAME,
                kind="rss_cluster",
                url=str(legacy.feed.url),
                limit=legacy.feed.limit,
                include_related=legacy.feed.include_related,
                max_related=legacy.feed.max_related,
            )
        )
        self.config_path = (
            Path(config_path)
            if Path(config_path).is_absolute()
            else PROJECT_ROOT / Path(config_path)
        )


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "TECHMEME_DOMAIN",
    "TechmemeFeedSettings",
    "TechmemeScraper",
    "TechmemeSettings",
    "load_techmeme_config",
]
