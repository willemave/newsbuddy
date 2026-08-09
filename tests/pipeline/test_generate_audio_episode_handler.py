from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import cast

import pytest

from app.core.settings import Settings
from app.models.db import AudioEpisode
from app.pipeline.handlers.generate_audio_episode import GenerateAudioEpisodeHandler
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope
from app.services import audio_episodes
from app.services.audio_episodes import AudioEpisodeInputError, scripting
from app.services.queue import QueueService, TaskType
from app.services.voice.narration_tts import NarrationTtsConfigurationError


def test_generate_audio_episode_handler_keeps_episode_pending_for_retry(
    db_session,
    db_session_factory,
    monkeypatch,
    user_factory,
) -> None:
    user = user_factory()
    episode = AudioEpisode(
        user_id=user.id,
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

    monkeypatch.setattr(scripting, "generate_script", fail_script)

    context = TaskContext(
        queue_service=QueueService(),
        settings=cast(Settings, SimpleNamespace(queue=SimpleNamespace(max_retries=3))),
        llm_service=None,
        worker_id="test-worker",
        db_factory=db_factory,
    )
    task = TaskEnvelope(
        id=1,
        task_type=TaskType.GENERATE_AUDIO_EPISODE,
        payload={"audio_episode_id": episode.id, "user_id": user.id},
    )

    result = GenerateAudioEpisodeHandler().handle(task, context)

    assert result.success is False
    assert result.error_message == "script boom"
    assert result.retryable is True
    db_session.expire_all()
    persisted = db_session.query(AudioEpisode).filter(AudioEpisode.id == episode.id).one()
    assert persisted.status == "pending"
    assert persisted.error_message == "script boom"


def test_generate_audio_episode_reclaim_budget_exhaustion_terminalizes_without_provider(
    db_session,
    db_session_factory,
    monkeypatch,
    user_factory,
) -> None:
    user = user_factory()
    episode = AudioEpisode(
        user_id=user.id,
        kind=audio_episodes.FAST_NEWS_DIGEST_KIND,
        status="pending",
        title="Fast Reads Brief",
        input_hash="reclaim-budget",
        source_item_ids=[1],
        source_snapshot={"kind": audio_episodes.FAST_NEWS_DIGEST_KIND, "items": []},
        prompt_version=audio_episodes.PROMPT_VERSION,
    )
    db_session.add(episode)
    db_session.commit()

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

    monkeypatch.setattr(
        scripting,
        "generate_script",
        lambda *_args: pytest.fail("exhausted reclaim must not call the provider"),
    )
    context = TaskContext(
        queue_service=QueueService(),
        settings=cast(Settings, SimpleNamespace(queue=SimpleNamespace(max_retries=3))),
        llm_service=None,
        worker_id="test-worker",
        db_factory=db_factory,
    )

    result = GenerateAudioEpisodeHandler().handle(
        TaskEnvelope(
            id=2,
            task_type=TaskType.GENERATE_AUDIO_EPISODE,
            payload={"audio_episode_id": episode.id, "user_id": user.id},
            retry_count=4,
        ),
        context,
    )

    assert result.success is False
    assert result.retryable is False
    db_session.expire_all()
    persisted = db_session.get(AudioEpisode, episode.id)
    assert persisted.status == "failed"
    assert "worker interruptions" in persisted.error_message


def test_generate_audio_episode_exhausted_redelivery_preserves_completed_artifact(
    db_session,
    db_session_factory,
    monkeypatch,
    user_factory,
) -> None:
    user = user_factory()
    episode = AudioEpisode(
        user_id=user.id,
        kind=audio_episodes.FAST_NEWS_DIGEST_KIND,
        status="completed",
        title="Finished Brief",
        input_hash="completed-reclaim",
        source_item_ids=[1],
        source_snapshot={"kind": audio_episodes.FAST_NEWS_DIGEST_KIND, "items": []},
        prompt_version=audio_episodes.PROMPT_VERSION,
        audio_storage_path="/tmp/already-completed.mp3",
    )
    db_session.add(episode)
    db_session.commit()

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

    monkeypatch.setattr(
        scripting,
        "generate_script",
        lambda *_args: pytest.fail("completed episode must not call the provider"),
    )
    context = TaskContext(
        queue_service=QueueService(),
        settings=cast(Settings, SimpleNamespace(queue=SimpleNamespace(max_retries=3))),
        llm_service=None,
        worker_id="test-worker",
        db_factory=db_factory,
    )

    result = GenerateAudioEpisodeHandler().handle(
        TaskEnvelope(
            id=3,
            owner_user_id=user.id,
            task_type=TaskType.GENERATE_AUDIO_EPISODE,
            payload={"audio_episode_id": episode.id, "user_id": user.id},
            retry_count=4,
        ),
        context,
    )

    db_session.expire_all()
    persisted = db_session.get(AudioEpisode, episode.id)
    assert result.success is True
    assert persisted.status == "completed"
    assert persisted.audio_storage_path == "/tmp/already-completed.mp3"


def test_generate_audio_episode_rejects_cross_user_target_without_mutating_it(
    db_session,
    db_session_factory,
    monkeypatch,
    user_factory,
) -> None:
    task_owner = user_factory()
    target_owner = user_factory()
    episode = AudioEpisode(
        user_id=target_owner.id,
        kind=audio_episodes.FAST_NEWS_DIGEST_KIND,
        status="pending",
        title="Another User's Brief",
        input_hash="cross-user-reclaim",
        source_item_ids=[1],
        source_snapshot={"kind": audio_episodes.FAST_NEWS_DIGEST_KIND, "items": []},
        prompt_version=audio_episodes.PROMPT_VERSION,
    )
    db_session.add(episode)
    db_session.commit()

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

    monkeypatch.setattr(
        scripting,
        "generate_script",
        lambda *_args: pytest.fail("cross-user episode must not call the provider"),
    )
    context = TaskContext(
        queue_service=QueueService(),
        settings=cast(Settings, SimpleNamespace(queue=SimpleNamespace(max_retries=3))),
        llm_service=None,
        worker_id="test-worker",
        db_factory=db_factory,
    )

    result = GenerateAudioEpisodeHandler().handle(
        TaskEnvelope(
            id=4,
            owner_user_id=task_owner.id,
            task_type=TaskType.GENERATE_AUDIO_EPISODE,
            payload={"audio_episode_id": episode.id, "user_id": task_owner.id},
            retry_count=4,
        ),
        context,
    )

    db_session.expire_all()
    persisted = db_session.get(AudioEpisode, episode.id)
    assert result.success is False
    assert result.retryable is False
    assert persisted.status == "pending"
    assert persisted.audio_storage_path is None


@pytest.mark.parametrize(
    (
        "failure_kind",
        "status_code",
        "retry_count",
        "expected_retryable",
        "expected_episode_status",
        "expected_error",
    ),
    [
        ("provider", 404, 0, False, "failed", "status_code: 404, raw provider diagnostics"),
        ("provider", 408, 0, True, "pending", "status_code: 408, raw provider diagnostics"),
        ("provider", 429, 0, True, "pending", "status_code: 429, raw provider diagnostics"),
        ("provider", 503, 0, True, "pending", "status_code: 503, raw provider diagnostics"),
        ("provider", 503, 3, True, "failed", "status_code: 503, raw provider diagnostics"),
        ("local_input", None, 0, False, "failed", "Preauthored audio episode narration is empty"),
        ("local_tts", None, 0, False, "failed", "ElevenLabs API key is not configured"),
        (
            "wrapped_local_tts",
            None,
            0,
            False,
            "failed",
            "Failed to generate audio episode dialogue",
        ),
        ("unknown_value", None, 0, True, "pending", "Provider output validation failed"),
        ("wrapped_transient", 503, 0, True, "pending", "Invalid provider response"),
    ],
)
def test_generate_audio_episode_handler_classifies_generation_failures(
    db_session,
    db_session_factory,
    monkeypatch,
    user_factory,
    failure_kind: str,
    status_code: int | None,
    retry_count: int,
    expected_retryable: bool,
    expected_episode_status: str,
    expected_error: str,
) -> None:
    user = user_factory()
    episode = AudioEpisode(
        user_id=user.id,
        kind=audio_episodes.FAST_NEWS_DIGEST_KIND,
        status="pending",
        title="Fast Reads Brief",
        input_hash=f"{failure_kind}-{status_code}-{retry_count}",
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

    class ProviderHTTPError(RuntimeError):
        def __init__(self, status: int) -> None:
            self.status_code = status
            super().__init__(f"status_code: {status}, raw provider diagnostics")

    def fail_script(_episode):
        if failure_kind == "local_input":
            raise AudioEpisodeInputError("Preauthored audio episode narration is empty")
        if failure_kind == "local_tts":
            raise NarrationTtsConfigurationError("ElevenLabs API key is not configured")
        if failure_kind == "wrapped_local_tts":
            error = NarrationTtsConfigurationError("ffmpeg is not installed")
            raise RuntimeError("Failed to generate audio episode dialogue") from error
        if failure_kind == "unknown_value":
            raise ValueError("Provider output validation failed")
        assert status_code is not None
        provider_error = ProviderHTTPError(status_code)
        if failure_kind == "wrapped_transient":
            raise ValueError("Invalid provider response") from provider_error
        raise provider_error

    monkeypatch.setattr(scripting, "generate_script", fail_script)
    context = TaskContext(
        queue_service=QueueService(),
        settings=cast(Settings, SimpleNamespace(queue=SimpleNamespace(max_retries=3))),
        llm_service=None,
        worker_id="test-worker",
        db_factory=db_factory,
    )
    task = TaskEnvelope(
        id=1,
        task_type=TaskType.GENERATE_AUDIO_EPISODE,
        payload={"audio_episode_id": episode.id, "user_id": user.id},
        retry_count=retry_count,
    )

    result = GenerateAudioEpisodeHandler().handle(task, context)

    assert result.success is False
    assert result.retryable is expected_retryable
    db_session.expire_all()
    persisted = db_session.query(AudioEpisode).filter(AudioEpisode.id == episode.id).one()
    assert persisted.status == expected_episode_status
    assert persisted.error_message == expected_error
