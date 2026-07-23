from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from app.models.contracts import ContentType, TaskType
from app.models.db import (
    AudioEpisode,
    BriefingLens,
    BriefingSegment,
    BriefingState,
    ContentReadStatus,
    NewsItemReadStatus,
    ProcessingTask,
)
from tests.support.builders import create_content_status_entry_row, create_news_item_row


def test_create_fast_news_audio_episode_enqueues_generation(
    client,
    db_session,
    test_user,
) -> None:
    create_news_item_row(
        db_session,
        visibility_scope="user",
        owner_user_id=test_user.id,
        summary_title="New model ships",
        summary_text="A new model shipped with faster audio.",
    )

    response = client.post("/api/content/audio-episodes/fast-news")

    assert response.status_code == 200
    payload = response.json()
    assert payload["kind"] == "fast_news_digest"
    assert payload["status"] == "pending"
    assert payload["audio_url"] is None
    assert payload["stream_url"] == f"/api/content/audio-episodes/{payload['id']}/stream"

    episode = db_session.query(AudioEpisode).one()
    task = db_session.query(ProcessingTask).one()
    assert task.task_type == TaskType.GENERATE_AUDIO_EPISODE.value
    assert task.payload == {"audio_episode_id": episode.id}
    assert task.queue_name == "audio_episode"


def test_create_fast_news_stream_delivery_does_not_enqueue_generation(
    client,
    db_session,
    test_user,
) -> None:
    create_news_item_row(
        db_session,
        visibility_scope="user",
        owner_user_id=test_user.id,
        summary_title="Streaming audio ships",
        summary_text="A stream-first episode should start without a queued worker.",
    )

    response = client.post("/api/content/audio-episodes/fast-news?delivery=stream")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "pending"
    assert payload["stream_url"] == f"/api/content/audio-episodes/{payload['id']}/stream"
    assert db_session.query(AudioEpisode).count() == 1
    assert db_session.query(ProcessingTask).count() == 0


def test_create_fast_news_inline_delivery_generates_before_response(
    client,
    db_session,
    test_user,
    monkeypatch,
) -> None:
    create_news_item_row(
        db_session,
        visibility_scope="user",
        owner_user_id=test_user.id,
        summary_title="Inline audio ships",
        summary_text="Inline delivery should return a completed file-backed episode.",
    )

    def fake_generate_audio_episode(db, *, audio_episode_id: int):
        episode = db.query(AudioEpisode).filter(AudioEpisode.id == audio_episode_id).one()
        episode.status = "completed"
        episode.audio_storage_path = "/tmp/audio-episode-test.mp3"
        episode.audio_content_type = "audio/mpeg"
        episode.duration_seconds = 90
        return episode

    monkeypatch.setattr(
        "app.services.audio_episodes.presentation.generate_audio_episode",
        fake_generate_audio_episode,
    )

    response = client.post("/api/content/audio-episodes/fast-news?delivery=inline")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["audio_url"] == f"/api/content/audio-episodes/{payload['id']}/audio"
    assert payload["duration_seconds"] == 90
    assert db_session.query(AudioEpisode).count() == 1
    assert db_session.query(ProcessingTask).count() == 0


def test_create_news_item_audio_episode_stream_delivery_does_not_enqueue_generation(
    client,
    db_session,
    test_user,
) -> None:
    item = create_news_item_row(
        db_session,
        visibility_scope="user",
        owner_user_id=test_user.id,
        summary_title="News detail audio ships",
        summary_text="The detail view should use a streaming podcast episode.",
    )

    response = client.post(f"/api/news/items/{item.id}/audio-episodes/discussion?delivery=stream")

    assert response.status_code == 200
    payload = response.json()
    assert payload["kind"] == "news_item_discussion"
    assert payload["status"] == "pending"
    assert payload["source_item_ids"] == [item.id]
    assert payload["stream_url"] == f"/api/content/audio-episodes/{payload['id']}/stream"
    assert db_session.query(AudioEpisode).count() == 1
    assert db_session.query(ProcessingTask).count() == 0


