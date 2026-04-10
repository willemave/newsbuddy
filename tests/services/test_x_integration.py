"""Tests for X integration sync flows."""

from datetime import UTC, datetime, timedelta

import pytest

import app.services.x_integration as x_integration
from app.constants import CONTENT_DIGEST_VISIBILITY_DIGEST_ONLY
from app.core.settings import get_settings
from app.models.schema import (
    NewsItem,
    UserIntegrationConnection,
    UserIntegrationSyncState,
)
from app.services.token_crypto import decrypt_token
from app.services.x_api import XTokenResponse, XTweet, XTweetsPage, XUser
from app.services.x_digest_filter import XDigestFilterDecision
from app.services.x_integration import (
    _upsert_x_digest_tweet_content,
    exchange_x_oauth,
    start_x_oauth,
    sync_x_sources_for_user,
)


class _FakeQueueGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int | None, dict | None]] = []

    def enqueue(self, task_type, *, content_id=None, payload=None, queue_name=None, dedupe=None):  # noqa: ANN001
        self.calls.append((task_type.value, content_id, payload))
        return len(self.calls)


def _build_connection(test_user, scopes: list[str]) -> UserIntegrationConnection:
    return UserIntegrationConnection(
        user_id=test_user.id,
        provider="x",
        provider_user_id="42",
        provider_username="willem",
        access_token_encrypted="encrypted",
        refresh_token_encrypted="refresh",
        is_active=True,
        scopes=scopes,
        connection_metadata={},
    )


def _tweet(
    tweet_id: str,
    text: str,
    *,
    in_reply_to_user_id: str | None = None,
) -> XTweet:
    return XTweet(
        id=tweet_id,
        text=text,
        author_username="willem",
        author_name="Willem",
        created_at="2026-03-26T10:00:00Z",
        like_count=12,
        retweet_count=3,
        reply_count=1,
        in_reply_to_user_id=in_reply_to_user_id,
    )


def test_start_x_oauth_persists_pending_state_and_sync_scopes(
    db_session,
    test_user,
    monkeypatch,
):
    """OAuth start should persist pending state and request timeline/list scopes."""
    monkeypatch.setattr("app.services.x_integration.is_x_oauth_configured", lambda: True)

    captured: dict[str, object] = {}

    def fake_build_oauth_authorize_url(*, state, code_challenge, scopes):  # noqa: ANN001
        captured["state"] = state
        captured["code_challenge"] = code_challenge
        captured["scopes"] = scopes
        return "https://x.com/i/oauth2/authorize?state=test-state"

    monkeypatch.setattr(
        "app.services.x_integration.build_oauth_authorize_url",
        fake_build_oauth_authorize_url,
    )

    authorize_url, state, scopes = start_x_oauth(
        db_session,
        user=test_user,
        twitter_username="@Willem_AW",
    )

    connection = (
        db_session.query(UserIntegrationConnection)
        .filter_by(user_id=test_user.id, provider="x")
        .one()
    )

    assert authorize_url.startswith("https://x.com/i/oauth2/authorize")
    assert state == captured["state"]
    assert "bookmark.read" in scopes
    assert "tweet.read" in scopes
    assert "users.read" in scopes
    assert "offline.access" in scopes
    assert test_user.twitter_username == "willem_aw"
    assert connection.scopes == scopes
    assert connection.connection_metadata["oauth_pending"]["state"] == state
    assert connection.connection_metadata["oauth_pending"]["code_verifier"]
    assert captured["code_challenge"]


def test_exchange_x_oauth_stores_encrypted_tokens_and_profile(
    db_session,
    test_user,
    monkeypatch,
):
    """OAuth exchange should persist encrypted tokens and the authenticated X profile."""
    monkeypatch.setattr(get_settings(), "x_token_encryption_key", "test-encryption-key")

    connection = UserIntegrationConnection(
        user_id=test_user.id,
        provider="x",
        is_active=False,
        scopes=["tweet.read", "users.read", "bookmark.read", "offline.access"],
        connection_metadata={
            "oauth_pending": {
                "state": "oauth-state",
                "code_verifier": "verifier",
                "created_at": "2026-03-26T10:00:00+00:00",
            }
        },
    )
    db_session.add(connection)
    db_session.commit()

    monkeypatch.setattr(
        "app.services.x_integration._pending_state_expired",
        lambda _created_at: False,
    )
    monkeypatch.setattr(
        "app.services.x_integration.exchange_oauth_code",
        lambda code, code_verifier: XTokenResponse(
            access_token="access-token",
            refresh_token="refresh-token",
            expires_in=7200,
            scopes=["tweet.read", "users.read", "bookmark.read", "offline.access"],
        ),
    )
    monkeypatch.setattr(
        "app.services.x_integration.get_authenticated_user",
        lambda access_token: XUser(id="42", username="willemaw", name="Willem"),
    )

    view = exchange_x_oauth(
        db_session,
        user=test_user,
        code="oauth-code",
        state="oauth-state",
    )

    db_session.refresh(connection)
    assert view.connected is True
    assert connection.is_active is True
    assert connection.provider_user_id == "42"
    assert connection.provider_username == "willemaw"
    assert decrypt_token(connection.access_token_encrypted) == "access-token"
    assert decrypt_token(connection.refresh_token_encrypted) == "refresh-token"
    assert "oauth_pending" not in connection.connection_metadata
    assert test_user.twitter_username == "willemaw"


