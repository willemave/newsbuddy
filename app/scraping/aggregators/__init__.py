"""News aggregator scrapers (HN, Techmeme, Mediagazer, Memeorandum, etc).

Each aggregator gets a dedicated subclass under ``app.scraping.aggregators``.
``ScraperRunner`` builds them from ``config/aggregators.yml`` via
``load_aggregator_scrapers``.
"""

from app.scraping.aggregators.base import AggregatorScraper
from app.scraping.aggregators.brutalist import BrutalistReportAggregatorScraper
from app.scraping.aggregators.config import (
    AggregatorConfig,
    AggregatorsFile,
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
from app.scraping.aggregators.registry import (
    build_scraper,
    known_aggregator_keys,
    load_aggregator_scrapers,
)
from app.scraping.aggregators.sciurls import SciUrlsAggregatorScraper
from app.scraping.aggregators.techmeme import TechmemeAggregatorScraper

__all__ = [
    "AggregatorConfig",
    "AggregatorScraper",
    "AggregatorsFile",
    "BrutalistReportAggregatorScraper",
    "FinUrlsAggregatorScraper",
    "HackerNewsAggregator",
    "HackerNewsAggregatorScraper",
    "HtmlGroupedAggregator",
    "HtmlTopicAggregator",
    "MediagazerAggregatorScraper",
    "MemeorandumAggregatorScraper",
    "RssClusterAggregator",
    "SciUrlsAggregatorScraper",
    "TechmemeAggregatorScraper",
    "build_scraper",
    "known_aggregator_keys",
    "load_aggregator_scrapers",
    "load_aggregators_config",
]
