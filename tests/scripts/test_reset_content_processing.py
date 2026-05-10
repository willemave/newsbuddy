from __future__ import annotations

from types import SimpleNamespace

from app.models.contracts import ContentType
from app.models.db import Content, ProcessingTask
from scripts import reset_content_processing


def test_perform_reset_preserves_podcast_source_metadata(
    postgres_harness,
    db_session,
    monkeypatch,
) -> None:
    audio_url = "https://cdn.example.com/episode.mp3"
    content = Content(
        content_type=ContentType.PODCAST.value,
        url=audio_url,
        source="Example Feed",
        status="failed",
        error_message="old failure",
        content_metadata={
            "audio_url": audio_url,
            "feed_url": "https://cdn.example.com/feed.xml",
            "feed_config_id": 42,
            "duration_seconds": 1234,
            "summary": {"overview": "old summary"},
            "transcript": "old transcript",
            "content_to_summarize": "old transcript",
        },
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)
    db_session.add(
        ProcessingTask(
            task_type="summarize",
            content_id=content.id,
            status="pending",
        )
    )
    db_session.commit()

    monkeypatch.setattr(
        reset_content_processing,
        "get_settings",
        lambda: SimpleNamespace(database_url=postgres_harness.database_url),
    )

    result = reset_content_processing.perform_reset(
        reset_content_processing.ResetOptions(content_type=ContentType.PODCAST)
    )

    db_session.expire_all()
    reset_content = db_session.query(Content).filter(Content.id == content.id).one()
    tasks = db_session.query(ProcessingTask).filter(ProcessingTask.content_id == content.id).all()

    assert result.deleted_tasks == 1
    assert result.reset_contents == 1
    assert result.created_tasks == 1
    assert reset_content.status == "new"
    assert reset_content.error_message is None
    assert reset_content.content_metadata == {
        "audio_url": audio_url,
        "duration_seconds": 1234,
        "feed_config_id": 42,
        "feed_url": "https://cdn.example.com/feed.xml",
    }
    assert len(tasks) == 1
    assert tasks[0].task_type == "process_content"
