from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.settings import Settings, get_settings
from app.models.contracts import (
    ContentClassification,
    ContentStatus,
    ContentType,
    NewsItemStatus,
)
from app.models.db import (
    ChatSession,
    Content,
    ContentKnowledgeSave,
    ContentReadStatus,
    ContentStatusEntry,
    NewsItem,
    NewsItemReadStatus,
)
from app.services.agent_data_events import (
    build_agent_data_sync_request,
    prepare_agent_data_sync_requests,
)
from app.services.briefing.eligibility import briefing_enabled_user_ids
from app.services.briefing.refresh import (
    build_ready_source_refresh_requests,
    expedite_pending_refreshes,
    insert_pending_sources,
)
from app.services.gateways.task_queue_gateway import get_task_queue_gateway
from app.services.news_feed import visible_user_ids_for_news_item


def enqueue_content_for_briefing_if_ready(
    db: Session,
    *,
    content_id: int,
    settings: Settings | None = None,
) -> int:
    settings = settings or get_settings()
    content = db.query(Content).filter(Content.id == content_id).first()
    if content is None:
        return 0
    if content.status != ContentStatus.COMPLETED.value:
        return 0

    status_rows = (
        db.query(ContentStatusEntry.user_id, ContentStatusEntry.status)
        .filter(ContentStatusEntry.content_id == content_id)
        .all()
    )
    related_user_rows = (
        db.query(ContentKnowledgeSave.user_id)
        .filter(ContentKnowledgeSave.content_id == content_id)
        .union(db.query(ChatSession.user_id).filter(ChatSession.content_id == content_id))
        .all()
    )
    status_user_ids = {
        int(user_id)
        for user_id, _status in status_rows
        if isinstance(user_id, int) and not isinstance(user_id, bool)
    }
    related_user_ids = {
        int(user_id)
        for (user_id,) in related_user_rows
        if isinstance(user_id, int) and not isinstance(user_id, bool)
    }
    all_user_ids = status_user_ids | related_user_ids
    agent_requests = prepare_agent_data_sync_requests(
        db,
        [
            build_agent_data_sync_request(user_id=user_id, content_ids=(content_id,))
            for user_id in sorted(all_user_ids)
        ],
    )

    if content.classification == ContentClassification.SKIP.value or content.content_type not in {
        ContentType.ARTICLE.value,
        ContentType.PODCAST.value,
    }:
        if agent_requests:
            get_task_queue_gateway().enqueue_many_in_session(db, agent_requests)
        return 0

    lens_key = "podcasts" if content.content_type == ContentType.PODCAST.value else "articles"
    candidate_user_ids = {
        int(user_id)
        for user_id, status in status_rows
        if isinstance(user_id, int) and status == "inbox"
    }
    eligible_user_ids = briefing_enabled_user_ids(
        db,
        candidate_user_ids=candidate_user_ids,
        settings=settings,
    )
    read_user_ids = {
        int(user_id)
        for (user_id,) in db.query(ContentReadStatus.user_id)
        .filter(
            ContentReadStatus.content_id == content_id,
            ContentReadStatus.user_id.in_(eligible_user_ids),
        )
        .all()
    }
    briefing_user_ids = eligible_user_ids.difference(read_user_ids)
    inserted_user_ids = insert_pending_sources(
        db,
        user_ids=briefing_user_ids,
        source_kind="content",
        source_id=content_id,
        lens_key=lens_key,
    )
    refresh_requests = build_ready_source_refresh_requests(
        db,
        user_ids=briefing_user_ids,
        lens_key=lens_key,
        settings=settings,
    )
    requests = [*agent_requests, *refresh_requests]
    if requests:
        task_ids = get_task_queue_gateway().enqueue_many_in_session(db, requests)
        expedite_pending_refreshes(
            db,
            requests=refresh_requests,
            task_ids=task_ids[len(agent_requests) :],
        )
    return len(inserted_user_ids)


def enqueue_news_item_for_briefing_if_ready(
    db: Session,
    *,
    news_item_id: int,
    settings: Settings | None = None,
) -> int:
    settings = settings or get_settings()
    item = db.query(NewsItem).filter(NewsItem.id == news_item_id).first()
    if (
        item is None
        or item.status != NewsItemStatus.READY.value
        or item.representative_news_item_id is not None
    ):
        return 0

    visible_user_ids = visible_user_ids_for_news_item(db, item=item)
    if not visible_user_ids:
        return 0
    read_user_ids = {
        int(user_id)
        for (user_id,) in db.query(NewsItemReadStatus.user_id)
        .filter(
            NewsItemReadStatus.news_item_id == news_item_id,
            NewsItemReadStatus.user_id.in_(visible_user_ids),
        )
        .all()
    }
    briefing_user_ids = briefing_enabled_user_ids(
        db,
        candidate_user_ids=visible_user_ids,
        settings=settings,
    ).difference(read_user_ids)
    inserted_user_ids = insert_pending_sources(
        db,
        user_ids=briefing_user_ids,
        source_kind="news",
        source_id=news_item_id,
        lens_key=None,
    )

    agent_requests = prepare_agent_data_sync_requests(
        db,
        [
            build_agent_data_sync_request(
                user_id=user_id,
                news_item_ids=(news_item_id,),
            )
            for user_id in sorted(visible_user_ids)
        ],
    )
    refresh_requests = build_ready_source_refresh_requests(
        db,
        user_ids=briefing_user_ids,
        lens_key=None,
        settings=settings,
    )
    requests = [*agent_requests, *refresh_requests]
    task_ids = get_task_queue_gateway().enqueue_many_in_session(db, requests)
    expedite_pending_refreshes(
        db,
        requests=refresh_requests,
        task_ids=task_ids[len(agent_requests) :],
    )
    return len(inserted_user_ids)
