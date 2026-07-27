from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.scraping.podcast_unified import PodcastUnifiedScraper
from app.scraping.reddit_unified import RedditTarget, RedditUnifiedScraper


def test_podcast_no_feeds_configured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test that podcast scraper handles no feeds gracefully."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("NEWSAPP_CONFIG_DIR", str(config_dir))
    warning = MagicMock()
    monkeypatch.setattr("app.scraping.podcast_unified.logger.warning", warning)

    # PodcastUnifiedScraper now loads from database via _load_podcast_feeds()
    # When no feeds are configured, scrape() returns empty list with warning
    scraper = PodcastUnifiedScraper()

    # Mock the database call to return no feeds
    monkeypatch.setattr(scraper, "_load_podcast_feeds", lambda: [])

    result = scraper.scrape()
    assert result == []
    warning.assert_called_once_with("No podcast feeds configured")


def test_reddit_scraper_loads_db_targets_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    cfg = config_dir / "reddit.yml"
    cfg.write_text(
        """subreddits:\n  - name: MachineLearning\n    limit: 5\n""",
        encoding="utf-8",
    )

    monkeypatch.setenv("NEWSAPP_CONFIG_DIR", str(config_dir))
    db_target = RedditTarget(subreddit="LocalLLaMA", limit=3, visibility_scope="user")
    monkeypatch.setattr(
        RedditUnifiedScraper,
        "_load_subreddits_from_db",
        lambda self: [db_target],
    )

    scraper = RedditUnifiedScraper()
    assert scraper.targets == [db_target]
