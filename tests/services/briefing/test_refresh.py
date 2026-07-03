from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.models.contracts import ContentClassification, ContentType, TaskStatus, TaskType
from app.models.db import (
    BriefingLens,
    BriefingSegment,
    Content,
    ProcessingTask,
)
from app.models.db.users import User
from app.services.briefing.read_marks import mark_briefing_sources_read
from app.services.briefing.refresh import (
    enqueue_briefing_refresh_task,
    run_briefing_refresh,
)


def test_full_refresh_builds_segments_and_schedules_sweep(
    db_session: Session,
    test_user: User,
    content_factory,
    status_entry_factory,
    monkeypatch,
) -> None:
    settings = get_settings()
    assert test_user.id is not None
    user_id = test_user.id
    monkeypatch.setattr(settings, "briefing_enabled_user_ids", [user_id])
    contents = [
        _create_unread_article(
            content_factory,
            status_entry_factory,
            test_user,
            index=index,
        )
        for index in range(3)
    ]

    result = run_briefing_refresh(
        db_session,
        user_id=user_id,
        mode="full",
        use_llm=False,
        settings=settings,
    )
    db_session.commit()

    assert result.appended_segments == 1
    assert result.pending_added == 3
    assert result.sweep_enqueued is True
    lens = db_session.query(BriefingLens).filter(BriefingLens.key == "articles").one()
    segment = db_session.query(BriefingSegment).filter(BriefingSegment.lens_id == lens.id).one()
    assert segment.status == "active"
    assert segment.source_keys == [f"content:{content.id}" for content in reversed(contents)]
    assert segment.narration_text
    assert (
        db_session.query(ProcessingTask)
        .filter(ProcessingTask.task_type == TaskType.BRIEFING_REFRESH.value)
        .filter(ProcessingTask.dedupe_key == f"briefing_refresh:{user_id}:sweep")
        .count()
        == 1
    )


def test_release_path_full_refresh_builds_segments_and_schedules_sweep(
    db_session: Session,
    test_user: User,
    content_factory,
    status_entry_factory,
    monkeypatch,
) -> None:
    settings = get_settings()
    assert test_user.id is not None
    user_id = test_user.id
    monkeypatch.setattr(settings, "briefing_enabled_user_ids", [user_id])
    contents = [
        _create_unread_article(
            content_factory,
            status_entry_factory,
            test_user,
            index=index,
        )
        for index in range(3)
    ]

    result = run_briefing_refresh(
        db_session,
        user_id=user_id,
        mode="full",
        use_llm=False,
        release_db_during_compose=True,
        settings=settings,
    )
    db_session.commit()

    assert result.appended_segments == 1
    assert result.pending_added == 3
    assert result.sweep_enqueued is True
    lens = db_session.query(BriefingLens).filter(BriefingLens.key == "articles").one()
    segment = db_session.query(BriefingSegment).filter(BriefingSegment.lens_id == lens.id).one()
    assert segment.status == "active"
    assert segment.source_keys == [f"content:{content.id}" for content in reversed(contents)]


def test_release_path_full_refresh_preserves_current_segments_when_compose_fails(
    db_session: Session,
    test_user: User,
    content_factory,
    status_entry_factory,
    monkeypatch,
) -> None:
    settings = get_settings()
    assert test_user.id is not None
    user_id = test_user.id
    monkeypatch.setattr(settings, "briefing_enabled_user_ids", [user_id])
    for index in range(3):
        _create_unread_article(
            content_factory,
            status_entry_factory,
            test_user,
            index=index,
        )
    run_briefing_refresh(
        db_session,
        user_id=user_id,
        mode="full",
        use_llm=False,
        settings=settings,
    )
    db_session.commit()
    existing_segment = db_session.query(BriefingSegment).one()

    def fail_compose(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise TimeoutError("compose timed out")

    monkeypatch.setattr("app.services.briefing.refresh.compose_window", fail_compose)
    with pytest.raises(TimeoutError, match="compose timed out"):
        run_briefing_refresh(
            db_session,
            user_id=user_id,
            mode="full",
            use_llm=True,
            release_db_during_compose=True,
            settings=settings,
        )
    db_session.rollback()
    db_session.refresh(existing_segment)

    assert existing_segment.status == "active"
    assert db_session.query(BriefingSegment).count() == 1


def test_mark_read_retires_fully_read_segment_and_bumps_version(
    db_session: Session,
    test_user: User,
    content_factory,
    status_entry_factory,
    monkeypatch,
) -> None:
    settings = get_settings()
    assert test_user.id is not None
    user_id = test_user.id
    monkeypatch.setattr(settings, "briefing_enabled_user_ids", [user_id])
    _create_unread_article(content_factory, status_entry_factory, test_user, index=1)
    _create_unread_article(content_factory, status_entry_factory, test_user, index=2)
    _create_unread_article(content_factory, status_entry_factory, test_user, index=3)
    refresh = run_briefing_refresh(
        db_session,
        user_id=user_id,
        mode="full",
        use_llm=False,
        settings=settings,
    )
    db_session.commit()
    segment = db_session.query(BriefingSegment).one()
    assert segment.source_keys is not None

    result = mark_briefing_sources_read(
        db_session,
        user_id=user_id,
        source_keys=list(segment.source_keys),
    )
    db_session.commit()
    db_session.refresh(segment)

    assert result.marked == 3
    assert result.version == refresh.version + 1
    assert segment.status == "retired"


def test_append_enqueue_coalesces_but_manual_refresh_pulls_pending_task_forward(
    db_session: Session,
    test_user: User,
) -> None:
    assert test_user.id is not None
    user_id = test_user.id
    now = datetime.now(UTC).replace(tzinfo=None)

    assert enqueue_briefing_refresh_task(
        db_session,
        user_id=user_id,
        mode="append",
        delay_seconds=900,
    )
    assert not enqueue_briefing_refresh_task(
        db_session,
        user_id=user_id,
        mode="append",
        delay_seconds=900,
    )
    assert enqueue_briefing_refresh_task(
        db_session,
        user_id=user_id,
        mode="append",
        delay_seconds=0,
    )
    task = (
        db_session.query(ProcessingTask)
        .filter(ProcessingTask.dedupe_key == f"briefing_refresh:{user_id}:append")
        .one()
    )

    assert task.status == TaskStatus.PENDING.value
    assert task.available_at is not None
    assert task.available_at <= now + timedelta(seconds=5)


def _create_unread_article(
    content_factory,
    status_entry_factory,
    user: User,
    *,
    index: int,
) -> Content:
    content = content_factory(
        content_type=ContentType.ARTICLE,
        title=f"Briefing article {index}",
        classification=ContentClassification.TO_READ.value,
        content_metadata={
            "summary": {
                "overview": f"Summary {index}",
                "key_points": [f"Point {index}"],
            }
        },
    )
    status_entry_factory(user=user, content=content, status="inbox")
    return content
