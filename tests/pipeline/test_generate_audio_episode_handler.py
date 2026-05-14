from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import Mock

from app.models.db import AudioEpisode
from app.pipeline.handlers.generate_audio_episode import GenerateAudioEpisodeHandler
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope
from app.services import audio_episodes
from app.services.queue import QueueService, TaskType


def test_generate_audio_episode_handler_commits_failed_episode_state(
    db_session,
    db_session_factory,
    monkeypatch,
) -> None:
    episode = AudioEpisode(
        user_id=123,
        kind=audio_episodes.FAST_NEWS_DIGEST_KIND,
        status="pending",
        title="Fast Reads Brief",
        input_hash="abc",
        source_item_ids=[1],
        source_snapshot={"kind": audio_episodes.FAST_NEWS_DIGEST_KIND, "items": []},
        prompt_version=audio_episodes.PROMPT_VERSION,
    )
    db_session.add(episode)
    db_session.commit()
    db_session.refresh(episode)

    @contextmanager
    def db_factory():
        session = db_session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def fail_script(_episode):
        raise RuntimeError("script boom")

    monkeypatch.setattr(audio_episodes, "_generate_script", fail_script)

    context = TaskContext(
        queue_service=QueueService(),
        settings=Mock(),
        llm_service=None,
        worker_id="test-worker",
        db_factory=db_factory,
    )
    task = TaskEnvelope(
        id=1,
        task_type=TaskType.GENERATE_AUDIO_EPISODE,
        payload={"audio_episode_id": episode.id},
    )

    result = GenerateAudioEpisodeHandler().handle(task, context)

    assert result.success is False
    assert result.error_message == "script boom"
    db_session.expire_all()
    persisted = db_session.query(AudioEpisode).filter(AudioEpisode.id == episode.id).one()
    assert persisted.status == "failed"
    assert persisted.error_message == "script boom"
