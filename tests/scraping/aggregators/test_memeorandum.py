from unittest.mock import MagicMock, patch

from app.scraping.aggregators.config import RssClusterAggregator
from app.scraping.aggregators.memeorandum import MemeorandumAggregatorScraper


def test_memeorandum_treats_memeorandum_links_as_cluster() -> None:
    description_html = """
    <p>
      <a href=\"https://politics.example.com/article\">Top political story</a>
      <a href=\"http://www.memeorandum.com/260101/p20#a260101p20\">Cluster permalink</a>
      <ul>
        <li><a href=\"https://reaction.com/take\">Reaction piece</a></li>
        <li>
          <a href=\"https://www.memeorandum.com/another-cluster\">
            Cluster sibling — should be skipped
          </a>
        </li>
      </ul>
    </p>
    """
    feed_entry = {
        "title": "Top political story",
        "link": "http://www.memeorandum.com/260101/p20",
        "description": description_html,
        "published_parsed": (2026, 1, 1, 12, 0, 0, 0, 0, 0),
    }
    mock_feed = MagicMock()
    mock_feed.entries = [feed_entry]
    mock_feed.feed = {"title": "Memeorandum"}
    mock_feed.bozo = 0

    settings = RssClusterAggregator(
        key="memeorandum",
        name="Memeorandum",
        kind="rss_cluster",
        url="https://www.memeorandum.com/feed.xml",
        limit=5,
        include_related=True,
        max_related=5,
    )
    with patch("app.scraping.aggregators._rss_cluster.feedparser.parse", return_value=mock_feed):
        items = MemeorandumAggregatorScraper(settings).scrape()

    assert len(items) == 1
    metadata = items[0]["metadata"]
    assert metadata["platform"] == "memeorandum"
    assert metadata["aggregator"]["key"] == "memeorandum"
    related_urls = {link["url"] for link in metadata["aggregator"]["metadata"]["related_links"]}
    # Sibling cluster link must be filtered out, only the external reaction stays.
    assert related_urls == {"https://reaction.com/take"}
