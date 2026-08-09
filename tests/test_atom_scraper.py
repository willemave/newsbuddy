"""Tests for Atom feed scraper."""

from unittest.mock import MagicMock, call

import httpx

from app.scraping.atom_unified import AtomScraper


def test_atom_scraper_in_runner(monkeypatch):
    """Test that AtomScraper is registered in ScraperRunner."""
    from app.scraping import runner as scraping_runner

    class _StubScraper:
        def __init__(self, name: str):
            self.name = name

    monkeypatch.setattr(scraping_runner, "load_aggregator_scrapers", lambda: [])
    monkeypatch.setattr(scraping_runner, "RedditUnifiedScraper", lambda: _StubScraper("Reddit"))
    monkeypatch.setattr(scraping_runner, "SubstackScraper", lambda: _StubScraper("Substack"))
    monkeypatch.setattr(scraping_runner, "PodcastUnifiedScraper", lambda: _StubScraper("Podcast"))
    monkeypatch.setattr(scraping_runner, "AtomScraper", lambda: _StubScraper("Atom"))

    runner = scraping_runner.ScraperRunner()
    scraper_names = runner.list_scrapers()

    assert "Atom" in scraper_names
    assert "Twitter" not in scraper_names


def test_atom_scraper_no_feeds_logs_info(
    monkeypatch,
):
    """Expected empty Atom config should not warn."""
    scraper = AtomScraper()
    monkeypatch.setattr(scraper, "_load_feeds", lambda: [])
    info = MagicMock()
    warning = MagicMock()
    monkeypatch.setattr("app.scraping.atom_unified.logger.info", info)
    monkeypatch.setattr("app.scraping.atom_unified.logger.warning", warning)

    items = scraper.scrape()

    assert items == []
    info.assert_called_once_with("No Atom feeds configured. Skipping scrape.")
    warning.assert_not_called()


def test_atom_scraper_continues_after_feed_timeout(monkeypatch) -> None:
    scraper = AtomScraper()
    first_url = "https://slow.example/feed.atom"
    second_url = "https://working.example/feed.atom"
    monkeypatch.setattr(
        scraper,
        "_load_feeds",
        lambda: [
            {"url": first_url, "name": "Slow", "limit": 10},
            {"url": second_url, "name": "Working", "limit": 10},
        ],
    )

    parsed_feed = MagicMock()
    parsed_feed.bozo = False
    parsed_feed.feed = {"title": "Working", "subtitle": ""}
    parsed_feed.entries = [
        {
            "title": "Recovered item",
            "link": "https://working.example/item",
            "summary": "Summary",
        }
    ]

    def fetch_by_url(url: str, **_kwargs):
        if url == first_url:
            raise httpx.ReadTimeout("feed timed out", request=httpx.Request("GET", first_url))
        assert url == second_url
        return parsed_feed

    fetch = MagicMock(side_effect=fetch_by_url)
    monkeypatch.setattr("app.scraping.atom_unified.fetch_and_parse_feed", fetch)

    items = scraper.scrape()

    assert [item["title"] for item in items] == ["Recovered item"]
    fetch.assert_has_calls(
        [
            call(first_url, user_id=0, execution_id=None),
            call(second_url, user_id=0, execution_id=None),
        ],
        any_order=True,
    )
