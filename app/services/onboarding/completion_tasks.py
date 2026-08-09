"""Task-batch construction for onboarding completion."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.constants import DEFAULT_INITIAL_FEED_ARTICLE_DOWNLOAD_COUNT
from app.models.db import ProcessingTask
from app.models.internal.feed_backfill import FeedBatchBackfillRequest
from app.services.long_form_images import build_visible_long_form_image_task_requests
from app.services.queue import TaskEnqueueRequest, TaskType


def _has_feed_discovery_task(db: Session, *, user_id: int) -> bool:
    return (
        db.query(ProcessingTask.id)
        .filter(
            ProcessingTask.owner_user_id == user_id,
            ProcessingTask.task_type == TaskType.DISCOVER_FEEDS.value,
            ProcessingTask.status.in_(("pending", "processing", "completed")),
        )
        .first()
        is not None
    )


def build_onboarding_completion_task_batch(
    db: Session,
    *,
    user_id: int,
    feed_config_ids: list[int],
    sources_to_scrape: list[str],
    first_edition_run_id: int,
    discovery_payload: dict[str, Any] | None,
    seeded_feed_content_ids: list[int],
) -> tuple[list[TaskEnqueueRequest], int | None]:
    """Build the atomic task batch and locate the primary response task."""
    requests: list[TaskEnqueueRequest] = []
    response_task_index: int | None = None

    if feed_config_ids:
        response_task_index = len(requests)
        requests.append(
            TaskEnqueueRequest(
                TaskType.BACKFILL_FEEDS,
                payload=FeedBatchBackfillRequest(
                    user_id=user_id,
                    config_ids=feed_config_ids,
                    count=DEFAULT_INITIAL_FEED_ARTICLE_DOWNLOAD_COUNT,
                    first_edition_run_id=first_edition_run_id,
                ).model_dump(exclude_none=True),
                dedupe=True,
                owner_user_id=user_id,
            )
        )

    if sources_to_scrape:
        if response_task_index is None:
            response_task_index = len(requests)
        requests.append(
            TaskEnqueueRequest(
                TaskType.SCRAPE,
                payload={
                    "sources": sources_to_scrape,
                    "first_edition_run_id": first_edition_run_id,
                },
                access_user_id=user_id,
            )
        )

    if discovery_payload is not None:
        requests.append(
            TaskEnqueueRequest(
                TaskType.ONBOARDING_DISCOVER,
                payload=discovery_payload,
                owner_user_id=user_id,
            )
        )

    if not _has_feed_discovery_task(db, user_id=user_id):
        if response_task_index is None:
            response_task_index = len(requests)
        requests.append(
            TaskEnqueueRequest(
                TaskType.DISCOVER_FEEDS,
                payload={"user_id": user_id, "trigger": "onboarding"},
                dedupe=True,
                owner_user_id=user_id,
            )
        )

    requests.extend(build_visible_long_form_image_task_requests(db, seeded_feed_content_ids))
    return requests, response_task_index
