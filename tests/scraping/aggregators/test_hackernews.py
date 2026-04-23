from unittest.mock import MagicMock, patch

from app.models.metadata import ContentType
from app.scraping.aggregators.config import HackerNewsAggregator
from app.scraping.aggregators.hackernews import HackerNewsAggregatorScraper


def _build_settings() -> HackerNewsAggregator:
    return HackerNewsAggregator(
        key="hackernews",
        name="Hacker News",
        kind="hackernews",
        limit=2,
    )


@patch("app.scraping.aggregators.hackernews.httpx.Client")
def test_hackernews_scraper_normalizes_top_stories(mock_httpx_client) -> None:
    mock_client = MagicMock()
    mock_httpx_client.return_value.__enter__.return_value = mock_client
    mock_httpx_client.return_value.__exit__.return_value = None

    top_response = MagicMock()
    top_response.json.return_value = [123456, 987654, 555555]

    story_response = MagicMock()
    story_response.json.return_value = {
        "id": 123456,
        "type": "story",
        "title": "Sample HN story",
        "url": "http://example.com/article",
        "by": "alice",
        "score": 200,
        "descendants": 25,
        "time": 1_700_000_000,
    }
    second_story = MagicMock()
    second_story.json.return_value = {
        "id": 987654,
        "type": "story",
        "title": "Second story",
        "url": "https://other.com/story",
        "by": "bob",
        "score": 50,
        "descendants": 7,
        "time": 1_700_001_000,
    }

    mock_client.get.side_effect = [top_response, story_response, second_story]

    scraper = HackerNewsAggregatorScraper(_build_settings())
    items = scraper.scrape()

    # limit=2 caps the number fetched
    assert len(items) == 2
    item = items[0]
    assert item["content_type"] == ContentType.NEWS
    assert item["is_aggregate"] is False

    metadata = item["metadata"]
    assert metadata["platform"] == "hackernews"
    assert metadata["source"] == "example.com"
    assert metadata["article"]["url"] == "https://example.com/article"
    assert metadata["article"]["source_domain"] == "example.com"

    aggregator = metadata["aggregator"]
    assert aggregator["key"] == "hackernews"
    assert aggregator["name"] == "Hacker News"
    assert aggregator["external_id"] == "123456"
    assert aggregator["metadata"]["score"] == 200
    assert metadata["discussion_url"].endswith("123456")


@patch("app.scraping.aggregators.hackernews.httpx.Client")
def test_hackernews_scraper_skips_jobs_and_no_url(mock_httpx_client) -> None:
    mock_client = MagicMock()
    mock_httpx_client.return_value.__enter__.return_value = mock_client
    mock_httpx_client.return_value.__exit__.return_value = None

    top_response = MagicMock()
    top_response.json.return_value = [1, 2]

    job_response = MagicMock()
    job_response.json.return_value = {"id": 1, "type": "job", "title": "Hiring"}
    ask_response = MagicMock()
    ask_response.json.return_value = {"id": 2, "type": "story", "title": "Ask HN: ..."}

    mock_client.get.side_effect = [top_response, job_response, ask_response]

    scraper = HackerNewsAggregatorScraper(_build_settings())
    items = scraper.scrape()
    assert items == []
