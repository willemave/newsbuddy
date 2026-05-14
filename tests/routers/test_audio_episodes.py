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
        summary_title="New model ships",
        summary_text="A new model shipped with faster audio.",
    )

    response = client.post("/api/content/audio-episodes/fast-news")

    assert response.status_code == 200
    payload = response.json()
    assert payload["kind"] == "fast_news_digest"
    assert payload["status"] == "pending"
    assert payload["audio_url"] is None

    episode = db_session.query(AudioEpisode).one()
    task = db_session.query(ProcessingTask).one()
    assert task.task_type == TaskType.GENERATE_AUDIO_EPISODE.value
    assert task.payload == {"audio_episode_id": episode.id}
    assert task.queue_name == "audio_episode"