def test_create_custom_narration_audio_episode_enqueues_generation(
    client,
    db_session,
    test_user,
    content_factory,
) -> None:
    article = content_factory(
        content_type=ContentType.ARTICLE,
        title="Agent workflows",
        content_metadata={"content": "Full article body about agent workflows."},
    )
    podcast = content_factory(
        content_type=ContentType.PODCAST,
        title="Infra podcast",
        content_metadata={"transcript": "Podcast transcript about infrastructure."},
    )
    create_content_status_entry_row(db_session, user=test_user, content=article)
    create_content_status_entry_row(db_session, user=test_user, content=podcast)

    response = client.post(
        "/api/content/audio-episodes/custom-narrations",
        json={
            "content_ids": [article.id, podcast.id],
            "title": "Agent infra narration",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["kind"] == "custom_narration"
    assert payload["status"] == "pending"
    assert payload["title"] == "Agent infra narration"
    assert payload["source_content_ids"] == [article.id, podcast.id]
    assert payload["source_count"] == 2
    assert payload["source_titles"] == ["Agent workflows", "Infra podcast"]
    assert payload["stream_url"] == f"/api/content/audio-episodes/{payload['id']}/stream"

    episode = db_session.query(AudioEpisode).filter(AudioEpisode.kind == "custom_narration").one()
    task = db_session.query(ProcessingTask).one()
    assert task.task_type == TaskType.GENERATE_AUDIO_EPISODE.value
    assert task.payload == {"audio_episode_id": episode.id}
    assert task.queue_name == "audio_episode"


def test_create_custom_narration_audio_episode_accepts_fast_reads(
    client,
    db_session,
    test_user,
    content_factory,
) -> None:
    article = content_factory(
        content_type=ContentType.ARTICLE,
        title="Agent workflows",
        content_metadata={"content": "Full article body about agent workflows."},
    )
    news_item = create_news_item_row(
        db_session,
        visibility_scope="user",
        owner_user_id=test_user.id,
        summary_title="AI labs ship new browsers",
        summary_text="AI labs are moving agent browsers into products.",
    )
    create_content_status_entry_row(db_session, user=test_user, content=article)

    response = client.post(
        "/api/content/audio-episodes/custom-narrations",
        json={
            "content_ids": [article.id],
            "news_item_ids": [news_item.id],
            "title": "Mixed narration",
            "mark_source_content_read_on_play": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["kind"] == "custom_narration"
    assert payload["source_content_ids"] == [article.id]
    assert payload["source_item_ids"] == [news_item.id]
    assert payload["source_count"] == 2
    assert payload["read_on_play_content_ids"] == [article.id]
    assert payload["read_on_play_news_item_ids"] == [news_item.id]


def test_custom_narration_marks_sources_read_when_audio_is_played(
    client,
    db_session,
    test_user,
    content_factory,
    monkeypatch,
    tmp_path,
) -> None:
    audio_path = tmp_path / "narration.mp3"
    audio_path.write_bytes(b"mp3")
    article = content_factory(
        content_type=ContentType.ARTICLE,
        title="Agent workflows",
        content_metadata={"content": "Full article body about agent workflows."},
    )
    news_item = create_news_item_row(
        db_session,
        visibility_scope="user",
        owner_user_id=test_user.id,
        summary_title="AI labs ship new browsers",
        summary_text="AI labs are moving agent browsers into products.",
    )
    create_content_status_entry_row(db_session, user=test_user, content=article)

    def fake_generate_audio_episode(db, *, audio_episode_id: int):
        episode = db.query(AudioEpisode).filter(AudioEpisode.id == audio_episode_id).one()
        episode.status = "completed"
        episode.audio_storage_path = str(audio_path)
        episode.audio_content_type = "audio/mpeg"
        episode.duration_seconds = 90
        return episode

    monkeypatch.setattr(
        "app.services.audio_episodes.presentation.generate_audio_episode",
        fake_generate_audio_episode,
    )

    response = client.post(
        "/api/content/audio-episodes/custom-narrations?delivery=inline",
        json={
            "content_ids": [article.id],
            "news_item_ids": [news_item.id],
            "mark_source_content_read_on_play": True,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["audio_url"] is not None
    assert db_session.query(ContentReadStatus).count() == 0
    assert db_session.query(NewsItemReadStatus).count() == 0

    audio_response = client.get(payload["audio_url"])

    assert audio_response.status_code == 200
    assert audio_response.content == b"mp3"
    content_read = (
        db_session.query(ContentReadStatus)
        .filter(
            ContentReadStatus.user_id == test_user.id,
            ContentReadStatus.content_id == article.id,
        )
        .one_or_none()
    )
    news_read = (
        db_session.query(NewsItemReadStatus)
        .filter(
            NewsItemReadStatus.user_id == test_user.id,
            NewsItemReadStatus.news_item_id == news_item.id,
        )
        .one_or_none()
    )
    assert content_read is not None
    assert news_read is not None


def test_briefing_narration_marks_sources_only_when_playback_finishes(
    client,
    db_session,
    test_user,
    content_factory,
    tmp_path,
) -> None:
    audio_path = tmp_path / "briefing.mp3"
    audio_path.write_bytes(b"mp3")
    article = content_factory(
        content_type=ContentType.ARTICLE,
        title="Finish-triggered article",
        content_metadata={"content": "The article narration."},
    )
    lens = BriefingLens(
        user_id=test_user.id,
        key="articles",
        tier="longform",
        title="Articles",
        status="active",
    )
    db_session.add(lens)
    db_session.flush()
    segment = BriefingSegment(
        lens_id=lens.id,
        user_id=test_user.id,
        blocks=[],
        markdown_raw="The article narration.",
        narration_text="The article narration.",
        source_keys=[f"content:{article.id}"],
        status="active",
        model="test:model",
        prompt_version="test",
    )
    episode = AudioEpisode(
        user_id=test_user.id,
        kind="briefing_narration",
        status="completed",
        title="Articles briefing",
        input_hash="finish-triggered-briefing",
        source_item_ids=[],
        source_snapshot={
            "kind": "briefing_narration",
            "source_keys": [f"content:{article.id}"],
            "read_on_play": {
                "content_ids": [article.id],
                "news_item_ids": [],
            },
        },
        prompt_version=2,
        audio_storage_path=str(audio_path),
    )
    db_session.add_all([segment, episode])
    db_session.commit()

    audio_response = client.get(f"/api/content/audio-episodes/{episode.id}/audio")
    stream_response = client.get(f"/api/content/audio-episodes/{episode.id}/stream")

    assert audio_response.status_code == 200
    assert stream_response.status_code == 200
    assert db_session.query(ContentReadStatus).count() == 0
    db_session.refresh(segment)
    assert segment.status == "active"

    finished_response = client.post(f"/api/content/audio-episodes/{episode.id}/playback-finished")

    assert finished_response.status_code == 200
    assert finished_response.json()["id"] == episode.id
    assert (
        db_session.query(ContentReadStatus)
        .filter_by(user_id=test_user.id, content_id=article.id)
        .one_or_none()
        is not None
    )
    db_session.refresh(segment)
    assert segment.status == "retired"
    state = db_session.query(BriefingState).filter_by(user_id=test_user.id).one()
    assert state.version == 1

    repeated_response = client.post(f"/api/content/audio-episodes/{episode.id}/playback-finished")

    assert repeated_response.status_code == 200
    db_session.refresh(state)
    assert state.version == 1


def test_custom_narration_playback_marks_fast_reads_but_not_long_reads_by_default(
    client,
    db_session,
    test_user,
    content_factory,
    monkeypatch,
    tmp_path,
) -> None:
    audio_path = tmp_path / "narration.mp3"
    audio_path.write_bytes(b"mp3")
    article = content_factory(
        content_type=ContentType.ARTICLE,
        title="Agent workflows",
        content_metadata={"content": "Full article body about agent workflows."},
    )
    news_item = create_news_item_row(
        db_session,
        visibility_scope="user",
        owner_user_id=test_user.id,
        summary_title="AI labs ship new browsers",
        summary_text="AI labs are moving agent browsers into products.",
    )
    create_content_status_entry_row(db_session, user=test_user, content=article)

    def fake_generate_audio_episode(db, *, audio_episode_id: int):
        episode = db.query(AudioEpisode).filter(AudioEpisode.id == audio_episode_id).one()
        episode.status = "completed"
        episode.audio_storage_path = str(audio_path)
        episode.audio_content_type = "audio/mpeg"
        episode.duration_seconds = 90
        return episode

    monkeypatch.setattr(
        "app.services.audio_episodes.presentation.generate_audio_episode",
        fake_generate_audio_episode,
    )

    response = client.post(
        "/api/content/audio-episodes/custom-narrations?delivery=inline",
        json={"content_ids": [article.id], "news_item_ids": [news_item.id]},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["read_on_play_content_ids"] == []
    assert payload["read_on_play_news_item_ids"] == [news_item.id]

    audio_response = client.get(payload["audio_url"])

    assert audio_response.status_code == 200
    assert db_session.query(ContentReadStatus).count() == 0
    assert (
        db_session.query(NewsItemReadStatus)
        .filter(
            NewsItemReadStatus.user_id == test_user.id,
            NewsItemReadStatus.news_item_id == news_item.id,
        )
        .one_or_none()
        is not None
    )


def test_custom_narration_public_share_page_audio_and_revoke(
    client,
    db_session,
    test_user,
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.core.external_urls.get_settings",
        lambda: SimpleNamespace(public_base_url="https://public.example.com"),
    )
    audio_path = tmp_path / "shared-narration.mp3"
    audio_path.write_bytes(b"shared-mp3")
    episode = AudioEpisode(
        user_id=test_user.id,
        kind="custom_narration",
        status="completed",
        title="Shared narration",
        input_hash="share",
        source_item_ids=[],
        source_snapshot={
            "kind": "custom_narration",
            "content_ids": [],
            "news_item_ids": [],
            "source_count": 1,
            "items": [{"title": "Shared narration"}],
        },
        prompt_version=1,
        audio_storage_path=str(audio_path),
    )
    db_session.add(episode)
    db_session.commit()

    share_response = client.post(f"/api/content/audio-episodes/{episode.id}/share")

    assert share_response.status_code == 200
    payload = share_response.json()
    assert payload["share_enabled"] is True
    assert payload["share_page_url"].startswith("https://public.example.com/audio/share/")
    assert payload["share_audio_url"].startswith("https://public.example.com/audio/share/")

    page_response = client.get(payload["share_page_url"])
    assert page_response.status_code == 200
    assert "Shared narration" in page_response.text
    assert "<audio controls" in page_response.text
    assert payload["share_audio_url"] in page_response.text

    audio_response = client.get(payload["share_audio_url"])
    assert audio_response.status_code == 200
    assert audio_response.content == b"shared-mp3"

    disable_response = client.delete(f"/api/content/audio-episodes/{episode.id}/share")
    assert disable_response.status_code == 200
    assert disable_response.json() == {
        "share_enabled": False,
        "share_page_url": None,
        "share_audio_url": None,
    }
    assert client.get(payload["share_page_url"]).status_code == 404
    assert client.get(payload["share_audio_url"]).status_code == 404

    reshare_response = client.post(f"/api/content/audio-episodes/{episode.id}/share")
    assert reshare_response.status_code == 200
    reshare_payload = reshare_response.json()
    assert reshare_payload["share_enabled"] is True
    assert reshare_payload["share_page_url"] != payload["share_page_url"]
    assert client.get(payload["share_page_url"]).status_code == 404
    assert client.get(reshare_payload["share_page_url"]).status_code == 200


def test_list_custom_narration_audio_episodes_is_user_scoped(
    client,
    db_session,
    test_user,
    user_factory,
) -> None:
    other_user = user_factory(email="other-audio@example.com")
    visible = AudioEpisode(
        user_id=test_user.id,
        kind="custom_narration",
        status="completed",
        title="Visible narration",
        input_hash="visible",
        source_item_ids=[],
        source_snapshot={
            "kind": "custom_narration",
            "content_ids": [10, 11],
            "source_count": 2,
            "items": [{"content_id": 10, "title": "First"}],
        },
        prompt_version=1,
        audio_storage_path="/tmp/visible.mp3",
    )
    hidden = AudioEpisode(
        user_id=other_user.id,
        kind="custom_narration",
        status="completed",
        title="Hidden narration",
        input_hash="hidden",
        source_item_ids=[],
        source_snapshot={"kind": "custom_narration", "content_ids": [99], "source_count": 1},
        prompt_version=1,
    )
    db_session.add_all([visible, hidden])
    db_session.commit()

    response = client.get("/api/content/audio-episodes/custom-narrations")

    assert response.status_code == 200
    payload = response.json()
    assert [episode["title"] for episode in payload] == ["Visible narration"]
    assert payload[0]["source_content_ids"] == [10, 11]
    assert payload[0]["source_count"] == 2
    assert payload[0]["audio_url"] == f"/api/content/audio-episodes/{visible.id}/audio"


def test_stream_audio_episode_enqueues_and_follows_generation(
    client,
    db_session,
    test_user,
    monkeypatch,
) -> None:
    episode = AudioEpisode(
        user_id=test_user.id,
        kind="fast_news_digest",
        status="pending",
        title="Fast Reads Brief",
        input_hash="abc",
        source_item_ids=[1],
        source_snapshot={"kind": "fast_news_digest", "items": []},
        prompt_version=1,
    )
    db_session.add(episode)
    db_session.commit()
    db_session.refresh(episode)

    enqueued_ids: list[int] = []

    def fake_enqueue_audio_episode_generation(audio_episode_id: int) -> int:
        enqueued_ids.append(audio_episode_id)
        return 42

    def fake_follow_audio_episode_stream_chunks(*, audio_episode_id: int, user_id: int):
        assert audio_episode_id == episode.id
        assert user_id == test_user.id
        yield b"abc"
        yield b"def"

    monkeypatch.setattr(
        "app.routers.api.audio_episodes.enqueue_audio_episode_generation",
        fake_enqueue_audio_episode_generation,
    )
    monkeypatch.setattr(
        "app.routers.api.audio_episodes.follow_audio_episode_stream_chunks",
        fake_follow_audio_episode_stream_chunks,
    )

    response = client.get(f"/api/content/audio-episodes/{episode.id}/stream")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/mpeg")
    assert response.content == b"abcdef"
    assert enqueued_ids == [episode.id]


def test_stream_audio_episode_follows_active_generation(
    client,
    db_session,
    test_user,
    monkeypatch,
) -> None:
    episode = AudioEpisode(
        user_id=test_user.id,
        kind="fast_news_digest",
        status="processing",
        title="Fast Reads Brief",
        input_hash="active",
        source_item_ids=[1],
        source_snapshot={"kind": "fast_news_digest", "items": []},
        prompt_version=1,
        started_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db_session.add(episode)
    db_session.commit()
    db_session.refresh(episode)

    def fake_follow_audio_episode_stream_chunks(*, audio_episode_id: int, user_id: int):
        assert audio_episode_id == episode.id
        assert user_id == test_user.id
        yield b"cached"

    monkeypatch.setattr(
        "app.routers.api.audio_episodes.follow_audio_episode_stream_chunks",
        fake_follow_audio_episode_stream_chunks,
    )

    response = client.get(f"/api/content/audio-episodes/{episode.id}/stream")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/mpeg")
    assert response.content == b"cached"
