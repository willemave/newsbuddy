from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.models.contracts import ContentClassification, ContentType
from app.models.db import (
    BriefingLens,
    BriefingPendingSource,
    BriefingSegment,
    Content,
    ProcessingTask,
)
from app.models.db.users import User
from app.services.briefing.first_run import start_first_edition
from app.services.briefing.presentation import get_briefing_index
from app.services.briefing.refresh import enqueue_ready_source, run_briefing_refresh


def test_ready_sources_pull_the_debounced_refresh_forward_at_three(
    db_session: Session,
    test_user: User,
) -> None:
    assert test_user.id is not None
    user_id = test_user.id
    settings = get_settings().model_copy(
        update={
            "briefing_debounce_seconds": 900,
            "briefing_new_lens_min_items": 3,
            "briefing_window_min": 3,
        }
    )
    started_at = datetime.now(UTC).replace(tzinfo=None)

    for source_id in (101, 102):
        assert enqueue_ready_source(
            db_session,
            user_id=user_id,
            source_kind="news",
            source_id=source_id,
            settings=settings,
        )

    task = (
        db_session.query(ProcessingTask)
        .filter(ProcessingTask.dedupe_key == f"briefing_refresh:{user_id}:append")
        .one()
    )
    assert task.available_at is not None
    assert task.available_at >= started_at + timedelta(seconds=890)

    ready_at = datetime.now(UTC).replace(tzinfo=None)
    assert enqueue_ready_source(
        db_session,
        user_id=user_id,
        source_kind="news",
        source_id=103,
        settings=settings,
    )
    db_session.expire_all()
    task = (
        db_session.query(ProcessingTask)
        .filter(ProcessingTask.dedupe_key == f"briefing_refresh:{user_id}:append")
        .one()
    )

    assert task.available_at is not None
    assert task.available_at <= ready_at + timedelta(seconds=5)


