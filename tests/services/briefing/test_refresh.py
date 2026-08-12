from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.models.contracts import ContentClassification, ContentType, TaskStatus, TaskType
from app.models.db import (
    BriefingLens,
    BriefingPendingSource,
    BriefingSegment,
    BriefingState,
    Content,
    NewsItemReadStatus,
    ProcessingTask,
)
from app.models.db.users import User
from app.services.briefing import refresh as refresh_service
from app.services.briefing.read_marks import (
    bump_briefing_version_for_news_item,
    mark_briefing_sources_read,
)
from app.services.briefing.refresh import (
    _retire_finished_segments,
    enqueue_briefing_refresh_task,
    run_briefing_refresh,
)

pytestmark = pytest.mark.usefixtures("stub_briefing_layout_generator")


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

    assert result.appended_segments == 3
    assert result.pending_added == 3
    assert result.sweep_enqueued is True
    lens = db_session.query(BriefingLens).filter(BriefingLens.key == "articles").one()
    segments = (
        db_session.query(BriefingSegment)
        .filter(BriefingSegment.lens_id == lens.id)
        .order_by(BriefingSegment.id)
        .all()
    )
    assert [segment.status for segment in segments] == ["active"] * 3
    assert [segment.source_keys for segment in segments] == [
        [f"content:{content.id}"] for content in reversed(contents)
    ]
    assert all(segment.narration_text for segment in segments)
    assert (
        db_session.query(ProcessingTask)
        .filter(ProcessingTask.task_type == TaskType.BRIEFING_REFRESH.value)
        .filter(ProcessingTask.dedupe_key == f"briefing_refresh:{user_id}:sweep")
        .count()
        == 1
    )


def test_refresh_owns_one_provider_client_lifecycle(
    db_session: Session,
    test_user: User,
    monkeypatch,
) -> None:
    assert test_user.id is not None
    settings = get_settings().model_copy(
        update={
            "briefing_enabled_user_ids": [test_user.id],
            "briefing_model": "openrouter:test/model",
            "openrouter_api_key": "test-key",
        }
    )
    clients: list[object] = []

    class FakeRefreshClient:
        def __init__(self, **_kwargs) -> None:
            self.entered = False
            self.closed = False
            clients.append(self)

        def __enter__(self):
            self.entered = True
            return self

        def __exit__(self, *_args) -> None:
            self.closed = True

        def request_json_schema(self, **_kwargs):
            raise AssertionError("No generation request expected for an empty refresh")

    monkeypatch.setattr(refresh_service, "BriefingOpenRouterClient", FakeRefreshClient)

    run_briefing_refresh(
        db_session,
        user_id=test_user.id,
        mode="sweep",
        use_llm=True,
        settings=settings,
    )

    assert len(clients) == 1
    assert isinstance(clients[0], FakeRefreshClient)
    assert clients[0].entered is True
    assert clients[0].closed is True


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
    existing_segments = db_session.query(BriefingSegment).order_by(BriefingSegment.id).all()

    def fail_compose(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise TimeoutError("compose timed out")

    monkeypatch.setattr("app.services.briefing.window_composition.compose_window", fail_compose)
    with pytest.raises(TimeoutError, match="compose timed out"):
        run_briefing_refresh(
            db_session,
            user_id=user_id,
            mode="full",
            use_llm=True,
            settings=settings,
        )
    db_session.rollback()
    for segment in existing_segments:
        db_session.refresh(segment)

    assert [segment.status for segment in existing_segments] == ["active"] * 3
    assert db_session.query(BriefingSegment).count() == 3


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
        settings=settings,
    )
    db_session.commit()

    assert result.appended_segments == 0
    assert result.pending_added == 0
    assert result.retired_segments == 0
    assert result.compacted_segments == 0


