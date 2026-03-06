"""Tests for daily news digest API endpoints."""

from __future__ import annotations

from datetime import datetime

from app.models.schema import DailyNewsDigest
from app.models.user import User


def _create_digest(
    *,
    user_id: int,
    local_date: str,
    read_at: datetime | None = None,
    title: str = "Digest title",
) -> DailyNewsDigest:
    return DailyNewsDigest(
        user_id=user_id,
        local_date=datetime.fromisoformat(local_date).date(),
        timezone="UTC",
        title=title,
        summary="Daily summary body.",
        key_points=["Point 1", "Point 2"],
        source_content_ids=[1, 2],
        source_count=2,
        llm_model="google-gla:gemini-flash-latest",
        generated_at=datetime(2026, 3, 1, 3, 0, 0),
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

    response = client.get(f"/api/content/daily-digests/{digest.id}/voice-summary")
    assert response.status_code == 200
    payload = response.json()
    assert payload["digest_id"] == digest.id
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
        "app.routers.api.daily_news_digests.get_digest_narration_tts_service",
        lambda: _FakeTtsService(),
    )

    response = client.get(f"/api/content/daily-digests/{digest.id}/voice-summary/audio")
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

    response = client.get(f"/api/content/daily-digests/{other_digest.id}/voice-summary/audio")
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
        "app.routers.api.daily_news_digests.get_digest_narration_tts_service",
        lambda: _FailingTtsService(),
    )

    response = client.get(f"/api/content/daily-digests/{digest.id}/voice-summary/audio")
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
