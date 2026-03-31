"""Tests for X API helpers."""

from app.services.x_api import (
    _extract_linked_tweet_ids,
    _extract_next_token,
    _map_list,
    _map_tweet,
    _normalize_external_url,
    build_tweet_processing_text,
)


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
    assert tweet.author_id == "u1"
    assert tweet.in_reply_to_user_id == "u2"
    assert tweet.referenced_tweet_types == ["retweeted"]
    assert tweet.linked_tweet_ids == ["99"]
    assert tweet.like_count == 10


def test_extract_linked_tweet_ids_includes_status_urls_from_entities() -> None:
    """Entity URLs pointing at X posts should be treated as linked tweets."""
    linked_ids = _extract_linked_tweet_ids(
        {"referenced_tweets": [{"type": "quoted", "id": "99"}]},
        {
            "urls": [
                {"expanded_url": "https://x.com/alice/status/123"},
                {"expanded_url": "https://twitter.com/bob/status/456"},
                {"expanded_url": "https://example.com/story"},
            ]
        },
    )

    assert linked_ids == ["99", "123", "456"]


def test_map_tweet_keeps_external_article_urls_and_linked_tweet_ids() -> None:
    """Non-X URLs should remain scrape targets while linked statuses are recorded separately."""
    tweet = _map_tweet(
        {
            "id": "123",
            "text": "Read this",
            "author_id": "u1",
            "entities": {
                "urls": [
                    {"expanded_url": "https://example.com/story"},
                    {"expanded_url": "https://x.com/alice/status/456"},
                ]
            },
        },
        {"u1": {"id": "u1", "username": "willem", "name": "Willem"}},
    )

    assert tweet is not None
    assert tweet.external_urls == ["https://example.com/story"]
    assert tweet.linked_tweet_ids == ["456"]


def test_map_tweet_extracts_native_article_content() -> None:
    """Native X article payloads should be preserved for later processing."""
    tweet = _map_tweet(
        {
            "id": "123",
            "text": "Short teaser",
            "author_id": "u1",
            "article": {
                "title": "Native X Article",
                "plain_text": "This is the full native article body.",
            },
        },
        {"u1": {"id": "u1", "username": "willem", "name": "Willem"}},
    )

    assert tweet is not None
    assert tweet.text == "Short teaser"
    assert tweet.article_title == "Native X Article"
    assert tweet.article_text == "This is the full native article body."
    assert (
        build_tweet_processing_text(tweet)
        == "Native X Article\n\nThis is the full native article body."
    )


def test_map_tweet_prefers_note_tweet_text_for_processing() -> None:
    """Long-form note tweets should surface their full text for processing."""
    tweet = _map_tweet(
        {
            "id": "123",
            "text": "Truncated preview",
            "author_id": "u1",
            "note_tweet": {
                "text": "This is the complete long-form note tweet text.",
            },
        },
        {"u1": {"id": "u1", "username": "willem", "name": "Willem"}},
    )

    assert tweet is not None
    assert tweet.text == "Truncated preview"
    assert tweet.note_tweet_text == "This is the complete long-form note tweet text."
    assert build_tweet_processing_text(tweet) == "This is the complete long-form note tweet text."


def test_map_list_and_extract_next_token() -> None:
    """List mapping helpers should keep usable ids and cursors."""
    x_list = _map_list({"id": "42", "name": "AI Infra"})

    assert x_list is not None
    assert x_list.id == "42"
    assert x_list.name == "AI Infra"
    assert _extract_next_token({"next_token": "abc123"}) == "abc123"
