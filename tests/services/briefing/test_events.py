from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.models.contracts import ContentClassification, ContentType, TaskStatus, TaskType
from app.models.db import BriefingPendingSource, ProcessingTask, UserScraperConfig
from app.services.agent_data_events import enqueue_agent_data_sync
from app.services.briefing import events
from app.services.briefing.events import (
    enqueue_content_for_briefing_if_ready,
    enqueue_news_item_for_briefing_if_ready,
)
from app.services.gateways.task_queue_gateway import get_task_queue_gateway


def test_enqueue_content_for_briefing_accepts_null_classification(
    db_session: Session,
    test_user,
    content_factory,
    status_entry_factory,
    monkeypatch,
) -> None:
    settings = get_settings()
    assert test_user.id is not None
    monkeypatch.setattr(settings, "briefing_enabled_user_ids", [])
    test_user.reading_experience = "briefing"
    db_session.flush()
    content = content_factory(
        content_type=ContentType.PODCAST,
        title="Unclassified episode",
        classification=None,
    )
    status_entry_factory(user=test_user, content=content, status="inbox")
    gateway = get_task_queue_gateway()
    calls: list[list] = []

    class SpyGateway:
        def enqueue_many_in_session(self, db, requests):  # noqa: ANN001
            calls.append(list(requests))
            return gateway.enqueue_many_in_session(db, requests)

    monkeypatch.setattr(events, "get_task_queue_gateway", lambda: SpyGateway())

    enqueued = enqueue_content_for_briefing_if_ready(
        db_session,
        content_id=content.id,
        settings=settings,
    )

    assert enqueued == 1
    db_session.flush()
    pending = db_session.query(BriefingPendingSource).one()
    assert pending.source_kind == "content"
    assert pending.source_id == content.id
    assert pending.lens_key == "podcasts"
    assert len(calls) == 1
    assert {request.task_type for request in calls[0]} == {
        TaskType.SYNC_AGENT_DATA,
        TaskType.BRIEFING_REFRESH,
    }


def test_enqueue_content_for_briefing_excludes_skip_classification(
    db_session: Session,
    test_user,
    content_factory,
    status_entry_factory,
    monkeypatch,
) -> None:
    settings = get_settings()
    assert test_user.id is not None
    monkeypatch.setattr(settings, "briefing_enabled_user_ids", [test_user.id])
    content = content_factory(
        content_type=ContentType.ARTICLE,
        title="Skipped article",
        classification=ContentClassification.SKIP.value,
    )
    status_entry_factory(user=test_user, content=content, status="inbox")

    enqueued = enqueue_content_for_briefing_if_ready(
        db_session,
        content_id=content.id,
        settings=settings,
    )

    assert enqueued == 0
    assert db_session.query(BriefingPendingSource).count() == 0


def test_news_fanout_resolves_visibility_once_and_enqueues_one_batch(
    db_session: Session,
    test_user,
    user_factory,
    news_item_factory,
    monkeypatch,
) -> None:
    hidden_user = user_factory()
    db_session.add_all(
        [
            UserScraperConfig(
                user_id=test_user.id,
                scraper_type="aggregator",
                config={"key": "hackernews", "topics": []},
                is_active=True,
            ),
            UserScraperConfig(
                user_id=hidden_user.id,
                scraper_type="aggregator",
                config={"key": "brutalist", "topics": []},
                is_active=True,
            ),
        ]
    )
    item = news_item_factory(platform="hackernews", visibility_scope="global")
    db_session.flush()

    processing_sync_id = enqueue_agent_data_sync(
        db_session,
        user_id=test_user.id,
        news_item_ids=(item.id,),
    )
    processing_sync = db_session.get(ProcessingTask, processing_sync_id)
    assert processing_sync is not None
    base_sync_key = str(processing_sync.dedupe_key)
    processing_sync.status = TaskStatus.PROCESSING.value
    db_session.flush()

    gateway = get_task_queue_gateway()
    calls: list[list] = []

    class SpyGateway:
        def enqueue_many_in_session(self, db, requests):  # noqa: ANN001
            calls.append(list(requests))
            return gateway.enqueue_many_in_session(db, requests)

    monkeypatch.setattr(events, "get_task_queue_gateway", lambda: SpyGateway())

    enqueued = enqueue_news_item_for_briefing_if_ready(
        db_session,
        news_item_id=item.id,
        settings=get_settings(),
    )

    assert enqueued == 1
    assert len(calls) == 1
    assert len(calls[0]) == 2
    pending = db_session.query(BriefingPendingSource).one()
    assert pending.user_id == test_user.id
    assert pending.source_kind == "news"
    assert pending.source_id == item.id
    tasks = db_session.query(ProcessingTask).all()
    assert {task.task_type for task in tasks} == {
        TaskType.SYNC_AGENT_DATA.value,
        TaskType.BRIEFING_REFRESH.value,
    }
    assert {task.owner_user_id for task in tasks} == {test_user.id}
    sync_tasks = sorted(
        (task for task in tasks if task.task_type == TaskType.SYNC_AGENT_DATA.value),
        key=lambda task: task.id if task.id is not None else -1,
    )
    assert len(sync_tasks) == 2
    assert sync_tasks[1].dedupe_key == f"{base_sync_key}|after:{processing_sync_id}"
