"""Tests for the download-audio task handler."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import Mock

from app.models.metadata import ContentType
from app.models.schema import Content
from app.pipeline.handlers.download_audio import DownloadAudioHandler
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope
from app.services.queue import TaskType


def _build_context(db_session) -> TaskContext:
    @contextmanager
    def _db_context():
        yield db_session

    return TaskContext(
        queue_service=Mock(),
        settings=Mock(),
        llm_service=Mock(),
        worker_id="test-worker",
        db_factory=_db_context,
    )


def test_download_audio_handler_marks_youtube_auth_failure_non_retryable(
    monkeypatch,
    db_session,
) -> None:
    content = Content(
        content_type=ContentType.PODCAST.value,
        url="https://www.youtube.com/watch?v=abc123xyz",
        error_message=(
            "ERROR: [youtube] abc123xyz: Sign in to confirm you're not a bot. "
            "Use --cookies-from-browser or --cookies for the authentication."
        ),
        content_metadata={"audio_url": "https://www.youtube.com/watch?v=abc123xyz"},
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    monkeypatch.setattr(
        "app.pipeline.handlers.download_audio.PodcastDownloadWorker.process_download_task",
        lambda _self, _content_id: False,
    )

    handler = DownloadAudioHandler()
    context = _build_context(db_session)
    task = TaskEnvelope(id=1, task_type=TaskType.DOWNLOAD_AUDIO, content_id=content.id)

    result = handler.handle(task, context)

    assert result.success is False
    assert result.retryable is False
    assert "Sign in to confirm" in (result.error_message or "")
