from __future__ import annotations

from types import SimpleNamespace

from app.models.contracts import ContentStatus, ContentType, TaskQueue, TaskStatus, TaskType
from app.models.db import Content, ProcessingTask
from scripts import reset_errored_content


def test_reset_errored_content_requeues_content_with_canonical_queue_fields(
    postgres_harness,
    db_session,
    monkeypatch,
) -> None:
    content = Content(
        content_type=ContentType.ARTICLE.value,
        url="https://example.com/failed",
        source="example",
        status=ContentStatus.FAILED.value,
        error_message="summary failed",
        content_metadata={"partial": "data"},
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)
    db_session.add(
        ProcessingTask(
            task_type=TaskType.SUMMARIZE.value,
            content_id=content.id,
            status=TaskStatus.PENDING.value,
            queue_name=TaskQueue.CONTENT.value,
        )
    )
    db_session.commit()

    monkeypatch.setattr(
        reset_errored_content,
        "get_settings",
        lambda: SimpleNamespace(database_url=postgres_harness.database_url),
    )

    reset_errored_content.reset_errored_content()

    db_session.expire_all()
    reset_content = db_session.query(Content).filter(Content.id == content.id).one()
    tasks = db_session.query(ProcessingTask).filter(ProcessingTask.content_id == content.id).all()

    assert reset_content.status == ContentStatus.NEW.value
    assert reset_content.error_message is None
    assert len(tasks) == 1
    assert tasks[0].task_type == TaskType.PROCESS_CONTENT.value
    assert tasks[0].status == TaskStatus.PENDING.value
    assert tasks[0].queue_name == TaskQueue.CONTENT.value
    assert (
        tasks[0].dedupe_key
        == f"{TaskQueue.CONTENT.value}|{TaskType.PROCESS_CONTENT.value}|content:{content.id}"
    )
