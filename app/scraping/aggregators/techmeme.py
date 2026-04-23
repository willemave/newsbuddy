"""Techmeme RSS-cluster aggregator scraper."""

from __future__ import annotations

from app.scraping.aggregators._rss_cluster import RssClusterAggregatorScraper


class TechmemeAggregatorScraper(RssClusterAggregatorScraper):
    """Scrape Techmeme.com RSS clusters."""

    KEY = "techmeme"
    DISPLAY_NAME = "Techmeme"
    CLUSTER_DOMAIN = "techmeme.com"
