"""Tests for X integration sync flows."""


from app.constants import CONTENT_DIGEST_VISIBILITY_DIGEST_ONLY, CONTENT_STATUS_DIGEST_SOURCE
from app.core.settings import get_settings
from app.models.schema import (
    Content,
    ContentStatusEntry,
    UserIntegrationConnection,
    UserIntegrationSyncState,
)
from app.services.token_crypto import decrypt_token
from app.services.x_api import XList, XListsPage, XTokenResponse, XTweet, XTweetsPage, XUser
from app.services.x_digest_filter import XDigestFilterDecision
from app.services.x_integration import exchange_x_oauth, start_x_oauth, sync_x_sources_for_user


class _FakeQueueGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int | None]] = []

    def enqueue(self, task_type, *, content_id=None, payload=None, queue_name=None, dedupe=None):  # noqa: ANN001
        self.calls.append((task_type.value, content_id))
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


def test_start_x_oauth_persists_pending_state_and_expanded_scopes(
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
    assert "follows.read" in scopes
    assert "list.read" in scopes
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
        scopes=["tweet.read", "users.read", "bookmark.read", "follows.read", "list.read"],
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
            scopes=["tweet.read", "users.read", "bookmark.read", "follows.read", "list.read"],
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
    """Timeline sync should create digest-only news content and user-scoped status rows."""
    connection = _build_connection(
        test_user,
        ["tweet.read", "users.read", "bookmark.read", "list.read"],
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
    monkeypatch.setattr(
        "app.services.x_integration.fetch_owned_lists",
        lambda **_kwargs: XListsPage(lists=[]),
    )
    monkeypatch.setattr(
        "app.services.x_integration.fetch_followed_lists",
        lambda **_kwargs: XListsPage(lists=[]),
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

    content = db_session.query(Content).one()
    assert content.url.endswith(f"#newsly-digest-user-{test_user.id}")
    assert content.source_url == "https://x.com/i/status/101"
    assert content.content_metadata["digest_visibility"] == CONTENT_DIGEST_VISIBILITY_DIGEST_ONLY
    assert content.content_metadata["source_type"] == "x_timeline"
    assert content.content_metadata["filter_score"] == 0.91
    assert content.content_metadata["submitted_by_user_id"] == test_user.id

    status_row = db_session.query(ContentStatusEntry).one()
    assert status_row.user_id == test_user.id
    assert status_row.status == CONTENT_STATUS_DIGEST_SOURCE
    assert recorded_prompts
    assert queue_gateway.calls == [
        ("process_content", content.id),
        ("fetch_discussion", content.id),
    ]


def test_sync_x_sources_filters_lists_and_merges_list_state(db_session, test_user, monkeypatch):
    """List sync should respect the filter result and persist per-list state."""
    test_user.x_digest_filter_prompt = "Prefer semiconductor manufacturing and datacenter infra."
    connection = _build_connection(
        test_user,
        ["tweet.read", "users.read", "bookmark.read", "follows.read", "list.read"],
    )
    db_session.add(connection)
    db_session.flush()
    db_session.add(
        UserIntegrationSyncState(
            connection_id=connection.id,
            last_status="success",
            sync_metadata={"lists": {"list_states": {"legacy": {"name": "Legacy"}}}},
        )
    )
    db_session.commit()

    queue_gateway = _FakeQueueGateway()
    seen_prompts: list[str] = []

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
        lambda **_kwargs: XTweetsPage(tweets=[]),
    )
    monkeypatch.setattr(
        "app.services.x_integration.fetch_owned_lists",
        lambda **_kwargs: XListsPage(lists=[XList(id="55", name="Semis")]),
    )
    monkeypatch.setattr(
        "app.services.x_integration.fetch_followed_lists",
        lambda **_kwargs: XListsPage(lists=[]),
    )
    monkeypatch.setattr(
        "app.services.x_integration.fetch_list_tweets",
        lambda **_kwargs: XTweetsPage(
            tweets=[
                _tweet("201", "ASML order visibility improved again this quarter."),
                _tweet("202", "random joke tweet"),
            ]
        ),
    )

    def fake_score_x_digest_candidate(*, tweet, user_prompt, source_type, source_label):  # noqa: ANN001
        seen_prompts.append(user_prompt)
        if tweet.id == "202":
            return XDigestFilterDecision(score=0.2, reason="Low-signal joke.", accepted=False)
        return XDigestFilterDecision(score=0.88, reason="Matches infra filter.", accepted=True)

    monkeypatch.setattr(
        "app.services.x_integration.score_x_digest_candidate",
        fake_score_x_digest_candidate,
    )
    monkeypatch.setattr("app.services.x_integration.get_task_queue_gateway", lambda: queue_gateway)

    summary = sync_x_sources_for_user(db_session, user_id=test_user.id)

    assert summary.channels["lists"].accepted == 1
    assert summary.channels["lists"].filtered_out == 1
    assert any("semiconductor manufacturing" in prompt for prompt in seen_prompts)

    sync_state = (
        db_session.query(UserIntegrationSyncState)
        .filter_by(connection_id=connection.id)
        .one()
    )
    list_states = sync_state.sync_metadata["lists"]["list_states"]
    assert "legacy" in list_states
    assert list_states["55"]["last_synced_item_id"] == "201"


def test_sync_x_sources_marks_missing_list_scopes_without_failing_bookmarks(
    db_session,
    test_user,
    monkeypatch,
):
    """Missing list scopes should degrade only the list channel."""
    connection = _build_connection(test_user, ["tweet.read", "users.read", "bookmark.read"])
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
        lambda **_kwargs: XTweetsPage(tweets=[]),
    )
    monkeypatch.setattr(
        "app.services.x_integration.fetch_reverse_chronological_timeline",
        lambda **_kwargs: XTweetsPage(tweets=[]),
    )
    monkeypatch.setattr(
        "app.services.x_integration.get_task_queue_gateway",
        lambda: _FakeQueueGateway(),
    )

    summary = sync_x_sources_for_user(db_session, user_id=test_user.id)

    assert summary.status == "success_with_warnings"
    assert summary.channels["lists"].status == "missing_scopes"
