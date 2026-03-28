"""Focused tests for Twitter scraper API fallback behavior."""

from __future__ import annotations

from app.scraping.twitter_unified import TwitterUnifiedScraper
from app.services.x_api import XTweet, XTweetsPage


def test_parse_tweet_date_supports_iso_timestamp() -> None:
    """ISO timestamps from the official X API should parse cleanly."""
    scraper = TwitterUnifiedScraper()

    parsed = scraper._parse_tweet_date("2026-03-27T21:56:00Z")

    assert parsed is not None
    assert parsed.isoformat() == "2026-03-27T21:56:00+00:00"


def test_scrape_uses_playwright_when_api_scrape_raises(
    monkeypatch,
) -> None:
    """API errors should not prevent the Playwright fallback from running."""
    scraper = TwitterUnifiedScraper()
    scraper.config = {
        "twitter_lists": [{"name": "FinTech", "list_id": "1521123920950222849"}],
        "settings": {},
    }
    scraper.settings = {}

    monkeypatch.setattr(scraper, "_recent_scrape_hours", lambda _config: 0.0)
    monkeypatch.setattr(
        scraper,
        "_scrape_list_api",
        lambda _config: (_ for _ in ()).throw(RuntimeError("api unavailable")),
    )

    fallback_item = {
        "url": "https://example.com/story",
        "title": "Story",
        "content_type": "news",
        "metadata": {"platform": "twitter", "source": "example.com"},
    }
    monkeypatch.setattr(scraper, "_scrape_list_playwright", lambda _config: [fallback_item])

    items = scraper.scrape()

    assert items == [fallback_item]


def test_scrape_list_api_builds_news_entries(monkeypatch) -> None:
    """Official X API list tweets should be converted into news entries."""
    scraper = TwitterUnifiedScraper()
    scraper.settings = {
        "default_limit": 50,
        "default_hours_back": 24,
        "include_retweets": False,
        "include_replies": False,
        "min_engagement": 0,
    }

    monkeypatch.setattr(scraper, "_get_x_api_access_token", lambda: "token")
    monkeypatch.setattr(
        "app.scraping.twitter_unified.fetch_list_tweets",
        lambda **_kwargs: XTweetsPage(
            tweets=[
                XTweet(
                    id="tweet-1",
                    text="Interesting link",
                    author_username="news_bot",
                    author_name="News Bot",
                    created_at="2026-03-27T21:56:00Z",
                    like_count=5,
                    retweet_count=2,
                    reply_count=1,
                    external_urls=["https://example.com/story"],
                )
            ],
            next_token=None,
        ),
    )

    items = scraper._scrape_list_api(
        {
            "name": "FinTech",
            "list_id": "1521123920950222849",
            "limit": 10,
            "hours_back": 24,
        }
    )

    assert items is not None
    assert len(items) == 1
    item = items[0]
    assert item["url"] == "https://example.com/story"
    assert item["metadata"]["platform"] == "twitter"
    assert item["metadata"]["aggregator"]["metadata"]["list_id"] == "1521123920950222849"
