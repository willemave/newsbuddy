"""Tests for X API helpers."""

from app.services.x_api import _extract_next_token, _map_list, _map_tweet, _normalize_external_url


def test_normalize_external_url_keeps_non_social_domains() -> None:
    """Domains that merely end with similar letters must not be filtered."""
    assert _normalize_external_url("https://index.com/article") == "https://index.com/article"
    assert (
        _normalize_external_url("https://mytwitter.com/post/1")
        == "https://mytwitter.com/post/1"
    )


def test_normalize_external_url_filters_x_twitter_domains() -> None:
    """X/Twitter domains and subdomains are excluded from fanout URLs."""
    assert _normalize_external_url("https://x.com/user/status/1") is None
    assert _normalize_external_url("https://mobile.twitter.com/user/status/1") is None
    assert _normalize_external_url("https://news.x.com/path") is None


def test_map_tweet_includes_reply_and_reference_metadata() -> None:
    """Tweet mapping should preserve reply and repost metadata."""
    tweet = _map_tweet(
        {
            "id": "123",
            "text": "Semiconductor capex keeps accelerating.",
            "author_id": "u1",
            "created_at": "2026-03-26T10:00:00Z",
            "conversation_id": "123",
            "in_reply_to_user_id": "u2",
            "referenced_tweets": [{"type": "retweeted", "id": "99"}],
            "public_metrics": {"like_count": 10, "retweet_count": 3, "reply_count": 1},
        },
        {"u1": {"id": "u1", "username": "willem", "name": "Willem"}},
    )

    assert tweet is not None
    assert tweet.author_username == "willem"
    assert tweet.in_reply_to_user_id == "u2"
    assert tweet.referenced_tweet_types == ["retweeted"]
    assert tweet.like_count == 10


def test_map_list_and_extract_next_token() -> None:
    """List mapping helpers should keep usable ids and cursors."""
    x_list = _map_list({"id": "42", "name": "AI Infra"})

    assert x_list is not None
    assert x_list.id == "42"
    assert x_list.name == "AI Infra"
    assert _extract_next_token({"next_token": "abc123"}) == "abc123"
