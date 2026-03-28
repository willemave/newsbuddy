"""Tests for daily news digest API endpoints."""

from __future__ import annotations

from datetime import datetime

from app.models.schema import ChatMessage, ChatSession, Content, DailyNewsDigest
from app.models.user import User


def _create_digest(
    *,
    user_id: int,
    local_date: str,
    read_at: datetime | None = None,
    title: str = "Digest title",
    coverage_end_at: datetime | None = None,
    bullet_details: list[dict[str, object]] | None = None,
) -> DailyNewsDigest:
    return DailyNewsDigest(
        user_id=user_id,
        local_date=datetime.fromisoformat(local_date).date(),
        timezone="UTC",
        title=title,
        summary="Daily summary body.",
        key_points=["Point 1", "Point 2"],
        bullet_details=bullet_details or [],
        source_content_ids=[1, 2],
        source_count=2,
        llm_model="google:gemini-3.1-flash-lite-preview",
        generated_at=datetime(2026, 3, 1, 3, 0, 0),
        coverage_end_at=coverage_end_at,
        read_at=read_at,
    )


def test_list_daily_digests_defaults_to_unread(client, db_session, test_user) -> None:
    db_session.add_all(
        [
            _create_digest(user_id=test_user.id, local_date="2026-02-28"),
            _create_digest(user_id=test_user.id, local_date="2026-02-27"),
            _create_digest(
                user_id=test_user.id,
                local_date="2026-02-26",
                read_at=datetime(2026, 2, 27, 8, 0, 0),
            ),
        ]
    )
    db_session.commit()

    response = client.get("/api/content/daily-digests")
    assert response.status_code == 200
    payload = response.json()

    assert len(payload["digests"]) == 2
    assert payload["digests"][0]["local_date"] == "2026-02-28"
    assert payload["digests"][1]["local_date"] == "2026-02-27"
    assert all(digest["is_read"] is False for digest in payload["digests"])
    assert payload["digests"][0]["coverage_end_at"] is None


def test_list_daily_digests_all_filter_includes_read(client, db_session, test_user) -> None:
    db_session.add_all(
        [
            _create_digest(user_id=test_user.id, local_date="2026-02-28"),
            _create_digest(
                user_id=test_user.id,
                local_date="2026-02-27",
                read_at=datetime(2026, 2, 27, 8, 0, 0),
            ),
        ]
    )
    db_session.commit()

    response = client.get("/api/content/daily-digests", params={"read_filter": "all"})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["digests"]) == 2


def test_list_daily_digests_includes_checkpoint_coverage(client, db_session, test_user) -> None:
    digest = _create_digest(
        user_id=test_user.id,
        local_date="2026-02-28",
        coverage_end_at=datetime(2026, 2, 28, 6, 0, 0),
    )
    db_session.add(digest)
    db_session.commit()

    response = client.get("/api/content/daily-digests")
    assert response.status_code == 200
    payload = response.json()
    assert payload["digests"][0]["coverage_end_at"] == "2026-02-28T06:00:00Z"


def test_list_daily_digests_includes_source_labels(client, db_session, test_user) -> None:
    db_session.add_all(
        [
            Content(
                id=1,
                content_type="news",
                url="https://news.ycombinator.com/item?id=1",
                title="HN story",
                source="hackernews",
                platform="hackernews",
                status="completed",
                classification="to_read",
                content_metadata={},
            ),
            Content(
                id=2,
                content_type="news",
                url="https://x.com/swyx/status/1#newsly-digest-user-1",
                source_url="https://x.com/swyx/status/1",
                title="X post",
                source="X Following",
                platform="twitter",
                status="completed",
                classification="to_read",
                content_metadata={"tweet_author_username": "swyx", "source_label": "X Following"},
            ),
            _create_digest(user_id=test_user.id, local_date="2026-02-28"),
        ]
    )
    db_session.commit()

    response = client.get("/api/content/daily-digests")
    assert response.status_code == 200
    payload = response.json()
    assert payload["digests"][0]["source_labels"] == ["Hacker News", "@swyx"]