def test_append_composes_low_volume_uncovered_sources_immediately(
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
    new_article = _create_unread_article(
        content_factory,
        status_entry_factory,
        test_user,
        index=99,
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

    assert result.pending_added == 1
    assert result.appended_segments == 1
    assert db_session.query(BriefingPendingSource).filter_by(user_id=user_id).count() == 0
    latest_segment = (
        db_session.query(BriefingSegment)
        .filter(BriefingSegment.user_id == user_id)
        .order_by(BriefingSegment.id.desc())
        .first()
    )
    assert latest_segment is not None
    assert latest_segment.source_keys == [f"content:{new_article.id}"]


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
    assert podcast_segment.source_keys == [f"content:{podcasts[-1].id}"]
    assert (
        db_session.query(BriefingPendingSource)
        .filter_by(user_id=user_id, lens_key="podcasts")
        .count()
        == 2
    )


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
    assert result.compacted_segments == 1
    source_key_groups = [
        segment.source_keys
        for segment in db_session.query(BriefingSegment)
        .filter(BriefingSegment.lens_id == lens.id)
        .filter(BriefingSegment.status.in_(("active", "degraded")))
        .order_by(BriefingSegment.id)
    ]
    assert source_key_groups == [[f"content:{podcast.id}"] for podcast in podcasts[2:]]
    assert (
        db_session.query(BriefingPendingSource)
        .filter_by(user_id=user_id, lens_key="podcasts")
        .count()
        == 2
    )


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
    segment = db_session.query(BriefingSegment).order_by(BriefingSegment.id).first()
    assert segment is not None
    assert segment.source_keys is not None

    result = mark_briefing_sources_read(
        db_session,
        user_id=user_id,
        source_keys=list(segment.source_keys),
    )
    db_session.commit()
    db_session.refresh(segment)

    assert result.marked == 1
    assert result.version == refresh.version + 1
    assert segment.status == "retired"


def test_old_unread_news_segment_stays_active(
    db_session: Session,
    test_user: User,
    news_item_factory,
) -> None:
    assert test_user.id is not None
    user_id = test_user.id
    published_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=30)
    item = news_item_factory(
        visibility_scope="user",
        owner_user_id=user_id,
        published_at=published_at,
    )
    lens = _create_lens(db_session, user_id=user_id, key="news-old")
    segment = BriefingSegment(
        lens_id=lens.id,
        user_id=user_id,
        blocks=[],
        source_keys=[f"news:{item.id}"],
        status="active",
        model="test",
        prompt_version="test",
    )
    db_session.add(segment)
    db_session.commit()

    retired = _retire_finished_segments(db_session, user_id=user_id, settings=get_settings())
    db_session.commit()
    db_session.refresh(segment)

    assert retired == 0
    assert segment.status == "active"


def test_refresh_retires_duplicate_segment_when_representative_is_read(
    db_session: Session,
    test_user: User,
    news_item_factory,
) -> None:
    assert test_user.id is not None
    user_id = test_user.id
    representative = news_item_factory(
        visibility_scope="user",
        owner_user_id=user_id,
    )
    duplicate = news_item_factory(
        visibility_scope="user",
        owner_user_id=user_id,
        representative_news_item_id=representative.id,
    )
    lens = _create_lens(db_session, user_id=user_id, key="news-clustered")
    segment = BriefingSegment(
        lens_id=lens.id,
        user_id=user_id,
        blocks=[],
        source_keys=[f"news:{duplicate.id}"],
        status="active",
        model="test",
        prompt_version="test",
    )
    db_session.add_all(
        [
            segment,
            NewsItemReadStatus(
                user_id=user_id,
                news_item_id=representative.id,
            ),
        ]
    )
    db_session.commit()

    retired = _retire_finished_segments(db_session, user_id=user_id, settings=get_settings())
    db_session.commit()
    db_session.refresh(segment)

    assert retired == 1
    assert segment.status == "retired"


def test_bump_briefing_version_for_news_item_only_updates_matching_enabled_active_segments(
    db_session: Session,
    user_factory,
    news_item_factory,
    monkeypatch,
) -> None:
    enabled_user = user_factory(email="briefing-enabled@example.com")
    other_enabled_user = user_factory(email="briefing-other-enabled@example.com")
    disabled_user = user_factory(email="briefing-disabled@example.com", is_active=False)
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


def test_append_enqueue_moves_deadline_earlier_but_never_later(
    db_session: Session,
    test_user: User,
) -> None:
    assert test_user.id is not None
    user_id = test_user.id
    now = datetime.now(UTC).replace(tzinfo=None)

    assert enqueue_briefing_refresh_task(
        db_session,
        user_id=user_id,
        mode="sweep",
        delay_seconds=1800,
    )
    assert not enqueue_briefing_refresh_task(
        db_session,
        user_id=user_id,
        mode="sweep",
        delay_seconds=2400,
    )
    assert enqueue_briefing_refresh_task(
        db_session,
        user_id=user_id,
        mode="sweep",
        delay_seconds=600,
    )
    task = (
        db_session.query(ProcessingTask)
        .filter(ProcessingTask.dedupe_key == f"briefing_refresh:{user_id}:sweep")
        .one()
    )

    assert task.available_at is not None
    assert now + timedelta(seconds=590) <= task.available_at <= now + timedelta(seconds=610)


def test_three_unassigned_news_sources_compose_as_the_preferred_batch(
    db_session: Session,
    test_user: User,
    news_item_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    assert test_user.id is not None
    user_id = test_user.id
    monkeypatch.setattr(settings, "briefing_enabled_user_ids", [user_id])
    monkeypatch.setattr(settings, "briefing_window_min", 3)
    monkeypatch.setattr(settings, "briefing_new_lens_min_items", 3)
    monkeypatch.setattr(settings, "briefing_news_window_max", 4)

    items = [
        news_item_factory(
            visibility_scope="user",
            owner_user_id=user_id,
            article_title=f"Preferred Batch Story {index}",
            summary_title=f"Preferred Batch Story {index}",
        )
        for index in range(3)
    ]
    db_session.add_all(
        [
            BriefingPendingSource(
                user_id=user_id,
                source_kind="news",
                source_id=item.id,
            )
            for item in items
        ]
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

    assert result.appended_segments == 1
    assert db_session.query(BriefingPendingSource).filter_by(user_id=user_id).count() == 0
    segment = db_session.query(BriefingSegment).filter_by(user_id=user_id).one()
    assert segment.source_keys == [f"news:{item.id}" for item in items]


def test_unassigned_news_waits_for_target_then_flushes_at_25_minute_deadline(
    db_session: Session,
    test_user: User,
    news_item_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    assert test_user.id is not None
    user_id = test_user.id
    monkeypatch.setattr(settings, "briefing_enabled_user_ids", [user_id])
    monkeypatch.setattr(settings, "briefing_window_min", 3)
    monkeypatch.setattr(settings, "briefing_pending_max_age_seconds", 1500)
    monkeypatch.setattr(settings, "briefing_sweep_seconds", 3600)

    item = news_item_factory(
        visibility_scope="user",
        owner_user_id=user_id,
        article_title="A low-volume deadline story",
        summary_title="A low-volume deadline story",
    )
    enqueued_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=5)
    pending = BriefingPendingSource(
        user_id=user_id,
        source_kind="news",
        source_id=item.id,
        enqueued_at=enqueued_at,
    )
    db_session.add(pending)
    db_session.commit()

    waiting_result = run_briefing_refresh(
        db_session,
        user_id=user_id,
        mode="append",
        use_llm=False,
        settings=settings,
    )
    db_session.commit()

    assert waiting_result.appended_segments == 0
    assert db_session.query(BriefingPendingSource).filter_by(user_id=user_id).count() == 1
    sweep_task = (
        db_session.query(ProcessingTask)
        .filter(ProcessingTask.dedupe_key == f"briefing_refresh:{user_id}:sweep")
        .one()
    )
    assert sweep_task.available_at is not None
    expected_deadline = enqueued_at + timedelta(seconds=1500)
    assert expected_deadline <= sweep_task.available_at <= expected_deadline + timedelta(seconds=2)

    pending.enqueued_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1501)
    db_session.commit()
    deadline_result = run_briefing_refresh(
        db_session,
        user_id=user_id,
        mode="sweep",
        use_llm=False,
        settings=settings,
    )
    db_session.commit()

    assert deadline_result.appended_segments == 1
    assert db_session.query(BriefingPendingSource).filter_by(user_id=user_id).count() == 0
    misc_lens = db_session.query(BriefingLens).filter_by(user_id=user_id, key="misc").one()
    segment = db_session.query(BriefingSegment).filter_by(lens_id=misc_lens.id).one()
    assert segment.source_keys == [f"news:{item.id}"]


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
