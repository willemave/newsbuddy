"""SciURLs aggregator scraper."""

from __future__ import annotations

from app.scraping.aggregators._html_grouped import HtmlGroupedAggregatorScraper


class SciUrlsAggregatorScraper(HtmlGroupedAggregatorScraper):
    """Scrape sciurls.com — flat list of science news grouped by source."""

    KEY = "sciurls"
    DISPLAY_NAME = "SciURLs"

    SOURCE_BLOCK_SELECTOR = ".publisher-block"
    SOURCE_HEADING_SELECTOR = ".publisher-header"
    ARTICLE_LINK_SELECTOR = ".publisher-link a.article-link"
