"""Mediagazer RSS-cluster aggregator scraper."""

from __future__ import annotations

from app.scraping.aggregators._rss_cluster import RssClusterAggregatorScraper


class MediagazerAggregatorScraper(RssClusterAggregatorScraper):
    """Scrape Mediagazer.com RSS clusters (Techmeme network media-news site)."""

    KEY = "mediagazer"
    DISPLAY_NAME = "Mediagazer"
    CLUSTER_DOMAIN = "mediagazer.com"
