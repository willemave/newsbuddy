"""Tests for Twitter share URL helpers."""

from app.services import twitter_share


def test_tweet_url_helpers() -> None:
    assert twitter_share.extract_tweet_id("https://twitter.com/user/status/123") == "123"
    assert twitter_share.extract_tweet_id("https://x.com/i/status/456") == "456"
    assert twitter_share.canonical_tweet_url("789") == "https://x.com/i/status/789"
    assert twitter_share.is_tweet_url("https://example.com") is False
