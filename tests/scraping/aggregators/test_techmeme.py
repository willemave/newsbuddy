from unittest.mock import MagicMock, patch

from app.scraping.aggregators.config import RssClusterAggregator
from app.scraping.aggregators.techmeme import TechmemeAggregatorScraper


def _settings() -> RssClusterAggregator:
    return RssClusterAggregator(
        key="techmeme",
        name="Techmeme",
        kind="rss_cluster",
        url="https://www.techmeme.com/feed.xml",
        limit=5,
        include_related=True,
        max_related=3,
    )


def test_cluster_link_requires_a_domain_boundary() -> None:
    scraper = TechmemeAggregatorScraper(_settings())

    assert scraper._is_cluster_link("https://www.techmeme.com/250921/p26") is True
    assert scraper._is_cluster_link("https://techmeme.com.evil.test/250921/p26") is False


def test_feed_http_outage_is_reported_in_run_stats() -> None:
    scraper = TechmemeAggregatorScraper(_settings())
    save_stats = {
        "saved": 0,
        "duplicates": 0,
        "errors": 0,
        "error_details": [],
        "processed_by_config_id": {},
    }

    with (
        patch(
            "app.scraping.aggregators._rss_cluster.fetch_and_parse_feed",
            side_effect=RuntimeError("Feed HTTP unavailable"),
        ),
        patch.object(scraper, "_save_items_with_stats", return_value=save_stats),
    ):
        stats = scraper.run_with_stats()

    assert stats.scraped == 0
    assert stats.saved == 0
    assert stats.errors == 1
    assert stats.error_details == ["https://www.techmeme.com/feed.xml: Feed HTTP unavailable"]


def test_malformed_entry_preserves_partial_progress_and_error_stats() -> None:
    scraper = TechmemeAggregatorScraper(_settings())
    malformed_entry = {
        "id": "broken-cluster",
        "link": "https://www.techmeme.com/broken-cluster",
    }
    valid_entry = {
        "title": "Surviving cluster",
        "link": "https://www.techmeme.com/250921/p26",
        "description": '<a href="https://example.com/surviving-story">Story</a>',
    }
    parsed_feed = MagicMock()
    parsed_feed.entries = [malformed_entry, valid_entry]
    parsed_feed.feed = {"title": "Techmeme"}
    parsed_feed.bozo = 0
    save_stats = {
        "saved": 1,
        "duplicates": 0,
        "errors": 0,
        "error_details": [],
        "processed_by_config_id": {},
    }

    with (
        patch(
            "app.scraping.aggregators._rss_cluster.fetch_and_parse_feed",
            return_value=parsed_feed,
        ),
        patch.object(
            scraper,
            "_process_entry",
            side_effect=[
                ValueError("malformed cluster"),
                scraper._process_entry(valid_entry, "Techmeme"),
            ],
        ),
        patch.object(scraper, "_save_items_with_stats", return_value=save_stats),
    ):
        stats = scraper.run_with_stats()

    assert (stats.scraped, stats.saved, stats.errors) == (1, 1, 1)
    assert stats.error_details == ["https://www.techmeme.com/broken-cluster: malformed cluster"]


def test_techmeme_scrape_returns_primary_and_related() -> None:
    description_html = """
    <p>
      <a href=\"https://example.com/article\"><img src=\"thumb.jpg\"></a>
      <a href=\"http://www.techmeme.com/250921/p26#a250921p26\"><img src=\"permalink.jpg\"></a>
      <a href=\"https://example.com/\">Example.com</a>:
      <span><b><a href=\"https://example.com/article\">Sample Techmeme headline</a></b></span>
      <ul>
        <li><a href=\"https://related.com/story\">Related story</a></li>
        <li><a href=\"https://another.com/piece\">Another angle</a></li>
      </ul>
    </p>
    """

    feed_entry = {
        "title": "Sample Techmeme headline",
        "link": "http://www.techmeme.com/250921/p26#a250921p26",
        "description": description_html,
        "published_parsed": (2025, 9, 21, 21, 10, 1, 0, 0, 0),
    }

    mock_feed = MagicMock()
    mock_feed.entries = [feed_entry]
    mock_feed.feed = {"title": "Techmeme"}
    mock_feed.bozo = 0

    with patch(
        "app.scraping.aggregators._rss_cluster.fetch_and_parse_feed", return_value=mock_feed
    ):
        items = TechmemeAggregatorScraper(_settings()).scrape()

    assert len(items) == 1
    item = items[0]
    assert item["url"] == "https://example.com/article"
    metadata = item["metadata"]
    assert metadata["platform"] == "techmeme"
    assert metadata["source"] == "example.com"
    assert metadata["aggregator"]["key"] == "techmeme"
    assert metadata["aggregator"]["name"] == "Techmeme"
    assert metadata["discussion_url"] == "https://www.techmeme.com/250921/p26#a250921p26"
    related = metadata["aggregator"]["metadata"]["related_links"]
    assert related[0]["url"] == "https://related.com/story"
    assert related[1]["url"] == "https://another.com/piece"


def test_techmeme_scrape_skips_clusters_without_external_link() -> None:
    feed_entry = {
        "title": "No external link",
        "link": "http://www.techmeme.com/cluster",
        "description": '<p><a href="http://www.techmeme.com/cluster">Permalink</a></p>',
        "published_parsed": (2025, 9, 21, 0, 0, 0, 0, 0, 0),
    }

    mock_feed = MagicMock()
    mock_feed.entries = [feed_entry]
    mock_feed.feed = {"title": "Techmeme"}
    mock_feed.bozo = 0

    with patch(
        "app.scraping.aggregators._rss_cluster.fetch_and_parse_feed", return_value=mock_feed
    ):
        items = TechmemeAggregatorScraper(_settings()).scrape()

    assert items == []
