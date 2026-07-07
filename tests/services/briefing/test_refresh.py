from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.models.contracts import ContentClassification, ContentType, TaskStatus, TaskType
from app.models.db import (
    BriefingLens,
    BriefingSegment,
    BriefingState,
    Content,
    ProcessingTask,
)
from app.models.db.users import User
from app.services.briefing.read_marks import (
    bump_briefing_version_for_news_item,
    mark_briefing_sources_read,
)
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


def test_release_path_append_without_pending_skips_planning(
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
        release_db_during_compose=True,
        settings=settings,
    )
    db_session.commit()

    def fail_planning(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("append with no pending work should return before planning")

    monkeypatch.setattr("app.services.briefing.refresh._plan_ready_windows", fail_planning)
    monkeypatch.setattr("app.services.briefing.refresh._retire_finished_segments", fail_planning)

    result = run_briefing_refresh(
        db_session,
        user_id=user_id,
        mode="append",
        release_db_during_compose=True,
        settings=settings,
    )
    db_session.commit()

    assert result.appended_segments == 0
    assert result.pending_added == 0
    assert result.retired_segments == 0
    assert result.compacted_segments == 0


def test_append_backfills_uncovered_unclassified_podcasts_after_existing_segment(
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

    podcasts = [
        _create_unread_podcast(
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
        mode="append",
        use_llm=False,
        settings=settings,
    )
    db_session.commit()

    assert result.pending_added == 3
    assert result.appended_segments == 1
    podcast_lens = db_session.query(BriefingLens).filter(BriefingLens.key == "podcasts").one()
    podcast_segment = (
        db_session.query(BriefingSegment).filter(BriefingSegment.lens_id == podcast_lens.id).one()
    )
    assert podcast_segment.source_keys == [
        f"content:{podcast.id}" for podcast in reversed(podcasts)
    ]


def test_append_seeds_uncovered_podcasts_beyond_existing_top_slice(
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
    podcasts = [
        _create_unread_podcast(
            content_factory,
            status_entry_factory,
            test_user,
            index=index,
        )
        for index in range(5)
    ]
    lens = BriefingLens(
        user_id=user_id,
        key="podcasts",
        tier="audio",
        title="Podcasts",
        deck="Existing podcasts.",
        position=0,
    )
    db_session.add(lens)
    db_session.flush()
    db_session.add(
        BriefingSegment(
            lens_id=lens.id,
            user_id=user_id,
            blocks=[],
            source_keys=[f"content:{podcast.id}" for podcast in podcasts[-2:]],
            status="active",
            model="test",
            prompt_version="test",
        )
    )
    db_session.commit()

    result = run_briefing_refresh(
        db_session,
        user_id=user_id,
        mode="append",
        use_llm=False,
        settings=settings,
    )
    db_session.commit()

    assert result.pending_added == 3
    assert result.appended_segments == 1
    source_key_sets = [
        set(segment.source_keys or [])
        for segment in db_session.query(BriefingSegment)
        .filter(BriefingSegment.lens_id == lens.id)
        .order_by(BriefingSegment.id)
    ]
    assert source_key_sets == [
        {f"content:{podcast.id}" for podcast in podcasts[-2:]},
        {f"content:{podcast.id}" for podcast in podcasts[:3]},
    ]


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


def test_bump_briefing_version_for_news_item_only_updates_matching_enabled_active_segments(
    db_session: Session,
    user_factory,
    news_item_factory,
    monkeypatch,
) -> None:
    enabled_user = user_factory(email="briefing-enabled@example.com")
    other_enabled_user = user_factory(email="briefing-other-enabled@example.com")
    disabled_user = user_factory(email="briefing-disabled@example.com")
    assert enabled_user.id is not None
    assert other_enabled_user.id is not None
    assert disabled_user.id is not None
    item = news_item_factory(visibility_scope="user", owner_user_id=enabled_user.id)
    settings = get_settings()
    monkeypatch.setattr(
        settings,
        "briefing_enabled_user_ids",
        [enabled_user.id, other_enabled_user.id],
    )

    matching_lens = _create_lens(db_session, user_id=enabled_user.id, key="news-enabled")
    retired_lens = _create_lens(db_session, user_id=other_enabled_user.id, key="news-retired")
    disabled_lens = _create_lens(db_session, user_id=disabled_user.id, key="news-disabled")
    db_session.add_all(
        [
            BriefingSegment(
                lens_id=matching_lens.id,
                user_id=enabled_user.id,
                blocks=[],
                source_keys=[f"news:{item.id}"],
                status="active",
                model="test",
                prompt_version="test",
            ),
            BriefingSegment(
                lens_id=retired_lens.id,
                user_id=other_enabled_user.id,
                blocks=[],
                source_keys=[f"news:{item.id}"],
                status="retired",
                model="test",
                prompt_version="test",
            ),
            BriefingSegment(
                lens_id=disabled_lens.id,
                user_id=disabled_user.id,
                blocks=[],
                source_keys=[f"news:{item.id}"],
                status="active",
                model="test",
                prompt_version="test",
            ),
            BriefingState(
                user_id=enabled_user.id,
                version=7,
                masthead_title="The Unread Times",
                masthead_deck="Existing",
            ),
            BriefingState(
                user_id=other_enabled_user.id,
                version=3,
                masthead_title="The Unread Times",
                masthead_deck="Existing",
            ),
            BriefingState(
                user_id=disabled_user.id,
                version=5,
                masthead_title="The Unread Times",
                masthead_deck="Existing",
            ),
        ]
    )
    db_session.commit()

    bumped = bump_briefing_version_for_news_item(
        db_session,
        news_item_id=item.id,
        settings=settings,
    )
    db_session.commit()

    assert bumped is True
    assert _state_version(db_session, enabled_user.id) == 8
    assert _state_version(db_session, other_enabled_user.id) == 3
    assert _state_version(db_session, disabled_user.id) == 5


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


def _create_lens(db_session: Session, *, user_id: int, key: str) -> BriefingLens:
    lens = BriefingLens(
        user_id=user_id,
        key=key,
        tier="news",
        title=key.replace("-", " ").title(),
        deck="News",
        position=1,
    )
    db_session.add(lens)
    db_session.flush()
    return lens


def _state_version(db_session: Session, user_id: int) -> int:
    state = db_session.query(BriefingState).filter(BriefingState.user_id == user_id).one()
    assert state.version is not None
    return int(state.version)


def _create_unread_podcast(
    content_factory,
    status_entry_factory,
    user: User,
    *,
    index: int,
) -> Content:
    content = content_factory(
        content_type=ContentType.PODCAST,
        title=f"Briefing podcast {index}",
        classification=None,
        content_metadata={
            "summary": {
                "overview": f"Podcast summary {index}",
                "key_points": [f"Podcast point {index}"],
            }
        },
    )
    status_entry_factory(user=user, content=content, status="inbox")
    return content