def test_list_daily_digests_includes_bullet_details(client, db_session, test_user) -> None:
    db_session.add_all(
        [
            Content(
                id=1,
                content_type="news",
                url="https://news.ycombinator.com/item?id=1",
                title="HN story",
                source="hackernews",
                platform="hackernews",
                status="completed",
                classification="to_read",
                content_metadata={"discussion_url": "https://news.ycombinator.com/item?id=1"},
            ),
            Content(
                id=2,
                content_type="news",
                url="https://x.com/swyx/status/1#newsly-digest-user-1",
                source_url="https://x.com/swyx/status/1",
                title="X post",
                source="X Following",
                platform="twitter",
                status="completed",
                classification="to_read",
                content_metadata={"tweet_author_username": "swyx", "source_label": "X Following"},
            ),
            _create_digest(
                user_id=test_user.id,
                local_date="2026-02-28",
                bullet_details=[
                    {
                        "text": "Point 1",
                        "source_content_ids": [1, 2],
                        "comment_quotes": ['"Quoted discussion comment" - alice'],
                    }
                ],
            ),
        ]
    )
    db_session.commit()

    response = client.get("/api/content/daily-digests")
    assert response.status_code == 200
    payload = response.json()

    bullet = payload["digests"][0]["bullet_details"][0]
    assert bullet["text"] == "Point 1"
    assert bullet["source_count"] == 2
    assert bullet["comment_quotes"] == ['"Quoted discussion comment" - alice']
    assert bullet["citations"] == [
        {
            "content_id": 1,
            "label": "Hacker News",
            "title": "HN story",
            "url": "https://news.ycombinator.com/item?id=1",
        },
        {
            "content_id": 2,
            "label": "@swyx",
            "title": "X post",
            "url": "https://x.com/swyx/status/1",
        },
    ]


def test_list_daily_digests_builds_fallback_bullet_details_for_legacy_digests(
    client,
    db_session,
    test_user,
) -> None:
    db_session.add_all(
        [
            Content(
                id=1,
                content_type="news",
                url="https://example.com/chips",
                title="Cloud Chip Demand",
                source="Example",
                platform="hackernews",
                status="completed",
                classification="to_read",
                content_metadata={
                    "summary": {
                        "title": "Cloud Chip Demand",
                        "key_points": ["Point 1"],
                    }
                },
            ),
            Content(
                id=2,
                content_type="news",
                url="https://example.com/policy",
                title="Policy Update",
                source="Example",
                platform="reddit",
                status="completed",
                classification="to_read",
                content_metadata={
                    "summary": {
                        "title": "Policy Update",
                        "key_points": ["Point 2"],
                    }
                },
            ),
            _create_digest(user_id=test_user.id, local_date="2026-02-28"),
        ]
    )
    db_session.commit()

    response = client.get("/api/content/daily-digests")
    assert response.status_code == 200
    payload = response.json()
    assert payload["digests"][0]["bullet_details"][0]["text"] == "Point 1"
    assert payload["digests"][0]["bullet_details"][0]["source_count"] == 1


def test_mark_daily_digest_read_and_unread(client, db_session, test_user) -> None:
    digest = _create_digest(user_id=test_user.id, local_date="2026-02-28")
    db_session.add(digest)
    db_session.commit()
    db_session.refresh(digest)

    mark_read_response = client.post(f"/api/content/daily-digests/{digest.id}/mark-read")
    assert mark_read_response.status_code == 200
    assert mark_read_response.json()["is_read"] is True

    db_session.refresh(digest)
    assert digest.read_at is not None

    mark_unread_response = client.delete(f"/api/content/daily-digests/{digest.id}/mark-unread")
    assert mark_unread_response.status_code == 200
    assert mark_unread_response.json()["is_read"] is False

    db_session.refresh(digest)
    assert digest.read_at is None


