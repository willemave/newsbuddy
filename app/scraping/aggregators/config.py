"""Configuration models for ``config/aggregators.yml``.

The YAML file lists every aggregator the platform knows how to scrape. Each
entry is a Pydantic model selected by the ``kind`` discriminator. The loader
returns instantiated scraper objects so ``ScraperRunner`` can iterate and run
them generically.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, Field, HttpUrl, TypeAdapter, ValidationError

from app.core.logging import get_logger

logger = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_AGGREGATORS_CONFIG_PATH = PROJECT_ROOT / "config" / "aggregators.yml"


class _AggregatorBase(BaseModel):
    """Common fields shared by every aggregator config entry."""

    key: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=128)
    enabled: bool = True


class HackerNewsAggregator(_AggregatorBase):
    """HackerNews configuration (uses Firebase API)."""

    kind: Literal["hackernews"]
    api_base_url: HttpUrl | str = Field(default="https://hacker-news.firebaseio.com/v0")
    site_base_url: HttpUrl | str = Field(default="https://news.ycombinator.com")
    limit: int = Field(default=30, ge=1, le=100)


class RssClusterAggregator(_AggregatorBase):
    """Techmeme-network RSS cluster aggregator (Techmeme/Mediagazer/Memeorandum)."""

    kind: Literal["rss_cluster"]
    url: HttpUrl | str
    limit: int = Field(default=25, ge=1, le=100)
    include_related: bool = True
    max_related: int = Field(default=6, ge=0, le=20)


class HtmlGroupedAggregator(_AggregatorBase):
    """SciURLs/FinURLs HTML aggregator with items grouped by source."""

    kind: Literal["html_grouped"]
    url: HttpUrl | str
    limit: int = Field(default=100, ge=1, le=500)


class HtmlTopicAggregator(_AggregatorBase):
    """Brutalist Report HTML aggregator with one feed per topic."""

    kind: Literal["html_topic"]
    base_url: str = Field(
        ...,
        description="URL template containing ``{topic}`` placeholder.",
    )
    topics: list[str] = Field(..., min_length=1)
    limit: int = Field(default=25, ge=1, le=200)
    hours: int = Field(default=24, ge=1, le=168)


AggregatorConfig = Annotated[
    HackerNewsAggregator | RssClusterAggregator | HtmlGroupedAggregator | HtmlTopicAggregator,
    Field(discriminator="kind"),
]


class AggregatorsFile(BaseModel):
    """Top-level shape of ``config/aggregators.yml``."""

    aggregators: list[AggregatorConfig] = Field(default_factory=list)


_AGGREGATORS_ADAPTER = TypeAdapter(AggregatorsFile)


def load_aggregators_config(
    config_path: str | Path = DEFAULT_AGGREGATORS_CONFIG_PATH,
) -> AggregatorsFile:
    """Load the aggregators YAML file. Returns an empty list on failure."""
    resolved_path = Path(config_path)
    if not resolved_path.is_absolute():
        resolved_path = PROJECT_ROOT / resolved_path

    if not resolved_path.exists():
        logger.warning("Aggregators config file not found at %s", resolved_path)
        return AggregatorsFile(aggregators=[])

    try:
        with open(resolved_path, encoding="utf-8") as fh:
            raw_config = yaml.safe_load(fh) or {}
        return _AGGREGATORS_ADAPTER.validate_python(raw_config)
    except ValidationError as exc:
        logger.error("Invalid aggregators config at %s: %s", resolved_path, exc)
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.error(
            "Error loading aggregators config at %s: %s",
            resolved_path,
            exc,
            exc_info=True,
        )

    return AggregatorsFile(aggregators=[])