def test_sync_x_sources_ingests_digest_only_timeline_content(db_session, test_user, monkeypatch):
    """Timeline sync should create user-scoped news items and processing tasks."""
    connection = _build_connection(
        test_user,
        ["tweet.read", "users.read", "bookmark.read"],
    )
    db_session.add(connection)
    db_session.commit()

    queue_gateway = _FakeQueueGateway()
    recorded_prompts: list[str] = []

    monkeypatch.setattr(
        "app.services.x_integration._ensure_valid_access_token",
        lambda *_args, **_kwargs: "token",
    )
    monkeypatch.setattr(
        "app.services.x_integration._ensure_provider_user_id",
        lambda *_args, **_kwargs: "42",
    )
    monkeypatch.setattr(
        "app.services.x_integration.fetch_bookmarks",
        lambda **_kwargs: XTweetsPage(tweets=[]),
    )
    monkeypatch.setattr(
        "app.services.x_integration.fetch_reverse_chronological_timeline",
        lambda **_kwargs: XTweetsPage(
            tweets=[
                _tweet("101", "TSMC raises capex again as AI packaging demand stays tight."),
                _tweet("102", "@reply should be skipped", in_reply_to_user_id="7"),
            ]
        ),
    )

    def fake_score_x_digest_candidate(*, tweet, user_prompt, source_type, source_label):  # noqa: ANN001
        recorded_prompts.append(user_prompt)
        return XDigestFilterDecision(
            score=0.91,
            reason=f"High-signal {source_type} post from {source_label}.",
            accepted=True,
        )

    monkeypatch.setattr(
        "app.services.x_integration.score_x_digest_candidate",
        fake_score_x_digest_candidate,
    )
    monkeypatch.setattr("app.services.x_integration.get_task_queue_gateway", lambda: queue_gateway)

    summary = sync_x_sources_for_user(db_session, user_id=test_user.id)

    assert summary.status == "success"
    assert summary.channels["timeline"].accepted == 1
    assert summary.channels["timeline"].filtered_out == 1
    assert summary.channels["timeline"].created == 1

    news_item = db_session.query(NewsItem).one()
    assert news_item.visibility_scope == "user"
    assert news_item.owner_user_id == test_user.id
    assert news_item.article_url == "https://x.com/i/status/101"
    assert news_item.discussion_url == "https://x.com/i/status/101"
    assert news_item.raw_metadata["digest_visibility"] == CONTENT_DIGEST_VISIBILITY_DIGEST_ONLY
    assert news_item.raw_metadata["source_type"] == "x_timeline"
    assert news_item.raw_metadata["filter_score"] == 0.91
    assert news_item.raw_metadata["submitted_by_user_id"] == test_user.id
    assert recorded_prompts
    assert queue_gateway.calls == [
        ("enrich_news_item_article", None, {"news_item_id": news_item.id}),
    ]


