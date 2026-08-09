import feedparser
import pytest

from app.models.domain.scraper_runs import is_zero_progress_scrape_failure
from app.scraping.podcast_unified import PodcastUnifiedScraper


def _build_entry(data: dict) -> feedparser.FeedParserDict:
    return feedparser.FeedParserDict(data)


def _build_enclosure(href: str, enclosure_type: str | None = None) -> feedparser.FeedParserDict:
    payload = {"href": href}
    if enclosure_type is not None:
        payload["type"] = enclosure_type
    return feedparser.FeedParserDict(payload)


def test_podcast_fallback_uses_enclosure_when_link_missing() -> None:
    scraper = PodcastUnifiedScraper()
    entry = _build_entry(
        {
            "title": "Episode 1",
            "enclosures": [_build_enclosure("https://cdn.example.com/ep1.mp3", "audio/mpeg")],
        }
    )

    item = scraper._process_entry(
        entry,
        feed_name="Test Feed",
        feed_info={"title": "Test Feed"},
        feed_url="https://example.com/feed.xml",
        user_id=1,
    )

    assert item is not None
    assert item["url"] == "https://cdn.example.com/ep1.mp3"
    assert item["metadata"]["audio_url"] == "https://cdn.example.com/ep1.mp3"


def test_podcast_fallback_uses_alternate_link_when_link_missing() -> None:
    scraper = PodcastUnifiedScraper()
    entry = _build_entry(
        {
            "title": "Episode 2",
            "links": [
                feedparser.FeedParserDict(
                    {
                        "href": "https://example.com/episode-2",
                        "rel": "alternate",
                        "type": "text/html",
                    }
                )
            ],
            "enclosures": [_build_enclosure("https://cdn.example.com/ep2.mp3", "audio/mpeg")],
        }
    )

    item = scraper._process_entry(
        entry,
        feed_name="Test Feed",
        feed_info={"title": "Test Feed"},
        feed_url="https://example.com/feed.xml",
        user_id=1,
    )

    assert item is not None
    assert item["url"] == "https://example.com/episode-2"
    assert item["metadata"]["audio_url"] == "https://cdn.example.com/ep2.mp3"


def test_podcast_fallback_accepts_enclosure_without_type() -> None:
    scraper = PodcastUnifiedScraper()
    entry = _build_entry(
        {
            "title": "Episode 3",
            "enclosures": [_build_enclosure("https://cdn.example.com/ep3.MP3")],
        }
    )

    item = scraper._process_entry(
        entry,
        feed_name="Test Feed",
        feed_info={"title": "Test Feed"},
        feed_url="https://example.com/feed.xml",
        user_id=1,
    )

    assert item is not None
    assert item["url"] == "https://cdn.example.com/ep3.MP3"
    assert item["metadata"]["audio_url"] == "https://cdn.example.com/ep3.MP3"


@pytest.mark.parametrize(
    ("payload", "content_type", "expected_error"),
    [
        (
            b"<html><body>not a podcast feed</body></html>",
            "text/html",
            "returned HTML instead of XML",
        ),
        (
            b'<?xml version="1.0"?><rss><channel><title>A & B</title></channel></rss>',
            "application/rss+xml",
            "contains malformed XML",
        ),
        (
            b'<?xml version="1.0"?><rss><channel><item></channel></rss>',
            "application/rss+xml",
            "contains malformed XML",
        ),
        (
            b'<?xml version="1.0"?><rss><channel><item>',
            "application/rss+xml",
            "contains malformed XML",
        ),
    ],
)
def test_critical_podcast_feed_parse_failure_is_reported_as_zero_progress_failure(
    monkeypatch,
    payload: bytes,
    content_type: str,
    expected_error: str,
) -> None:
    scraper = PodcastUnifiedScraper()
    parsed_feed = feedparser.parse(
        payload,
        response_headers={"content-type": content_type},
    )
    monkeypatch.setattr(
        scraper,
        "_load_podcast_feeds",
        lambda: [{"name": "Broken", "url": "https://example.com/feed.xml"}],
    )
    monkeypatch.setattr(
        "app.scraping.podcast_unified.fetch_and_parse_feed",
        lambda *_args, **_kwargs: parsed_feed,
    )

    stats = scraper.run_with_stats()

    assert stats.scraped == 0
    assert stats.saved == 0
    assert stats.duplicates == 0
    assert stats.errors == 1
    assert expected_error in stats.error_details[0]
    assert is_zero_progress_scrape_failure(stats) is True