def test_append_assigns_all_news_but_publishes_one_window(
    db_session: Session,
    test_user: User,
    news_item_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert test_user.id is not None
    user_id = test_user.id
    settings = get_settings().model_copy(
        update={
            "briefing_enabled_user_ids": [user_id],
            "briefing_new_lens_min_items": 3,
            "briefing_news_window_max": 4,
            "briefing_window_min": 3,
        }
    )
    enqueued_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
    items = [
        news_item_factory(
            visibility_scope="user",
            owner_user_id=user_id,
            article_title=f"Progressive category story {index}",
            summary_title=f"Progressive category story {index}",
        )
        for index in range(8)
    ]
    db_session.add_all(
        [
            BriefingPendingSource(
                user_id=user_id,
                source_kind="news",
                source_id=item.id,
                enqueued_at=enqueued_at + timedelta(microseconds=index),
            )
            for index, item in enumerate(items)
        ]
    )
    start_first_edition(db_session, user_id=user_id)
    db_session.commit()

    from app.services.briefing import window_composition

    original_compose = window_composition.compose_window_groups

    def inspect_progress(*args, **kwargs):  # noqa: ANN002, ANN003
        index = get_briefing_index(db_session, user_id=user_id)
        assert index.version == 0
        assert [(lens.key, lens.segment_count) for lens in index.lenses] == [
            ("news-progressive", 0)
        ]
        assert index.first_run is not None
        assert index.first_run.revision == 2
        assert index.first_run.ready_category_keys == []
        return original_compose(*args, **kwargs)

    monkeypatch.setattr(window_composition, "compose_window_groups", inspect_progress)

    result = run_briefing_refresh(
        db_session,
        user_id=user_id,
        mode="append",
        use_llm=False,
        settings=settings,
    )
    db_session.commit()

    assert result.appended_segments == 1
    assert result.version == 1
    segment = db_session.query(BriefingSegment).filter_by(user_id=user_id).one()
    assert segment.source_keys == [f"news:{item.id}" for item in items[:4]]
    remaining = (
        db_session.query(BriefingPendingSource)
        .filter_by(user_id=user_id)
        .order_by(BriefingPendingSource.id.asc())
        .all()
    )
    assert [row.source_id for row in remaining] == [item.id for item in items[4:]]
    assert {row.lens_key for row in remaining} == {"news-progressive"}


def test_append_publishes_preassigned_first_run_category_via_revision(
    db_session: Session,
    test_user: User,
    content_factory,
    status_entry_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert test_user.id is not None
    user_id = test_user.id
    settings = get_settings().model_copy(update={"briefing_enabled_user_ids": [user_id]})
    content = _create_unread_article(
        content_factory,
        status_entry_factory,
        test_user,
        index=1,
    )
    db_session.add(
        BriefingPendingSource(
            user_id=user_id,
            lens_key="articles",
            source_kind="content",
            source_id=content.id,
        )
    )
    start_first_edition(db_session, user_id=user_id)
    db_session.commit()

    from app.services.briefing import window_composition

    original_compose = window_composition.compose_window_groups

    def inspect_progress(*args, **kwargs):  # noqa: ANN002, ANN003
        index = get_briefing_index(db_session, user_id=user_id)
        assert index.version == 0
        assert [(lens.key, lens.segment_count) for lens in index.lenses] == [("articles", 0)]
        assert index.first_run is not None
        assert index.first_run.revision == 2
        return original_compose(*args, **kwargs)

    monkeypatch.setattr(window_composition, "compose_window_groups", inspect_progress)

    result = run_briefing_refresh(
        db_session,
        user_id=user_id,
        mode="append",
        use_llm=False,
        settings=settings,
    )

    assert result.appended_segments == 1
    assert result.version == 1


def test_append_updates_existing_news_category_one_window_at_a_time(
    db_session: Session,
    test_user: User,
    news_item_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert test_user.id is not None
    user_id = test_user.id
    settings = get_settings().model_copy(
        update={
            "briefing_enabled_user_ids": [user_id],
            "briefing_news_window_max": 4,
            "briefing_window_min": 3,
        }
    )
    lens = _create_lens(db_session, user_id=user_id, key="news-progressive")
    enqueued_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
    items = [
        news_item_factory(
            visibility_scope="user",
            owner_user_id=user_id,
            article_title=f"Existing category story {index}",
            summary_title=f"Existing category story {index}",
        )
        for index in range(8)
    ]
    db_session.add_all(
        [
            BriefingPendingSource(
                user_id=user_id,
                lens_key=lens.key,
                source_kind="news",
                source_id=item.id,
                enqueued_at=enqueued_at + timedelta(microseconds=index),
            )
            for index, item in enumerate(items)
        ]
    )
    db_session.commit()

    from app.services.briefing import window_composition

    original_compose = window_composition.compose_window_groups

    def inspect_unpublished_version(*args, **kwargs):  # noqa: ANN002, ANN003
        assert get_briefing_index(db_session, user_id=user_id).version == 0
        return original_compose(*args, **kwargs)

    monkeypatch.setattr(
        window_composition,
        "compose_window_groups",
        inspect_unpublished_version,
    )
    first = run_briefing_refresh(
        db_session,
        user_id=user_id,
        mode="append",
        use_llm=False,
        settings=settings,
    )
    db_session.commit()

    assert first.appended_segments == 1
    assert first.version == 1
    assert db_session.query(BriefingPendingSource).filter_by(user_id=user_id).count() == 4

    monkeypatch.setattr(window_composition, "compose_window_groups", original_compose)
    second = run_briefing_refresh(
        db_session,
        user_id=user_id,
        mode="sweep",
        use_llm=False,
        settings=settings,
    )
    db_session.commit()

    assert second.appended_segments == 1
    assert second.version == 2
    assert db_session.query(BriefingPendingSource).filter_by(user_id=user_id).count() == 0
    source_key_groups = [
        segment.source_keys
        for segment in db_session.query(BriefingSegment)
        .filter_by(user_id=user_id, lens_id=lens.id)
        .order_by(BriefingSegment.id.asc())
        .all()
    ]
    assert source_key_groups == [
        [f"news:{item.id}" for item in items[:4]],
        [f"news:{item.id}" for item in items[4:]],
    ]


def test_append_composes_one_article_segment_at_a_time(
    db_session: Session,
    test_user: User,
    content_factory,
    status_entry_factory,
) -> None:
    assert test_user.id is not None
    user_id = test_user.id
    settings = get_settings().model_copy(update={"briefing_enabled_user_ids": [user_id]})
    for index in range(8):
        _create_unread_article(
            content_factory,
            status_entry_factory,
            test_user,
            index=index,
        )

    first = run_briefing_refresh(
        db_session,
        user_id=user_id,
        mode="append",
        use_llm=False,
        settings=settings,
    )
    db_session.commit()

    assert first.appended_segments == 1
    assert (
        db_session.query(BriefingPendingSource)
        .filter_by(user_id=user_id, lens_key="articles")
        .count()
        == 7
    )

    second = run_briefing_refresh(
        db_session,
        user_id=user_id,
        mode="sweep",
        use_llm=False,
        settings=settings,
    )
    db_session.commit()

    assert second.appended_segments == 1
    assert (
        db_session.query(BriefingPendingSource)
        .filter_by(user_id=user_id, lens_key="articles")
        .count()
        == 6
    )


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
