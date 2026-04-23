"""Build ``AggregatorScraper`` instances from ``config/aggregators.yml`` entries.

The registry maps each ``kind`` discriminator to a concrete subclass. We do not
auto-discover by introspection: explicit registration keeps wiring obvious and
prevents accidental enabling of partial/unfinished scrapers.
"""

from __future__ import annotations

from pathlib import Path

from app.core.logging import get_logger
from app.scraping.aggregators.base import AggregatorScraper
from app.scraping.aggregators.brutalist import BrutalistReportAggregatorScraper
from app.scraping.aggregators.config import (
    AggregatorConfig,
    HackerNewsAggregator,
    HtmlGroupedAggregator,
    HtmlTopicAggregator,
    RssClusterAggregator,
    load_aggregators_config,
)
from app.scraping.aggregators.finurls import FinUrlsAggregatorScraper
from app.scraping.aggregators.hackernews import HackerNewsAggregatorScraper
from app.scraping.aggregators.mediagazer import MediagazerAggregatorScraper
from app.scraping.aggregators.memeorandum import MemeorandumAggregatorScraper
from app.scraping.aggregators.sciurls import SciUrlsAggregatorScraper
from app.scraping.aggregators.techmeme import TechmemeAggregatorScraper

logger = get_logger(__name__)

#: Map ``(kind, key)`` to the scraper subclass. ``key`` keeps each
#: ``rss_cluster`` entry routed to its specific subclass (Techmeme vs.
#: Mediagazer vs. Memeorandum) so cluster-domain detection is correct.
_BY_KEY: dict[str, type[AggregatorScraper]] = {
    "hackernews": HackerNewsAggregatorScraper,
    "techmeme": TechmemeAggregatorScraper,
    "mediagazer": MediagazerAggregatorScraper,
    "memeorandum": MemeorandumAggregatorScraper,
    "sciurls": SciUrlsAggregatorScraper,
    "finurls": FinUrlsAggregatorScraper,
    "brutalist": BrutalistReportAggregatorScraper,
}


def build_scraper(entry: AggregatorConfig) -> AggregatorScraper | None:
    """Instantiate the scraper subclass matching one config entry."""
    cls = _BY_KEY.get(entry.key)
    if cls is None:
        logger.warning("No aggregator scraper registered for key=%s kind=%s", entry.key, entry.kind)
        return None

    if isinstance(entry, HackerNewsAggregator):
        return cls(entry)  # type: ignore[arg-type]
    if isinstance(entry, RssClusterAggregator):
        return cls(entry)  # type: ignore[arg-type]
    if isinstance(entry, HtmlGroupedAggregator):
        return cls(entry)  # type: ignore[arg-type]
    if isinstance(entry, HtmlTopicAggregator):
        return cls(entry)  # type: ignore[arg-type]
    return None


def load_aggregator_scrapers(
    config_path: str | Path | None = None,
) -> list[AggregatorScraper]:
    """Load and instantiate every enabled aggregator scraper from YAML."""
    file = load_aggregators_config(config_path) if config_path else load_aggregators_config()
    scrapers: list[AggregatorScraper] = []
    for entry in file.aggregators:
        if not entry.enabled:
            continue
        scraper = build_scraper(entry)
        if scraper is not None:
            scrapers.append(scraper)
    return scrapers


def known_aggregator_keys() -> set[str]:
    """Return the canonical set of supported aggregator keys."""
    return set(_BY_KEY.keys())
