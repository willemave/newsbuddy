from unittest.mock import MagicMock, patch

from app.scraping.aggregators.config import RssClusterAggregator
from app.scraping.aggregators.mediagazer import MediagazerAggregatorScraper


def test_mediagazer_uses_its_own_cluster_domain() -> None:
    description_html = """
    <p>
      <a href=\"https://media.example.com/story\">Big media story</a>
      <a href=\"http://www.mediagazer.com/260101/p10#a260101p10\">Permalink</a>
      <a href=\"https://media.example.com/\">Media Example</a>
      <ul>
        <li><a href=\"https://other-news.com/related\">Related coverage</a></li>
      </ul>
    </p>
    """
    feed_entry = {
        "title": "Big media story",
        "link": "http://www.mediagazer.com/260101/p10",
        "description": description_html,
        "published_parsed": (2026, 1, 1, 12, 0, 0, 0, 0, 0),
    }
    mock_feed = MagicMock()
    mock_feed.entries = [feed_entry]
    mock_feed.feed = {"title": "Mediagazer"}
    mock_feed.bozo = 0

    settings = RssClusterAggregator(
        key="mediagazer",
        name="Mediagazer",
        kind="rss_cluster",
        url="https://www.mediagazer.com/feed.xml",
        limit=5,
        include_related=True,
        max_related=2,
    )
    with patch("app.scraping.aggregators._rss_cluster.feedparser.parse", return_value=mock_feed):
        items = MediagazerAggregatorScraper(settings).scrape()

    assert len(items) == 1
    item = items[0]
    metadata = item["metadata"]
    assert metadata["platform"] == "mediagazer"
    assert metadata["aggregator"]["key"] == "mediagazer"
    assert metadata["aggregator"]["name"] == "Mediagazer"
    assert metadata["discussion_url"].startswith("https://www.mediagazer.com")
    related = metadata["aggregator"]["metadata"]["related_links"]
    assert {link["url"] for link in related} == {"https://other-news.com/related"}
