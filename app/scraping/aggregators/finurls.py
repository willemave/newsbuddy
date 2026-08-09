"""FinURLs aggregator scraper."""

from __future__ import annotations

from app.scraping.aggregators._html_grouped import HtmlGroupedAggregatorScraper


class FinUrlsAggregatorScraper(HtmlGroupedAggregatorScraper):
    """Scrape finurls.com — flat list of finance/business news grouped by source."""

    KEY = "finurls"
    DISPLAY_NAME = "FinURLs"

    SOURCE_BLOCK_SELECTOR = ".publisher-block"
    SOURCE_HEADING_SELECTOR = ".publisher-header"
    ARTICLE_LINK_SELECTOR = ".publisher-link a.article-link"
