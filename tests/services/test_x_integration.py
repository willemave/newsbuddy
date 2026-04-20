"""Tests for X integration sync flows."""

from datetime import UTC, datetime, timedelta

import pytest

import app.services.x_integration as x_integration
from app.core.settings import get_settings
from app.models.schema import (
    Content,
    NewsItem,
    UserIntegrationConnection,
    UserIntegrationSyncedItem,
    UserIntegrationSyncState,
)
from app.services.token_crypto import decrypt_token
from app.services.x_api import XTokenResponse, XTweet, XTweetsPage, XUser
from app.services.x_integration import (
    exchange_x_oauth,
    start_x_oauth,
    sync_x_sources_for_user,
)


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
    )


def test_start_x_oauth_persists_pending_state_and_sync_scopes(
    db_session,
    test_user,
    monkeypatch,
) -> None:
    """OAuth start should persist pending state and request bookmark sync scopes."""
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
        lambda access_token, **_kwargs: XUser(id="42", username="willemaw", name="Willem"),
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


def test_sync_x_sources_syncs_bookmarks_only(
    db_session,
    test_user,
    monkeypatch,
) -> None:
    """Bookmark sync should create bookmark-backed content rows without digest news ingestion."""
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
            tweets=[
                _tweet("101", "TSMC raises capex again as AI packaging demand stays tight."),
            ]
        ),
    )

    summary = sync_x_sources_for_user(db_session, user_id=test_user.id)

    assert summary.status == "success"
    assert set(summary.channels) == {"bookmarks"}
    assert summary.channels["bookmarks"].accepted == 1
    assert summary.channels["bookmarks"].created == 1

    content = db_session.query(Content).one()
    synced_item = db_session.query(UserIntegrationSyncedItem).one()
    assert content.url == "https://x.com/i/status/101"
    assert (content.content_metadata or {})["tweet_snapshot_source"] == "x_bookmarks_sync"
    assert synced_item.connection_id == connection.id
    assert synced_item.channel == "bookmarks"
    assert synced_item.external_item_id == "101"
    assert synced_item.content_id == content.id
    assert synced_item.item_url == "https://x.com/i/status/101"
    assert db_session.query(NewsItem).count() == 0