def test_upsert_x_digest_tweet_content_does_not_reenqueue_existing_non_ready_rows(
    db_session,
    test_user,
    monkeypatch,
):
    """Existing short-form rows should not re-enter enrichment on every sync."""
    existing = NewsItem(
        ingest_key="existing-x-tweet",
        visibility_scope="user",
        owner_user_id=test_user.id,
        platform="twitter",
        source_type="x_timeline",
        source_label="X Following",
        source_external_id="401",
        article_url="https://x.com/i/status/401",
        canonical_story_url="https://x.com/i/status/401",
        canonical_item_url="https://x.com/i/status/401",
        discussion_url="https://x.com/i/status/401",
        article_title="Existing X post",
        summary_title="Existing X post",
        raw_metadata={},
        status="new",
    )
    db_session.add(existing)
    db_session.commit()

    queue_gateway = _FakeQueueGateway()
    monkeypatch.setattr("app.services.x_integration.get_task_queue_gateway", lambda: queue_gateway)

    was_created = _upsert_x_digest_tweet_content(
        db_session,
        user=test_user,
        tweet=_tweet("401", "Existing sync candidate should not be re-enqueued."),
        source_type="x_timeline",
        source_label="X Following",
        submitted_via="x_timeline",
        filter_decision=XDigestFilterDecision(
            score=0.92,
            reason="Relevant timeline signal.",
            accepted=True,
        ),
        aggregator_metadata={"timeline_type": "reverse_chronological"},
    )

    assert was_created is False
    assert queue_gateway.calls == []


def test_sync_x_sources_skips_bookmarks_when_bookmark_channel_is_recent(
    db_session,
    test_user,
    monkeypatch,
):
    """Bookmark channel should be independently throttled from timeline sync."""
    connection = _build_connection(test_user, ["tweet.read", "users.read", "bookmark.read"])
    db_session.add(connection)
    db_session.flush()
    db_session.add(
        UserIntegrationSyncState(
            connection_id=connection.id,
            last_status="success",
            sync_metadata={
                "bookmarks": {"last_synced_at": datetime.now(UTC).isoformat()},
                "timeline": {"last_synced_item_id": "123"},
            },
        )
    )
    db_session.commit()

    monkeypatch.setattr(get_settings(), "x_bookmark_sync_min_interval_minutes", 360)
    monkeypatch.setattr(
        "app.services.x_integration._ensure_valid_access_token",
        lambda *_args, **_kwargs: "token",
    )
    monkeypatch.setattr(
        "app.services.x_integration._ensure_provider_user_id",
        lambda *_args, **_kwargs: "42",
    )
    monkeypatch.setattr(
        "app.services.x_integration.fetch_bookmarks",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("bookmark fetch should skip")),
    )
    monkeypatch.setattr(
        "app.services.x_integration.fetch_reverse_chronological_timeline",
        lambda **_kwargs: XTweetsPage(tweets=[_tweet("101", "fresh timeline post")]),
    )
    monkeypatch.setattr(
        "app.services.x_integration.score_x_digest_candidate",
        lambda **_kwargs: XDigestFilterDecision(score=0.9, reason="keep", accepted=True),
    )
    monkeypatch.setattr(
        "app.services.x_integration.get_task_queue_gateway",
        lambda: _FakeQueueGateway(),
    )

    summary = sync_x_sources_for_user(db_session, user_id=test_user.id)

    assert summary.status == "success"
    assert summary.channels["bookmarks"].status == "skipped_recently"
    assert summary.channels["timeline"].accepted == 1


def test_sync_x_sources_persists_bookmark_progress_when_timeline_fails(
    db_session,
    test_user,
    monkeypatch,
):
    """Bookmark state should still persist when later channels fail."""
    connection = _build_connection(
        test_user,
        ["tweet.read", "users.read", "bookmark.read"],
    )
    db_session.add(connection)
    db_session.commit()

    monkeypatch.setattr(
        "app.services.x_integration._ensure_valid_access_token",
        lambda *_args, **_kwargs: "token",
    )
    monkeypatch.setattr(
        "app.services.x_integration._ensure_provider_user_id",
        lambda *_args, **_kwargs: "42",
    )
    monkeypatch.setattr(
        "app.services.x_integration.fetch_bookmarks",
        lambda **_kwargs: XTweetsPage(
            tweets=[_tweet("101", "Bookmark me")],
        ),
    )
    monkeypatch.setattr(
        "app.services.x_integration.fetch_reverse_chronological_timeline",
        lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("X API 401: Unauthorized: Unauthorized")
        ),
    )
    summary = sync_x_sources_for_user(db_session, user_id=test_user.id)

    sync_state = (
        db_session.query(UserIntegrationSyncState).filter_by(connection_id=connection.id).one()
    )

    assert summary.status == "failed"
    assert summary.channels["bookmarks"].accepted == 1
    assert summary.channels["timeline"].status == "failed"
    assert sync_state.last_status == "failed"
    assert sync_state.sync_metadata["bookmarks"]["last_synced_item_id"] == "101"
    assert "timeline" in (sync_state.last_error or "")


