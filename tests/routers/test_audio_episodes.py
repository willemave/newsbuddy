from __future__ import annotations

from app.models.contracts import TaskType
from app.models.db import AudioEpisode, ProcessingTask
from tests.support.builders import create_news_item_row


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


def test_stream_audio_episode_returns_generated_chunks(
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

    def fake_stream_audio_episode_chunks(*, audio_episode_id: int, user_id: int):
        assert audio_episode_id == episode.id
        assert user_id == test_user.id
        yield b"abc"
        yield b"def"

    monkeypatch.setattr(
        "app.routers.api.audio_episodes.stream_audio_episode_chunks",
        fake_stream_audio_episode_chunks,
    )

    response = client.get(f"/api/content/audio-episodes/{episode.id}/stream")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/mpeg")
    assert response.content == b"abcdef"
