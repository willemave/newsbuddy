"""Tests for X API helpers."""

from contextlib import contextmanager

import app.services.x_api as x_api
from app.models.db import VendorUsageRecord
from app.services import vendor_costs
from app.services.x_api import (
    _extract_linked_tweet_ids,
    _extract_next_token,
    _map_list,
    _map_tweet,
    _normalize_external_url,
    build_tweet_processing_text,
)


def _patch_oauth_token_request(monkeypatch) -> dict[str, object]:
    captured: dict[str, object] = {}
    settings = x_api.get_settings()

    monkeypatch.setattr(settings, "x_client_id", "client-id")
    monkeypatch.setattr(settings, "x_client_secret", "client-secret")
    monkeypatch.setattr(settings, "x_oauth_redirect_uri", "https://example.com/callback")
    monkeypatch.setattr(settings, "x_oauth_token_url", "https://api.x.com/2/oauth2/token")

    def fake_request_json(method, url, *, access_token, data, auth, **kwargs):  # noqa: ANN001
        captured["method"] = method
        captured["url"] = url
        captured["data"] = data
        captured["auth"] = auth
        return {
            "access_token": "token",
            "refresh_token": "refresh",
            "expires_in": 7200,
            "scope": "tweet.read users.read",
        }

    monkeypatch.setattr(x_api, "_request_json", fake_request_json)
    return captured


def test_normalize_external_url_keeps_non_social_domains() -> None:
    """Domains that merely end with similar letters must not be filtered."""
    assert _normalize_external_url("https://index.com/article") == "https://index.com/article"
    assert _normalize_external_url("https://mytwitter.com/post/1") == "https://mytwitter.com/post/1"


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


def test_map_tweet_detects_video_media_but_skips_animated_gifs() -> None:
    """Media expansions should surface native video availability for downstream processing."""
    tweet = _map_tweet(
        {
            "id": "123",
            "text": "Watch this",
            "author_id": "u1",
            "attachments": {"media_keys": ["gif-1", "video-1"]},
        },
        {"u1": {"id": "u1", "username": "willem", "name": "Willem"}},
        {
            "gif-1": {"media_key": "gif-1", "type": "animated_gif", "duration_ms": 1000},
            "video-1": {"media_key": "video-1", "type": "video", "duration_ms": 45000},
        },
    )

    assert tweet is not None
    assert tweet.has_video is True
    assert tweet.video_duration_ms == 45000


def test_map_list_and_extract_next_token() -> None:
    """List mapping helpers should keep usable ids and cursors."""
    x_list = _map_list({"id": "42", "name": "AI Infra"})

    assert x_list is not None
    assert x_list.id == "42"
    assert x_list.name == "AI Infra"
    assert _extract_next_token({"next_token": "abc123"}) == "abc123"


def test_exchange_oauth_code_includes_redirect_uri(monkeypatch) -> None:
    """Authorization-code exchange must send redirect_uri to X."""
    captured = _patch_oauth_token_request(monkeypatch)

    x_api.exchange_oauth_code(code="oauth-code", code_verifier="verifier")

    assert captured["method"] == "POST"
    assert captured["url"] == "https://api.x.com/2/oauth2/token"
    assert captured["data"] == {
        "grant_type": "authorization_code",
        "client_id": "client-id",
        "redirect_uri": "https://example.com/callback",
        "code": "oauth-code",
        "code_verifier": "verifier",
    }
    assert captured["auth"] == ("client-id", "client-secret")


def test_refresh_oauth_token_omits_redirect_uri(monkeypatch) -> None:
    """Refresh-token exchange should not send redirect_uri."""
    captured = _patch_oauth_token_request(monkeypatch)

    x_api.refresh_oauth_token(refresh_token="refresh-token")

    assert captured["method"] == "POST"
    assert captured["url"] == "https://api.x.com/2/oauth2/token"
    assert captured["data"] == {
        "grant_type": "refresh_token",
        "client_id": "client-id",
        "refresh_token": "refresh-token",
    }
    assert captured["auth"] == ("client-id", "client-secret")


def test_fetch_tweet_by_id_records_vendor_usage(db_session, monkeypatch) -> None:
    @contextmanager
    def fake_get_db():
        yield db_session
        db_session.commit()

    monkeypatch.setattr(
        x_api,
        "_request_json",
        lambda *_args, **_kwargs: {
            "data": {"id": "123", "text": "Hello", "author_id": "u1"},
            "includes": {"users": [{"id": "u1", "username": "alice", "name": "Alice"}]},
        },
    )
    monkeypatch.setattr(vendor_costs, "get_db", fake_get_db)

    result = x_api.fetch_tweet_by_id(
        tweet_id="123",
        telemetry={
            "feature": "x_sync",
            "operation": "x_sync.timeline.read",
            "user_id": 11,
        },
    )

    assert result.success is True
    persisted = db_session.query(VendorUsageRecord).one()
    assert persisted.provider == "x"
    assert persisted.model == "posts.read"
    assert persisted.user_id == 11
    assert persisted.request_count == 1
    assert persisted.resource_count == 1