def test_daily_digest_voice_summary(client, db_session, test_user) -> None:
    digest = _create_digest(
        user_id=test_user.id,
        local_date="2026-02-28",
        title="2026-02-28",
    )
    db_session.add(digest)
    db_session.commit()
    db_session.refresh(digest)

    response = client.get(f"/api/content/narration/daily-digest/{digest.id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["target_type"] == "daily-digest"
    assert payload["target_id"] == digest.id
    assert "2026-02-28" not in payload["narration_text"]
    assert "Daily summary body." not in payload["narration_text"]
    assert "Point 1" in payload["narration_text"]


def test_daily_digest_voice_summary_audio(
    client,
    db_session,
    test_user,
    monkeypatch,
) -> None:
    digest = _create_digest(
        user_id=test_user.id,
        local_date="2026-02-28",
        title="2026-02-28",
    )
    db_session.add(digest)
    db_session.commit()
    db_session.refresh(digest)

    captured: dict[str, object] = {}

    class _FakeTtsService:
        def synthesize_mp3(self, *, text: str, item_id: int | None = None) -> bytes:
            captured["text"] = text
            captured["item_id"] = item_id
            return b"fake-mp3-bytes"

    monkeypatch.setattr(
        "app.routers.api.narration.get_digest_narration_tts_service",
        lambda: _FakeTtsService(),
    )

    response = client.get(
        f"/api/content/narration/daily-digest/{digest.id}",
        headers={"Accept": "audio/mpeg"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/mpeg")
    assert response.content == b"fake-mp3-bytes"
    assert captured["item_id"] == digest.id
    assert "Point 1" in str(captured["text"])


def test_daily_digest_voice_summary_audio_returns_404_for_other_user(
    client,
    db_session,
    test_user,
) -> None:
    other_user = User(
        apple_id="daily_digest_audio_other_user",
        email="digest-audio-other@example.com",
        full_name="Digest Audio Other",
        is_active=True,
    )
    db_session.add(other_user)
    db_session.commit()
    db_session.refresh(other_user)

    other_digest = _create_digest(user_id=other_user.id, local_date="2026-02-28")
    db_session.add(other_digest)
    db_session.commit()
    db_session.refresh(other_digest)

    response = client.get(
        f"/api/content/narration/daily-digest/{other_digest.id}",
        headers={"Accept": "audio/mpeg"},
    )
    assert response.status_code == 404


def test_daily_digest_voice_summary_audio_returns_503_when_tts_unavailable(
    client,
    db_session,
    test_user,
    monkeypatch,
) -> None:
    digest = _create_digest(user_id=test_user.id, local_date="2026-02-28")
    db_session.add(digest)
    db_session.commit()
    db_session.refresh(digest)

    class _FailingTtsService:
        def synthesize_mp3(self, *, text: str, item_id: int | None = None) -> bytes:
            raise ValueError("ElevenLabs API key is not configured")

    monkeypatch.setattr(
        "app.routers.api.narration.get_digest_narration_tts_service",
        lambda: _FailingTtsService(),
    )

    response = client.get(
        f"/api/content/narration/daily-digest/{digest.id}",
        headers={"Accept": "audio/mpeg"},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "ElevenLabs API key is not configured"


def test_daily_digest_endpoints_are_user_scoped(client, db_session, test_user) -> None:
    other_user = User(
        apple_id="daily_digest_other_user",
        email="digest-other@example.com",
        full_name="Digest Other",
        is_active=True,
    )
    db_session.add(other_user)
    db_session.commit()
    db_session.refresh(other_user)

    other_digest = _create_digest(user_id=other_user.id, local_date="2026-02-28")
    db_session.add(other_digest)
    db_session.commit()
    db_session.refresh(other_digest)

    response = client.post(f"/api/content/daily-digests/{other_digest.id}/mark-read")
    assert response.status_code == 404


def test_start_daily_digest_chat_creates_fresh_session_each_time(
    client,
    db_session,
    test_user,
    monkeypatch,
) -> None:
    digest = _create_digest(user_id=test_user.id, local_date="2026-02-28", title="Daily AI Digest")
    db_session.add(digest)
    db_session.commit()
    db_session.refresh(digest)

    captured: list[tuple[int, int, str]] = []

    async def _fake_process_message_async(session_id: int, message_id: int, prompt: str) -> None:
        captured.append((session_id, message_id, prompt))

    monkeypatch.setattr(
        "app.routers.api.daily_news_digests.process_message_async",
        _fake_process_message_async,
    )
    monkeypatch.setattr("app.routers.api.daily_news_digests.log_event", lambda *args, **kwargs: 0)

    first_response = client.post(f"/api/content/daily-digests/{digest.id}/dig-deeper")
    second_response = client.post(f"/api/content/daily-digests/{digest.id}/dig-deeper")

    assert first_response.status_code == 200
    assert second_response.status_code == 200

    first_payload = first_response.json()
    second_payload = second_response.json()

    assert first_payload["session"]["session_type"] == "daily_digest_brain"
    assert first_payload["session"]["content_id"] is None
    assert first_payload["session"]["title"] == "Daily AI Digest"
    assert first_payload["status"] == "processing"
    assert second_payload["session"]["id"] != first_payload["session"]["id"]

    sessions = (
        db_session.query(ChatSession)
        .filter(
            ChatSession.user_id == test_user.id,
            ChatSession.session_type == "daily_digest_brain",
        )
        .order_by(ChatSession.id.asc())
        .all()
    )
    assert len(sessions) == 2
    assert sessions[0].context_snapshot == "Digest bullets:\n- Point 1\n- Point 2"
    assert "Daily summary body." not in sessions[0].context_snapshot
    assert "source_content_ids" not in sessions[0].context_snapshot

    messages = (
        db_session.query(ChatMessage)
        .filter(ChatMessage.session_id.in_([sessions[0].id, sessions[1].id]))
        .order_by(ChatMessage.id.asc())
        .all()
    )
    assert len(messages) == 2
    assert all(message.status == "processing" for message in messages)
    assert len(captured) == 2
    assert captured[0][0] == sessions[0].id
    assert captured[1][0] == sessions[1].id
    assert "Dig deeper into these digest bullets." in captured[0][2]


def test_start_daily_digest_bullet_chat_scopes_context_to_selected_bullet(
    client,
    db_session,
    test_user,
    monkeypatch,
) -> None:
    db_session.add_all(
        [
            Content(
                id=1,
                content_type="news",
                url="https://news.ycombinator.com/item?id=1",
                title="HN story",
                source="hackernews",
                platform="hackernews",
                status="completed",
                classification="to_read",
                content_metadata={},
            ),
            _create_digest(
                user_id=test_user.id,
                local_date="2026-02-28",
                title="Daily AI Digest",
                bullet_details=[
                    {
                        "text": "Point 1",
                        "source_content_ids": [1],
                        "comment_quotes": ['"Quoted discussion comment" - alice'],
                    },
                    {
                        "text": "Point 2",
                        "source_content_ids": [],
                        "comment_quotes": [],
                    },
                ],
            ),
        ]
    )
    db_session.commit()

    digest = db_session.query(DailyNewsDigest).filter(DailyNewsDigest.user_id == test_user.id).one()
    captured: list[tuple[int, int, str]] = []

    async def _fake_process_message_async(session_id: int, message_id: int, prompt: str) -> None:
        captured.append((session_id, message_id, prompt))

    monkeypatch.setattr(
        "app.routers.api.daily_news_digests.process_message_async",
        _fake_process_message_async,
    )
    monkeypatch.setattr("app.routers.api.daily_news_digests.log_event", lambda *args, **kwargs: 0)

    response = client.post(f"/api/content/daily-digests/{digest.id}/bullets/0/dig-deeper")

    assert response.status_code == 200
    payload = response.json()
    assert payload["session"]["session_type"] == "daily_digest_brain"
    assert payload["session"]["topic"] == "Point 1"

    session = (
        db_session.query(ChatSession)
        .filter(ChatSession.user_id == test_user.id, ChatSession.id == payload["session"]["id"])
        .one()
    )
    assert "Selected digest bullet:\n- Point 1" in session.context_snapshot
    assert (
        "Linked sources:\n- Hacker News: HN story (https://news.ycombinator.com/item?id=1)"
        in session.context_snapshot
    )
    assert (
        'Stored discussion comments:\n- "Quoted discussion comment" - alice'
        in session.context_snapshot
    )
    assert "Point 2" not in session.context_snapshot
    assert "Dig deeper into this digest bullet." in captured[0][2]


def test_start_daily_digest_bullet_chat_returns_404_for_invalid_bullet_index(
    client,
    db_session,
    test_user,
    monkeypatch,
) -> None:
    digest = _create_digest(user_id=test_user.id, local_date="2026-02-28")
    db_session.add(digest)
    db_session.commit()
    db_session.refresh(digest)

    monkeypatch.setattr(
        "app.routers.api.daily_news_digests.process_message_async",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr("app.routers.api.daily_news_digests.log_event", lambda *args, **kwargs: 0)

    response = client.post(f"/api/content/daily-digests/{digest.id}/bullets/5/dig-deeper")

    assert response.status_code == 404
    assert response.json()["detail"] == "Daily digest bullet not found"


def test_start_daily_digest_chat_is_user_scoped(client, db_session, test_user, monkeypatch) -> None:
    other_user = User(
        apple_id="daily_digest_chat_other_user",
        email="digest-chat-other@example.com",
        full_name="Digest Chat Other",
        is_active=True,
    )
    db_session.add(other_user)
    db_session.commit()
    db_session.refresh(other_user)

    other_digest = _create_digest(user_id=other_user.id, local_date="2026-02-28")
    db_session.add(other_digest)
    db_session.commit()
    db_session.refresh(other_digest)

    monkeypatch.setattr(
        "app.routers.api.daily_news_digests.process_message_async",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr("app.routers.api.daily_news_digests.log_event", lambda *args, **kwargs: 0)

    response = client.post(f"/api/content/daily-digests/{other_digest.id}/dig-deeper")

    assert response.status_code == 404


def test_start_daily_digest_chat_requires_key_points(
    client,
    db_session,
    test_user,
    monkeypatch,
) -> None:
    digest = _create_digest(user_id=test_user.id, local_date="2026-02-28")
    digest.key_points = []
    db_session.add(digest)
    db_session.commit()
    db_session.refresh(digest)

    monkeypatch.setattr(
        "app.routers.api.daily_news_digests.process_message_async",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr("app.routers.api.daily_news_digests.log_event", lambda *args, **kwargs: 0)

    response = client.post(f"/api/content/daily-digests/{digest.id}/dig-deeper")

    assert response.status_code == 400
    assert response.json()["detail"] == "Daily digest dig-deeper requires summary bullets"
