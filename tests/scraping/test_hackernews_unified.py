from unittest.mock import MagicMock, patch

from app.models.contracts import ContentType
from app.scraping.hackernews_unified import HackerNewsUnifiedScraper


@patch("app.scraping.hackernews_unified.httpx.Client")
def test_hackernews_scraper_creates_article(mock_httpx_client) -> None:
    """HackerNews scraper should return article content items, not aggregates."""

    # Mock HTTP client context manager
    mock_client_instance = MagicMock()
    mock_httpx_client.return_value.__enter__.return_value = mock_client_instance
    mock_httpx_client.return_value.__exit__.return_value = None

    # Mock responses for top stories and individual story fetch
    top_response = MagicMock()
    top_response.json.return_value = [123456]

    story_response = MagicMock()
    story_response.json.return_value = {
        "id": 123456,
        "type": "story",
        "title": "Sample HN Story",
        "url": "http://example.com/post",
        "by": "author",
        "score": 512,
        "descendants": 42,
        "time": 1_696_000_000,
        "text": "Optional summary",
    }

    mock_client_instance.get.side_effect = [top_response, story_response]

    scraper = HackerNewsUnifiedScraper()
    items = scraper.scrape()

    assert len(items) == 1
    item = items[0]

    # Ensure article pipeline will process the item
    assert item["content_type"] == ContentType.NEWS
    assert item.get("is_aggregate") is False

    metadata = item["metadata"]
    assert metadata["platform"] == "hackernews"
    assert metadata["source"] == "example.com"

    article_info = metadata.get("article", {})
    assert article_info["url"] == "https://example.com/post"
    assert article_info["source_domain"] == "example.com"

    aggregator = metadata.get("aggregator", {})
    assert metadata["discussion_url"].endswith("123456")
    assert aggregator["metadata"]["score"] == 512
