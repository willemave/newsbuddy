import logging
from pathlib import Path

import pytest

from app.scraping.podcast_unified import PodcastUnifiedScraper
from app.scraping.reddit_unified import RedditUnifiedScraper
from app.scraping.substack_unified import load_substack_feeds
from app.utils.error_logger import get_scraper_metrics, reset_scraper_metrics


@pytest.fixture(autouse=True)
def _reset_metrics() -> None:
    reset_scraper_metrics()


def test_substack_missing_config_logs_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    monkeypatch.setenv("NEWSAPP_CONFIG_DIR", str(config_dir))
    caplog.set_level(logging.WARNING)

    feeds = load_substack_feeds()
    assert feeds == []

    # Second call should not emit a duplicate warning
    feeds = load_substack_feeds()
    assert feeds == []

    warn_messages = [
        record.message for record in caplog.records if record.levelno == logging.WARNING
    ]
    missing_logs = [message for message in warn_messages if "config_missing" in message]
    assert len(missing_logs) == 1

    metrics = get_scraper_metrics()
    assert metrics["Substack"]["scrape_config_missing"] == 1


def test_substack_env_override_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    cfg = config_dir / "substack.yml"
    cfg.write_text(
        """feeds:\n  - name: Test\n    url: https://example.com/feed\n    limit: 3\n""",
        encoding="utf-8",
    )

    monkeypatch.setenv("NEWSAPP_CONFIG_DIR", str(config_dir))

    feeds = load_substack_feeds()
    assert feeds == [
        {
            "url": "https://example.com/feed",
            "name": "Test",
            "limit": 3,
        }
    ]

    metrics = get_scraper_metrics()
    assert "Substack" not in metrics or "scrape_config_missing" not in metrics["Substack"]


def test_podcast_no_feeds_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Test that podcast scraper handles no feeds gracefully."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("NEWSAPP_CONFIG_DIR", str(config_dir))
    caplog.set_level(logging.WARNING)

    # PodcastUnifiedScraper now loads from database via _load_podcast_feeds()
    # When no feeds are configured, scrape() returns empty list with warning
    scraper = PodcastUnifiedScraper()

    # Mock the database call to return no feeds
    monkeypatch.setattr(scraper, "_load_podcast_feeds", lambda: [])

    result = scraper.scrape()
    assert result == []

    warn_messages = [
        record.message for record in caplog.records if record.levelno == logging.WARNING
    ]
    assert any("No podcast feeds configured" in message for message in warn_messages)


def test_reddit_config_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    cfg = config_dir / "reddit.yml"
    cfg.write_text(
        """subreddits:\n  - name: MachineLearning\n    limit: 5\n""",
        encoding="utf-8",
    )

    monkeypatch.setenv("NEWSAPP_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(
        RedditUnifiedScraper,
        "_load_subreddits_from_db",
        lambda self: [],
    )

    scraper = RedditUnifiedScraper()
    assert scraper.targets == []

    metrics = get_scraper_metrics()
    assert "Reddit" not in metrics or "scrape_config_missing" not in metrics["Reddit"]