def test_sync_x_sources_skips_bookmarks_when_bookmark_channel_is_recent(
    db_session,
    test_user,
    monkeypatch,
):
    """Bookmark channel should respect its own throttle window."""
    connection = _build_connection(test_user, ["tweet.read", "users.read", "bookmark.read"])
    db_session.add(connection)
    db_session.flush()
    db_session.add(
        UserIntegrationSyncState(
            connection_id=connection.id,
            last_status="success",
            sync_metadata={
                "bookmarks": {"last_synced_at": datetime.now(UTC).isoformat()},
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

    summary = sync_x_sources_for_user(db_session, user_id=test_user.id)

    assert summary.status == "success"
    assert summary.channels["bookmarks"].status == "skipped_recently"


def test_sync_x_sources_persists_bookmark_progress(
    db_session,
    test_user,
    monkeypatch,
):
    """Bookmark state should persist even though there is no longer a timeline channel."""
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
    summary = sync_x_sources_for_user(db_session, user_id=test_user.id)

    sync_state = (
        db_session.query(UserIntegrationSyncState).filter_by(connection_id=connection.id).one()
    )

    assert summary.status == "success"
    assert summary.channels["bookmarks"].accepted == 1
    assert sync_state.last_status == "success"
    assert sync_state.sync_metadata["bookmarks"]["last_synced_item_id"] == "101"
    assert "timeline" not in sync_state.sync_metadata
    assert sync_state.last_error is None


def test_sync_x_sources_persists_bookmark_tweet_snapshot_for_later_resolution(
    db_session,
    test_user,
    monkeypatch,
):
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
    bookmark_pages = iter(
        [
            XTweetsPage(
                tweets=[
                    XTweet(
                        id="101",
                        text="Bookmark me",
                        author_id="42",
                        author_username="willem",
                        author_name="Willem",
                        created_at="2026-03-26T10:00:00Z",
                        like_count=12,
                        retweet_count=3,
                        reply_count=1,
                        conversation_id="101",
                        external_urls=["https://example.com/story"],
                        linked_tweet_ids=["202"],
                    )
                ],
                included_tweets={
                    "202": XTweet(
                        id="202",
                        text="Linked tweet body",
                        author_id="42",
                        author_username="willem",
                        author_name="Willem",
                        created_at="2026-03-26T10:01:00Z",
                        like_count=2,
                        retweet_count=1,
                        reply_count=0,
                        conversation_id="101",
                        external_urls=["https://example.com/linked"],
                    )
                },
                next_token="older",
            ),
            XTweetsPage(
                tweets=[_tweet("100", "Older bookmark")],
                included_tweets={},
            ),
        ]
    )
    monkeypatch.setattr(
        "app.services.x_integration.fetch_bookmarks",
        lambda **_kwargs: next(bookmark_pages),
    )
    summary = sync_x_sources_for_user(db_session, user_id=test_user.id)

    content = db_session.query(Content).filter(Content.url == "https://x.com/i/status/101").one()
    metadata = content.content_metadata or {}

    assert summary.status == "success"
    assert metadata["tweet_snapshot"]["id"] == "101"
    assert metadata["tweet_snapshot"]["external_urls"] == ["https://example.com/story"]
    assert metadata["tweet_snapshot"]["linked_tweet_ids"] == ["202"]
    assert metadata["tweet_snapshot_included"]["202"]["external_urls"] == [
        "https://example.com/linked"
    ]
    assert metadata["tweet_snapshot_source"] == "x_bookmarks_sync"


def test_sync_x_sources_records_synced_item_when_reusing_existing_content(
    db_session,
    test_user,
    monkeypatch,
) -> None:
    connection = _build_connection(
        test_user,
        ["tweet.read", "users.read", "bookmark.read"],
    )
    db_session.add(connection)
    db_session.flush()
    existing = Content(
        url="https://x.com/i/status/101",
        source_url="https://x.com/i/status/101",
        content_type="unknown",
        title=None,
        source="self",
        platform="twitter",
        is_aggregate=False,
        status="new",
        classification="to_read",
        content_metadata={
            "source": "self",
            "submitted_by_user_id": test_user.id,
            "submitted_via": "x_bookmarks",
            "platform_hint": "twitter",
        },
    )
    db_session.add(existing)
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
        lambda **_kwargs: XTweetsPage(tweets=[_tweet("101", "Bookmark me again")]),
    )

    summary = sync_x_sources_for_user(db_session, user_id=test_user.id)

    synced_item = db_session.query(UserIntegrationSyncedItem).one()
    assert summary.status == "success"
    assert summary.channels["bookmarks"].reused == 1
    assert synced_item.connection_id == connection.id
    assert synced_item.channel == "bookmarks"
    assert synced_item.external_item_id == "101"
    assert synced_item.content_id == existing.id
    assert synced_item.item_url == "https://x.com/i/status/101"


def test_sync_x_sources_reuses_existing_ledger_entry_without_resubmitting_content(
    db_session,
    test_user,
    monkeypatch,
) -> None:
    connection = _build_connection(
        test_user,
        ["tweet.read", "users.read", "bookmark.read"],
    )
    db_session.add(connection)
    db_session.flush()
    existing = Content(
        url="https://x.com/i/status/101",
        source_url="https://x.com/i/status/101",
        content_type="unknown",
        title=None,
        source="self",
        platform="twitter",
        is_aggregate=False,
        status="new",
        classification="to_read",
        content_metadata={
            "source": "self",
            "submitted_by_user_id": test_user.id,
            "submitted_via": "x_bookmarks",
            "platform_hint": "twitter",
        },
    )
    db_session.add(existing)
    db_session.flush()
    db_session.add(
        UserIntegrationSyncedItem(
            connection_id=connection.id,
            channel="bookmarks",
            external_item_id="101",
            content_id=existing.id,
            item_url="https://x.com/i/status/101",
            first_synced_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1),
            last_seen_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1),
        )
    )
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
        lambda **_kwargs: XTweetsPage(tweets=[_tweet("101", "Bookmark me again")]),
    )

    def fail_submit(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("existing ledger entry should skip content submission")

    monkeypatch.setattr(
        "app.services.x_integration.submit_user_content",
        fail_submit,
    )

    before_seen_at = (
        db_session.query(UserIntegrationSyncedItem.last_seen_at)
        .filter_by(connection_id=connection.id, channel="bookmarks", external_item_id="101")
        .scalar()
    )

    summary = sync_x_sources_for_user(db_session, user_id=test_user.id)

    synced_item = db_session.query(UserIntegrationSyncedItem).one()
    db_session.refresh(existing)

    assert summary.status == "success"
    assert summary.channels["bookmarks"].reused == 1
    assert synced_item.content_id == existing.id
    assert synced_item.last_seen_at > before_seen_at
    assert (existing.content_metadata or {})["tweet_snapshot_source"] == "x_bookmarks_sync"


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
            sync_metadata={"bookmarks": {"last_synced_item_id": "123"}},
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


def test_sync_x_sources_failure_does_not_consume_scheduled_retry_window(
    db_session,
    test_user,
    monkeypatch,
):
    """A failed scheduled run should not turn the queue retry into skipped_recently."""
    connection = _build_connection(
        test_user,
        ["tweet.read", "users.read", "bookmark.read"],
    )
    db_session.add(connection)
    db_session.commit()

    monkeypatch.setattr(get_settings(), "x_sync_min_interval_minutes", 60)
    monkeypatch.setattr(
        "app.services.x_integration._ensure_valid_access_token",
        lambda *_args, **_kwargs: "token",
    )
    monkeypatch.setattr(
        "app.services.x_integration._ensure_provider_user_id",
        lambda *_args, **_kwargs: "42",
    )

    attempts = iter(
        [
            RuntimeError("X API 400: invalid_request"),
            XTweetsPage(tweets=[_tweet("101", "Recovered bookmark")]),
        ]
    )

    def fake_fetch_bookmarks(**_kwargs):  # noqa: ANN003
        result = next(attempts)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(
        "app.services.x_integration.fetch_bookmarks",
        fake_fetch_bookmarks,
    )

    with pytest.raises(RuntimeError, match="invalid_request"):
        sync_x_sources_for_user(db_session, user_id=test_user.id)

    sync_state = (
        db_session.query(UserIntegrationSyncState).filter_by(connection_id=connection.id).one()
    )
    assert sync_state.last_status == "failed"
    assert sync_state.last_synced_at is None

    summary = sync_x_sources_for_user(db_session, user_id=test_user.id)

    db_session.refresh(sync_state)
    assert summary.status == "success"
    assert summary.channels["bookmarks"].accepted == 1
    assert sync_state.last_status == "success"
    assert sync_state.last_synced_at is not None


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
    )

    assert metadata["bookmarks"]["last_synced_item_id"] == "bookmark-1"
    assert "timeline" not in metadata
    assert "lists" not in metadata
