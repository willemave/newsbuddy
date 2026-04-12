from types import SimpleNamespace

import pytest
from pytest_mock import MockerFixture

from app.scraping.reddit_unified import RedditTarget, RedditUnifiedScraper


@pytest.fixture(autouse=True)
def configure_reddit_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.scraping import reddit_unified as reddit_module

    monkeypatch.setattr(reddit_module.settings, "reddit_client_id", "client-id", raising=False)
    monkeypatch.setattr(
        reddit_module.settings, "reddit_client_secret", "client-secret", raising=False
    )
    monkeypatch.setattr(reddit_module.settings, "reddit_username", "bot_user", raising=False)
    monkeypatch.setattr(reddit_module.settings, "reddit_password", "bot_pass", raising=False)
    monkeypatch.setattr(reddit_module.settings, "reddit_read_only", True, raising=False)
    monkeypatch.setattr(
        reddit_module.settings,
        "reddit_user_agent",
        "news-app.tests/1.0 (by u/tester)",
        raising=False,
    )


def test_reddit_scraper_uses_praw(monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture) -> None:
    from app.scraping import reddit_unified as reddit_module

    submission = mocker.Mock()
    submission.is_self = False
    submission.url = "https://example.com/story"
    submission.permalink = "/r/artificial/comments/abc123/story"
    submission.removed_by_category = None
    submission.title = "Example Story"
    submission.subreddit = SimpleNamespace(display_name="artificial")
    submission.score = 42
    submission.num_comments = 3
    submission.upvote_ratio = 0.91
    submission.over_18 = False
    submission.selftext = "body"
    submission.domain = "example.com"
    submission.id = "abc123"
    submission.author = SimpleNamespace(name="author1")

    mock_subreddit = mocker.Mock()
    mock_subreddit.new.return_value = [submission]
    mock_reddit = mocker.Mock()
    mock_reddit.subreddit.return_value = mock_subreddit

    mocker.patch.object(reddit_module.praw, "Reddit", return_value=mock_reddit)

    scraper = RedditUnifiedScraper()
    scraper.targets = [RedditTarget(subreddit="artificial", limit=5, visibility_scope="user")]

    items = scraper.scrape()

    assert len(items) == 1
    item = items[0]
    assert item["url"] == "https://example.com/story"
    assert (
        item["metadata"]["discussion_url"]
        == "https://www.reddit.com/r/artificial/comments/abc123/story"
    )
    assert item["visibility_scope"] == "user"
    assert item["metadata"]["aggregator"]["metadata"]["score"] == 42

    mock_reddit.subreddit.assert_called_once_with("artificial")
    mock_subreddit.new.assert_called_once_with(limit=5)
    assert mock_reddit.read_only is True


def test_is_external_url_allows_front_media() -> None:
    scraper = RedditUnifiedScraper()
    assert scraper._is_external_url("https://i.redd.it/image.jpg", allow_reddit_media=True) is True
    assert (
        scraper._is_external_url("https://www.reddit.com/gallery/abc123", allow_reddit_media=True)
        is True
    )
    assert (
        scraper._is_external_url(
            "https://www.reddit.com/r/test/comments/abc123",
            allow_reddit_media=True,
        )
        is False
    )
