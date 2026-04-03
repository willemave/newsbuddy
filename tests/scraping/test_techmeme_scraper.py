from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.scraping.techmeme_unified import (
    TechmemeFeedSettings,
    TechmemeScraper,
    TechmemeSettings,
    load_techmeme_config,
)


@pytest.fixture
def techmeme_settings() -> TechmemeSettings:
    """Provide deterministic settings for the Techmeme scraper."""

    return TechmemeSettings(
        feed=TechmemeFeedSettings(
            url="https://www.techmeme.com/feed.xml",
            limit=5,
            include_related=True,
            max_related=3,
        )
    )


@pytest.fixture
def scraper(techmeme_settings: TechmemeSettings) -> TechmemeScraper:
    """Instantiate scraper with mocked config and error logger."""

    with patch("app.scraping.techmeme_unified.load_techmeme_config") as mock_load_config:
        mock_load_config.return_value = techmeme_settings
        yield TechmemeScraper()


def test_load_techmeme_config_defaults(tmp_path: Path) -> None:
    """Missing config file should fall back to defaults."""

    missing_path = tmp_path / "missing.yml"
    settings = load_techmeme_config(missing_path)

    assert isinstance(settings, TechmemeSettings)
    assert settings.feed.url == "https://www.techmeme.com/feed.xml"
    assert settings.feed.limit == 20


def test_scrape_returns_primary_and_related(scraper: TechmemeScraper) -> None:
    """Techmeme entries are normalized into aggregate items with related links."""

    description_html = """
    <p>
        <a href="https://example.com/article"><img src="thumb.jpg"></a>
        <a href="http://www.techmeme.com/250921/p26#a250921p26"><img src="permalink.jpg"></a>
        <a href="https://example.com/">Example.com</a>:
        <span><b><a href="https://example.com/article">Sample Techmeme headline</a></b></span>
        <ul>
            <li><a href="https://related.com/story">Related story</a></li>
            <li><a href="https://another.com/piece">Another angle</a></li>
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

    with patch("app.scraping.techmeme_unified.feedparser.parse", return_value=mock_feed):
        items = scraper.scrape()

    assert len(items) == 1
    item = items[0]
    assert item["is_aggregate"] is False
    assert item["content_type"].value == "news"
    assert item["url"] == "https://example.com/article"

    metadata = item["metadata"]
    assert metadata["platform"] == "techmeme"
    assert metadata["source"] == "example.com"

    article_info = metadata.get("article", {})
    assert article_info["url"] == "https://example.com/article"
    assert article_info["source_domain"] == "example.com"

    aggregator = metadata.get("aggregator", {})
    assert metadata["discussion_url"] == "https://www.techmeme.com/250921/p26#a250921p26"
    assert "Sample Techmeme headline" in metadata["excerpt"]
    assert "Related story" in metadata["excerpt"]
    assert aggregator["metadata"]["related_links"][0]["url"] == "https://related.com/story"
    assert aggregator["metadata"]["feed_name"] == "Techmeme"


def test_scrape_skips_entries_without_article(scraper: TechmemeScraper) -> None:
    """Entries lacking external anchors should be ignored."""

    feed_entry = {
        "title": "Cluster without article",
        "link": "http://www.techmeme.com/cluster",
        "description": "<p><a href=\"http://www.techmeme.com/cluster\">Permalink</a></p>",
        "published_parsed": (2025, 9, 21, 12, 0, 0, 0, 0, 0),
    }

    mock_feed = MagicMock()
    mock_feed.entries = [feed_entry]
    mock_feed.feed = {"title": "Techmeme"}
    mock_feed.bozo = 0

    with patch("app.scraping.techmeme_unified.feedparser.parse", return_value=mock_feed):
        items = scraper.scrape()

    assert items == []