def test_sync_x_sources_skips_recent_scheduled_runs(db_session, test_user, monkeypatch):
    """Scheduled sync should no-op when the last run is still within the cooldown window."""
    connection = _build_connection(
        test_user,
        ["tweet.read", "users.read", "bookmark.read"],
    )
    db_session.add(connection)
    db_session.flush()
    db_session.add(
        UserIntegrationSyncState(
            connection_id=connection.id,
            last_status="success",
            last_synced_at=(datetime.now(UTC) - timedelta(minutes=5)).replace(tzinfo=None),
            sync_metadata={"timeline": {"last_synced_item_id": "123"}},
        )
    )
    db_session.commit()

    monkeypatch.setattr(get_settings(), "x_sync_min_interval_minutes", 60)
    ensure_token_mock = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("skip"))  # noqa: E731
    monkeypatch.setattr(
        "app.services.x_integration._ensure_valid_access_token",
        ensure_token_mock,
    )

    summary = sync_x_sources_for_user(db_session, user_id=test_user.id)

    assert summary.status == "skipped_recently"
    assert summary.fetched == 0
    assert summary.channels == {}


def test_ensure_valid_access_token_deactivates_connection_on_unrecoverable_refresh_error(
    db_session,
    test_user,
    monkeypatch,
):
    """Invalid refresh responses should disable the connection until reauth."""
    connection = _build_connection(
        test_user,
        ["tweet.read", "users.read", "bookmark.read", "offline.access"],
    )
    connection.token_expires_at = (datetime.now(UTC) - timedelta(minutes=5)).replace(tzinfo=None)
    db_session.add(connection)
    db_session.commit()

    monkeypatch.setattr("app.services.x_integration.decrypt_token", lambda _value: "refresh-token")
    monkeypatch.setattr(
        "app.services.x_integration.refresh_oauth_token",
        lambda refresh_token: (_ for _ in ()).throw(RuntimeError("X API 400: invalid_request")),
    )

    with pytest.raises(x_integration.XReauthRequiredError):
        x_integration._ensure_valid_access_token(db_session, connection)

    db_session.refresh(connection)
    assert connection.is_active is False
    assert connection.access_token_encrypted is None
    assert connection.refresh_token_encrypted is None
    assert connection.token_expires_at is None
    assert (
        connection.connection_metadata["reauth_required"]["reason"] == "X API 400: invalid_request"
    )


def test_sync_x_sources_returns_reauth_required_for_invalid_refresh(
    db_session,
    test_user,
    monkeypatch,
):
    """Expired broken connections should stop erroring and surface reauth-required status."""
    connection = _build_connection(
        test_user,
        ["tweet.read", "users.read", "bookmark.read", "offline.access"],
    )
    connection.token_expires_at = (datetime.now(UTC) - timedelta(minutes=5)).replace(tzinfo=None)
    db_session.add(connection)
    db_session.commit()

    monkeypatch.setattr("app.services.x_integration.decrypt_token", lambda _value: "refresh-token")
    monkeypatch.setattr(
        "app.services.x_integration.refresh_oauth_token",
        lambda refresh_token: (_ for _ in ()).throw(RuntimeError("X API 400: invalid_request")),
    )

    summary = sync_x_sources_for_user(db_session, user_id=test_user.id)

    sync_state = (
        db_session.query(UserIntegrationSyncState).filter_by(connection_id=connection.id).one()
    )
    db_session.refresh(connection)

    assert summary.status == "reauth_required"
    assert summary.channels == {}
    assert connection.is_active is False
    assert sync_state.last_status == "reauth_required"
    assert "reauthentication" in (sync_state.last_error or "")


def test_build_sync_metadata_payload_preserves_last_ids_when_run_is_empty() -> None:
    """Empty channel runs should not clear stored X cursors."""
    metadata = x_integration._build_sync_metadata_payload(
        existing_sync_metadata={
            "bookmarks": {"last_synced_item_id": "bookmark-1"},
            "timeline": {"last_synced_item_id": "timeline-1"},
        },
        bookmark_summary=x_integration.XSyncChannelSummary(
            status="success",
            fetched=0,
            accepted=0,
            filtered_out=0,
            errored=0,
            created=0,
            reused=0,
            newest_item_id=None,
        ),
        timeline_summary=x_integration.XSyncChannelSummary(
            status="success",
            fetched=0,
            accepted=0,
            filtered_out=0,
            errored=0,
            created=0,
            reused=0,
            newest_item_id=None,
        ),
    )

    assert metadata["bookmarks"]["last_synced_item_id"] == "bookmark-1"
    assert metadata["timeline"]["last_synced_item_id"] == "timeline-1"
    assert "lists" not in metadata
