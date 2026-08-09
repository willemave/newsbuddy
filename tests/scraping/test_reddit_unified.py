from types import SimpleNamespace

import pytest
from pytest_mock import MockerFixture

from app.models.contracts import SummaryKind, SummaryVersion
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


def test_reddit_scraper_includes_self_posts_as_ready_summaries(
    monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    from app.scraping import reddit_unified as reddit_module

    submission = mocker.Mock()
    submission.is_self = True
    submission.url = "https://www.reddit.com/r/dogs/comments/abc123/what_breed_should_i_get/"
    submission.permalink = "/r/dogs/comments/abc123/what_breed_should_i_get/"
    submission.removed_by_category = None
    submission.title = "What breed should I get?"
    submission.subreddit = SimpleNamespace(display_name="dogs")
    submission.score = 7
    submission.num_comments = 2
    submission.upvote_ratio = 0.88
    submission.over_18 = False
    submission.selftext = "Looking for advice on a medium-energy dog for an apartment."
    submission.domain = "self.dogs"
    submission.id = "abc123"
    submission.author = SimpleNamespace(name="dog_owner")

    mock_subreddit = mocker.Mock()
    mock_subreddit.new.return_value = [submission]
    mock_reddit = mocker.Mock()
    mock_reddit.subreddit.return_value = mock_subreddit

    mocker.patch.object(reddit_module.praw, "Reddit", return_value=mock_reddit)

    scraper = RedditUnifiedScraper()
    scraper.targets = [
        RedditTarget(
            subreddit="dogs",
            limit=1,
            visibility_scope="user",
            owner_user_id=15,
            user_scraper_config_id=20,
        )
    ]

    items = scraper.scrape()

    assert len(items) == 1
    item = items[0]
    assert item["url"] == "https://www.reddit.com/r/dogs/comments/abc123/what_breed_should_i_get"
    assert item["owner_user_id"] == 15
    assert item["user_scraper_config_id"] == 20
    assert item["metadata"]["summary"] == {
        "title": "What breed should I get?",
        "article_url": "https://www.reddit.com/r/dogs/comments/abc123/what_breed_should_i_get",
        "key_points": ["Looking for advice on a medium-energy dog for an apartment."],
        "summary": "Looking for advice on a medium-energy dog for an apartment.",
    }
    assert item["metadata"]["summary_kind"] == SummaryKind.SHORT_NEWS.value
    assert item["metadata"]["summary_version"] == SummaryVersion.V1.value
    assert item["metadata"]["items"][0]["summary"] == (
        "Looking for advice on a medium-energy dog for an apartment."
    )
    mock_subreddit.new.assert_called_once_with(limit=1)


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


def test_reddit_target_failure_is_reported_in_run_stats(
    mocker: MockerFixture,
) -> None:
    scraper = RedditUnifiedScraper()
    scraper.targets = [RedditTarget(subreddit="unavailable", limit=5, visibility_scope="user")]
    mocker.patch.object(scraper, "_get_reddit_client", return_value=mocker.Mock())
    mocker.patch.object(
        scraper,
        "_scrape_subreddit",
        side_effect=RuntimeError("provider unavailable"),
    )
    mocker.patch.object(
        scraper,
        "_save_items_with_stats",
        return_value={
            "saved": 0,
            "duplicates": 0,
            "errors": 0,
            "error_details": [],
            "processed_by_config_id": {},
        },
    )

    stats = scraper.run_with_stats()

    assert (stats.scraped, stats.saved, stats.errors) == (0, 0, 1)
    assert stats.error_details == ["r/unavailable: provider unavailable"]


def test_reddit_configured_target_without_credentials_is_an_error(
    mocker: MockerFixture,
) -> None:
    scraper = RedditUnifiedScraper()
    scraper.targets = [RedditTarget(subreddit="configured", limit=5, visibility_scope="user")]
    mocker.patch.object(scraper, "_get_reddit_client", return_value=None)
    mocker.patch.object(
        scraper,
        "_save_items_with_stats",
        return_value={
            "saved": 0,
            "duplicates": 0,
            "errors": 0,
            "error_details": [],
            "processed_by_config_id": {},
        },
    )

    stats = scraper.run_with_stats()

    assert (stats.scraped, stats.saved, stats.errors) == (0, 0, 1)
    assert stats.error_details == ["reddit: Reddit credentials are not configured"]
