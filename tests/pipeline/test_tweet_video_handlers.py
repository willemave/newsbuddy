"""Tests for tweet video media task handlers."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

from app.core.settings import Settings
from app.models.contracts import ContentStatus, ContentType
from app.models.db import Content
from app.pipeline.handlers.download_tweet_video import DownloadTweetVideoAudioHandler
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope
from app.services.queue import TaskType


def _build_context(db_session, tmp_path, queue_gateway: Mock) -> TaskContext:
    @contextmanager
    def _db_context():
        yield db_session

    return TaskContext(
        queue_service=Mock(),
        settings=cast(
            Settings,
            SimpleNamespace(
                tweet_video_enabled=True,
                tweet_video_media_dir=tmp_path,
            ),
        ),
        llm_service=Mock(),
        worker_id="test-worker",
        queue_gateway=queue_gateway,
        db_factory=_db_context,
    )


def test_download_tweet_video_uses_snapshot_video_metadata(
    db_session,
    monkeypatch,
    tmp_path,
) -> None:
    content = Content(
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.PROCESSING.value,
        url="https://x.com/i/status/123",
        platform="twitter",
        content_metadata={
            "platform": "twitter",
            "has_video": False,
            "tweet_video_skip_reason": "duration_limit",
            "tweet_snapshot": {
                "has_video": True,
                "video_duration_ms": 3_395_666,
            },
        },
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    audio_path = tmp_path / "tweet-123.m4a"
    audio_path.write_bytes(b"audio")
    monkeypatch.setattr(
        "app.pipeline.handlers.download_tweet_video.download_audio_via_ytdlp",
        lambda *_args, **_kwargs: audio_path,
    )

    queue_gateway = Mock()
    queue_gateway.enqueue.return_value = 77
    context = _build_context(db_session, tmp_path, queue_gateway)
    task = TaskEnvelope(
        id=1,
        task_type=TaskType.DOWNLOAD_TWEET_VIDEO_AUDIO,
        content_id=content.id,
    )

    result = DownloadTweetVideoAudioHandler().handle(task, context)

    assert result.success is True
    db_session.refresh(content)
    metadata = content.content_metadata
    assert isinstance(metadata, dict)
    assert metadata["has_video"] is True
    assert metadata["video_duration_ms"] == 3_395_666
    assert "tweet_video_skip_reason" not in metadata
    assert metadata["video_audio_path"] == str(audio_path)
    queue_gateway.enqueue.assert_called_once_with(
        TaskType.TRANSCRIBE_TWEET_VIDEO,
        content_id=content.id,
    )
