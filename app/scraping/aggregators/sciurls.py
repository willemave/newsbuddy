"""SciURLs aggregator scraper."""

from __future__ import annotations

from app.scraping.aggregators._html_grouped import HtmlGroupedAggregatorScraper


class SciUrlsAggregatorScraper(HtmlGroupedAggregatorScraper):
    """Scrape sciurls.com — flat list of science news grouped by source."""

    KEY = "sciurls"
    DISPLAY_NAME = "SciURLs"

    # SciURLs/FinURLs share the same template — each ``.box`` is a source block,
    # with ``.boxhead`` containing the source link and ``ul li a`` for stories.
    SOURCE_BLOCK_SELECTOR = ".box"
    SOURCE_HEADING_SELECTOR = ".boxhead"
    ARTICLE_LINK_SELECTOR = "ul li a"
