"""Memeorandum RSS-cluster aggregator scraper."""

from __future__ import annotations

from app.scraping.aggregators._rss_cluster import RssClusterAggregatorScraper


class MemeorandumAggregatorScraper(RssClusterAggregatorScraper):
    """Scrape Memeorandum.com RSS clusters (Techmeme network politics-news site)."""

    KEY = "memeorandum"
    DISPLAY_NAME = "Memeorandum"
    CLUSTER_DOMAIN = "memeorandum.com"
