"""List processing status for the current user's submitted content."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.api.content import (
    DetectedFeed,
    SubmissionFeedInitialDownloadResponse,
    SubmissionFeedSubscriptionResponse,
    SubmissionKind,
    SubmissionOutcome,
    SubmissionStatusListResponse,
    SubmissionStatusResponse,
)
from app.models.api.pagination import PaginationMetadata
from app.models.contracts import (
    ContentStatus,
    ContentType,
    LearningDeckRunStatus,
    LlmTaskActionStatus,
    LlmTaskKind,
    LlmTaskMode,
    LlmTaskStatus,
    TaskStatus,
    TaskType,
)
from app.models.db import (
    Content,
    LearningDeck,
    LearningDeckRun,
    LlmTask,
    LlmTaskAction,
    ProcessingTask,
)
from app.models.metadata.access import ContentMetadataView, metadata_view
from app.utils.pagination import PaginationCursor

logger = get_logger(__name__)

LEARNING_DECK_ACTIVE_STATUSES = {
    LearningDeckRunStatus.PREPARING,
    LearningDeckRunStatus.GENERATING,
    LearningDeckRunStatus.VALIDATING,
    LearningDeckRunStatus.PUBLISHING,
}
LLM_TASK_ACTIVE_STATUSES = {
    LlmTaskStatus.RUNNING,
    LlmTaskStatus.AWAITING_APPROVAL,
    LlmTaskStatus.APPLYING,
}


def execute(
    db: Session,
    *,
    user_id: int,
    cursor: str | None,
    limit: int,
) -> SubmissionStatusListResponse:
    """Return ShareSheet submissions anchored on their Share Action LLM tasks."""
    last_id = None
    last_created_at = None
    if cursor:
        cursor_data = PaginationCursor.decode_cursor(cursor)
        last_id = cursor_data.last_id
        last_created_at = cursor_data.last_created_at

    query = (
        db.query(LlmTask)
        .filter(
            LlmTask.user_id == user_id,
            LlmTask.task_kind == LlmTaskKind.SHARE_ACTION.value,
        )
        .order_by(LlmTask.created_at.desc(), LlmTask.id.desc())
    )
    if last_id is not None and last_created_at is not None:
        query = query.filter(
            or_(
                LlmTask.created_at < last_created_at,
                and_(LlmTask.created_at == last_created_at, LlmTask.id < last_id),
            )
        )

    tasks = query.limit(limit + 1).all()
    has_more = len(tasks) > limit
    if has_more:
        tasks = tasks[:limit]

    actions_by_task_id = _actions_by_task_id(db, tasks)
    content_by_id = _content_targets_by_id(db, actions_by_task_id)
    initial_download_tasks_by_id = _initial_download_tasks_by_id(
        db,
        user_id=user_id,
        contents=content_by_id.values(),
    )
    deck_targets_by_id = _learning_deck_targets_by_id(
        db,
        user_id=user_id,
        actions_by_task_id=actions_by_task_id,
    )

    submissions = [
        submission
        for task in tasks
        if (
            submission := _build_task_submission_response(
                task,
                actions=actions_by_task_id.get(_require_task_id(task.id), []),
                content_by_id=content_by_id,
                deck_targets_by_id=deck_targets_by_id,
                initial_download_tasks_by_id=initial_download_tasks_by_id,
            )
        )
        is not None
    ]

    next_cursor = None
    if has_more and tasks:
        last_item = tasks[-1]
        if last_item.created_at is None:
            raise ValueError("Submission task is missing created_at")
        next_cursor = PaginationCursor.encode_cursor(
            last_id=_require_task_id(last_item.id),
            last_created_at=last_item.created_at,
            filters={},
        )

    return SubmissionStatusListResponse(
        submissions=submissions,
        meta=PaginationMetadata(
            next_cursor=next_cursor,
            has_more=has_more,
            page_size=len(submissions),
            total=len(submissions),
        ),
    )


def _actions_by_task_id(
    db: Session,
    tasks: list[LlmTask],
) -> dict[int, list[LlmTaskAction]]:
    task_ids = [_require_task_id(task.id) for task in tasks]
    if not task_ids:
        return {}
    actions = (
        db.query(LlmTaskAction)
        .filter(LlmTaskAction.llm_task_id.in_(task_ids))
        .order_by(LlmTaskAction.created_at, LlmTaskAction.id)
        .all()
    )
    by_task_id: dict[int, list[LlmTaskAction]] = {task_id: [] for task_id in task_ids}
    for action in actions:
        task_id = _int_or_none(action.llm_task_id)
        if task_id is not None:
            by_task_id.setdefault(task_id, []).append(action)
    return by_task_id


def _content_targets_by_id(
    db: Session,
    actions_by_task_id: dict[int, list[LlmTaskAction]],
) -> dict[int, Content]:
    content_ids = {
        content_id
        for actions in actions_by_task_id.values()
        for action in actions
        if (content_id := _action_content_id(action)) is not None
    }
    if not content_ids:
        return {}
    return {
        _require_content_id(content.id): content
        for content in db.query(Content).filter(Content.id.in_(content_ids)).all()
    }


def _learning_deck_targets_by_id(
    db: Session,
    *,
    user_id: int,
    actions_by_task_id: dict[int, list[LlmTaskAction]],
) -> dict[int, tuple[LearningDeck, LearningDeckRun | LlmTask]]:
    deck_ids = {
        deck_id
        for actions in actions_by_task_id.values()
        for action in actions
        if (deck_id := _action_learning_deck_id(action)) is not None
    }
    if not deck_ids:
        return {}
    decks = (
        db.query(LearningDeck)
        .filter(
            LearningDeck.id.in_(deck_ids),
            LearningDeck.user_id == user_id,
            LearningDeck.deleted_at.is_(None),
        )
        .all()
    )
    task_ids = {int(deck.latest_task_id) for deck in decks if deck.latest_task_id is not None}
    run_ids = {
        int(deck.latest_run_id)
        for deck in decks
        if deck.latest_task_id is None and deck.latest_run_id is not None
    }
    tasks = {
        int(task.id): task
        for task in db.query(LlmTask).filter(LlmTask.id.in_(task_ids)).all()
        if task.id is not None
    }
    runs = {
        int(run.id): run
        for run in db.query(LearningDeckRun).filter(LearningDeckRun.id.in_(run_ids)).all()
        if run.id is not None
    }
    targets: dict[int, tuple[LearningDeck, LearningDeckRun | LlmTask]] = {}
    for deck in decks:
        attempt = (
            tasks.get(int(deck.latest_task_id))
            if deck.latest_task_id is not None
            else runs.get(int(deck.latest_run_id))
            if deck.latest_run_id is not None
            else None
        )
        if deck.id is not None and attempt is not None:
            targets[int(deck.id)] = (deck, attempt)
    return targets


def _initial_download_tasks_by_id(
    db: Session,
    *,
    user_id: int,
    contents: Iterable[Content],
) -> dict[int, ProcessingTask]:
    task_ids = {
        task_id
        for content in contents
        if (task_id := _initial_download_task_id(content)) is not None
    }
    if not task_ids:
        return {}
    return {
        int(task.id): task
        for task in (
            db.query(ProcessingTask)
            .filter(
                ProcessingTask.id.in_(task_ids),
                ProcessingTask.owner_user_id == user_id,
                ProcessingTask.task_type == TaskType.BACKFILL_FEEDS.value,
            )
            .all()
        )
        if task.id is not None
    }


def _initial_download_task_id(content: Content) -> int | None:
    metadata = metadata_view(content.content_metadata or {})
    raw_subscription = _dict_or_none(metadata.processing_flag("feed_subscription"))
    if raw_subscription is None:
        return None
    raw_initial_download = _dict_or_none(raw_subscription.get("initial_download"))
    if raw_initial_download is None:
        return None
    return _int_or_none(raw_initial_download.get("task_id"))


def _build_task_submission_response(
    task: LlmTask,
    *,
    actions: list[LlmTaskAction],
    content_by_id: dict[int, Content],
    deck_targets_by_id: dict[int, tuple[LearningDeck, LearningDeckRun | LlmTask]],
    initial_download_tasks_by_id: dict[int, ProcessingTask],
) -> SubmissionStatusResponse | None:
    try:
        action = _primary_action(actions)
        content_id = _action_content_id(action) if action else None
        if content_id is not None and content_id in content_by_id:
            return _build_content_target_submission_response(
                task,
                action=action,
                content=content_by_id[content_id],
                initial_download_tasks_by_id=initial_download_tasks_by_id,
            )

        deck_id = _action_learning_deck_id(action) if action else None
        if deck_id is not None and deck_id in deck_targets_by_id:
            deck, run = deck_targets_by_id[deck_id]
            return _build_learning_deck_submission_response(task, deck, run)

        return _build_llm_task_submission_response(task, action=action)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Skipping Share Action submission %s due to validation error: %s",
            task.id,
            exc,
            extra={
                "component": "submission_status",
                "operation": "list_share_action_submissions",
                "item_id": task.id,
                "context_data": {"llm_task_id": task.id},
            },
        )
        return None


def _primary_action(actions: list[LlmTaskAction]) -> LlmTaskAction | None:
    applied_actions = [
        action for action in actions if action.action_status == LlmTaskActionStatus.APPLIED.value
    ]
    if applied_actions:
        return applied_actions[-1]
    return actions[-1] if actions else None


def _action_content_id(action: LlmTaskAction) -> int | None:
    action_result = action.action_result if isinstance(action.action_result, dict) else {}
    return _int_or_none(action_result.get("content_id"))


def _action_learning_deck_id(action: LlmTaskAction) -> int | None:
    action_result = action.action_result if isinstance(action.action_result, dict) else {}
    return _int_or_none(action_result.get("learning_deck_id"))


def _build_content_target_submission_response(
    task: LlmTask,
    *,
    action: LlmTaskAction | None,
    content: Content,
    initial_download_tasks_by_id: dict[int, ProcessingTask],
) -> SubmissionStatusResponse:
    metadata = metadata_view(content.content_metadata or {})
    raw_content_type = content.content_type
    raw_status = content.status
    if raw_content_type is None or raw_status is None:
        raise ValueError("Submission content target is missing required fields")
    detected_feed = _build_detected_feed(metadata.detected_feed())
    feed_subscription = _build_feed_subscription(
        _dict_or_none(metadata.processing_flag("feed_subscription")),
        initial_download_tasks_by_id=initial_download_tasks_by_id,
    )
    submission_kind: SubmissionKind = (
        SubmissionKind.FEED_SUBSCRIPTION
        if _is_feed_subscription_submission(metadata, feed_subscription, detected_feed)
        else SubmissionKind.CONTENT
    )
    status = ContentStatus(raw_status)
    return SubmissionStatusResponse(
        id=_require_task_id(task.id),
        content_type=ContentType(raw_content_type),
        url=str(content.url),
        source_url=content.source_url,
        title=content.title,
        status=status,
        error_message=content.error_message or _action_error_message(action) or task.error_message,
        created_at=_require_datetime(task.created_at, "Share Action created_at").isoformat(),
        processed_at=_target_processed_at(content.processed_at, action, task),
        submitted_via="share_action",
        is_self_submission=True,
        submission_kind=submission_kind,
        outcome=_resolve_submission_outcome(
            status=status,
            submission_kind=submission_kind,
            feed_subscription=feed_subscription,
        ),
        detected_feed=detected_feed,
        feed_subscription=feed_subscription,
    )


def _build_llm_task_submission_response(
    task: LlmTask,
    *,
    action: LlmTaskAction | None,
) -> SubmissionStatusResponse:
    task_status = LlmTaskStatus(str(task.status))
    content_status = _llm_task_content_status(task_status)
    no_action_rationale = _completed_no_action_rationale(task, task_status=task_status)
    return SubmissionStatusResponse(
        id=_require_task_id(task.id),
        content_type=_task_content_type(task),
        url=_task_url(task, action),
        source_url=_task_source_url(task, action),
        title=_task_title(task, action),
        status=content_status,
        error_message=_action_error_message(action) or task.error_message,
        created_at=_require_datetime(task.created_at, "Share Action created_at").isoformat(),
        processed_at=task.completed_at.isoformat() if task.completed_at else None,
        submitted_via="share_action",
        is_self_submission=True,
        submission_kind=_task_submission_kind(task, action),
        outcome=(
            SubmissionOutcome.NO_ACTION
            if no_action_rationale is not None
            else _llm_task_outcome(task_status)
        ),
        rationale=no_action_rationale,
        detected_feed=None,
        feed_subscription=None,
    )


def _build_learning_deck_submission_response(
    task: LlmTask,
    deck: LearningDeck,
    run: LearningDeckRun | LlmTask,
) -> SubmissionStatusResponse:
    raw_status = run.status
    if raw_status is None:
        raise ValueError("Learning Deck run is missing status")
    deck_status = LearningDeckRunStatus(_learning_deck_attempt_status(raw_status))
    content_status = _learning_deck_content_status(deck_status)
    title = _clean_string(deck.source_title) or _clean_string(deck.title)
    source_url = _clean_string(deck.source_url)
    return SubmissionStatusResponse(
        id=_require_task_id(task.id),
        content_type=ContentType.UNKNOWN,
        url=source_url or _task_url(task, None),
        source_url=source_url,
        title=title or "Learning Deck",
        status=content_status,
        error_message=run.error_message or task.error_message,
        created_at=_require_datetime(task.created_at, "Share Action created_at").isoformat(),
        processed_at=_target_processed_at(run.completed_at, None, task),
        submitted_via="share_action",
        is_self_submission=True,
        submission_kind=SubmissionKind.LEARNING_DECK,
        outcome=_learning_deck_outcome(deck_status),
        detected_feed=None,
        feed_subscription=None,
    )


def _require_content_id(content_id: int | None) -> int:
    if content_id is None:
        raise ValueError("Content is missing an id")
    return content_id


def _require_task_id(task_id: int | None) -> int:
    if task_id is None:
        raise ValueError("Share Action task is missing an id")
    return int(task_id)


def _require_datetime(value: datetime | None, label: str) -> datetime:
    if value is None:
        raise ValueError(f"{label} is missing")
    return value


def _target_processed_at(
    target_processed_at: datetime | None,
    action: LlmTaskAction | None,
    task: LlmTask,
) -> str | None:
    if target_processed_at is not None:
        return target_processed_at.isoformat()
    if action is not None and action.completed_at is not None:
        return action.completed_at.isoformat()
    if task.completed_at is not None:
        return task.completed_at.isoformat()
    return None


def _action_error_message(action: LlmTaskAction | None) -> str | None:
    if action is None:
        return None
    return _clean_string(action.error_message)


def _task_submission_kind(
    task: LlmTask,
    action: LlmTaskAction | None,
) -> SubmissionKind:
    mode = str(task.mode)
    action_name = str(action.action_name) if action is not None else ""
    if mode == LlmTaskMode.PRESENTATION.value or action_name == "create_learning_deck":
        return SubmissionKind.LEARNING_DECK
    if mode == LlmTaskMode.ADD_FEED.value or action_name == "subscribe_to_feed":
        return SubmissionKind.FEED_SUBSCRIPTION
    return SubmissionKind.CONTENT


def _task_content_type(task: LlmTask) -> ContentType:
    value = _clean_string(_task_output(task).get("content_type"))
    if value is None:
        return ContentType.UNKNOWN
    try:
        return ContentType(value)
    except ValueError:
        return ContentType.UNKNOWN


def _task_url(task: LlmTask, action: LlmTaskAction | None) -> str:
    action_input = _action_input(action)
    action_result = _action_result(action)
    for value in (
        _task_input(task).get("url"),
        action_input.get("source_url"),
        action_input.get("url"),
        _task_output(task).get("primary_url"),
        action_result.get("source_url"),
    ):
        url = _clean_string(value)
        if url:
            return url
    return f"newsly://share-actions/{_require_task_id(task.id)}"


def _task_source_url(task: LlmTask, action: LlmTaskAction | None) -> str | None:
    url = _task_url(task, action)
    if url.startswith("newsly://"):
        return None
    return url


def _task_title(task: LlmTask, action: LlmTaskAction | None) -> str | None:
    action_input = _action_input(action)
    output_json = _task_output(task)
    presentation = _dict_or_none(output_json.get("presentation")) or {}
    for value in (
        action_input.get("title"),
        output_json.get("title"),
        presentation.get("title"),
    ):
        title = _clean_string(value)
        if title:
            return title
    return None


def _task_input(task: LlmTask) -> dict[str, Any]:
    return task.input_json if isinstance(task.input_json, dict) else {}


def _task_output(task: LlmTask) -> dict[str, Any]:
    return task.output_json if isinstance(task.output_json, dict) else {}


def _completed_no_action_rationale(
    task: LlmTask,
    *,
    task_status: LlmTaskStatus,
) -> str | None:
    if task_status != LlmTaskStatus.COMPLETED:
        return None
    output = _task_output(task)
    if output.get("action") != "no_action":
        return None
    return _clean_string(output.get("rationale")) or "Newsly could not find an action to take."


def _action_input(action: LlmTaskAction | None) -> dict[str, Any]:
    if action is None or not isinstance(action.action_input, dict):
        return {}
    return action.action_input


def _action_result(action: LlmTaskAction | None) -> dict[str, Any]:
    if action is None or not isinstance(action.action_result, dict):
        return {}
    return action.action_result


def _build_detected_feed(raw_feed: dict[str, Any] | None) -> DetectedFeed | None:
    if not raw_feed:
        return None
    try:
        return DetectedFeed.model_validate(raw_feed)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Ignoring invalid detected feed metadata: %s",
            exc,
            extra={
                "component": "submission_status",
                "operation": "build_detected_feed",
            },
        )
        return None


def _build_feed_subscription(
    raw_subscription: dict[str, Any] | None,
    *,
    initial_download_tasks_by_id: dict[int, ProcessingTask],
) -> SubmissionFeedSubscriptionResponse | None:
    if not raw_subscription:
        return None

    return SubmissionFeedSubscriptionResponse(
        status=_clean_string(raw_subscription.get("status")) or "unknown",
        feed_url=_clean_string(raw_subscription.get("feed_url")),
        feed_type=_clean_string(raw_subscription.get("feed_type")),
        created=_bool_or_none(raw_subscription.get("created")),
        config_id=_int_or_none(raw_subscription.get("config_id")),
        initial_download=_build_initial_download(
            _dict_or_none(raw_subscription.get("initial_download")),
            initial_download_tasks_by_id=initial_download_tasks_by_id,
        ),
    )


def _build_initial_download(
    raw_initial_download: dict[str, Any] | None,
    *,
    initial_download_tasks_by_id: dict[int, ProcessingTask],
) -> SubmissionFeedInitialDownloadResponse | None:
    if not raw_initial_download:
        return None
    projected_initial_download = dict(raw_initial_download)
    task_id = _int_or_none(projected_initial_download.get("task_id"))
    task = initial_download_tasks_by_id.get(task_id) if task_id is not None else None
    if task is not None:
        _overlay_initial_download_task_state(projected_initial_download, task=task)
    elif task_id is not None and _clean_string(projected_initial_download.get("status")) in {
        "pending",
        "processing",
        "queued",
    }:
        # Terminal queue rows are removed after retention. Do not project their
        # stale enqueue-time metadata as work that is still running forever.
        projected_initial_download.update(
            ran=None,
            status="unavailable",
            error="Initial download status is no longer available.",
        )
    try:
        return SubmissionFeedInitialDownloadResponse.model_validate(projected_initial_download)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Ignoring invalid feed initial download metadata: %s",
            exc,
            extra={
                "component": "submission_status",
                "operation": "build_initial_download",
            },
        )
        return None


def _overlay_initial_download_task_state(
    initial_download: dict[str, Any],
    *,
    task: ProcessingTask,
) -> None:
    status = str(task.status)
    if status == TaskStatus.PENDING.value:
        initial_download.update(
            ran=int(task.retry_count or 0) > 0,
            status="queued",
            error=None,
        )
    elif status == TaskStatus.PROCESSING.value:
        initial_download.update(ran=True, status="processing", error=None)
    elif status == TaskStatus.COMPLETED.value:
        initial_download.update(ran=True, status="completed", error=None)
    elif status == TaskStatus.FAILED.value:
        initial_download.update(
            ran=True,
            status="failed",
            error="Initial download failed",
        )


def _is_feed_subscription_submission(
    metadata: ContentMetadataView,
    feed_subscription: SubmissionFeedSubscriptionResponse | None,
    detected_feed: DetectedFeed | None,
) -> bool:
    return (
        _is_truthy(metadata.processing_flag("subscribe_to_feed"))
        or feed_subscription is not None
        or detected_feed is not None
    )


def _resolve_submission_outcome(
    *,
    status: ContentStatus,
    submission_kind: SubmissionKind,
    feed_subscription: SubmissionFeedSubscriptionResponse | None,
) -> SubmissionOutcome:
    if submission_kind != SubmissionKind.FEED_SUBSCRIPTION:
        return _content_status_outcome(status)

    if status in {ContentStatus.NEW, ContentStatus.PENDING}:
        return SubmissionOutcome.QUEUED
    if status == ContentStatus.PROCESSING:
        return SubmissionOutcome.PROCESSING
    if status == ContentStatus.FAILED:
        return SubmissionOutcome.FAILED

    subscription_status = (feed_subscription.status if feed_subscription else "").lower()
    if subscription_status in {"created", "reactivated"}:
        return SubmissionOutcome.SUBSCRIBED
    if subscription_status == "already_exists":
        return SubmissionOutcome.ALREADY_SUBSCRIBED
    if subscription_status == "no_feed_found":
        return SubmissionOutcome.FEED_NOT_FOUND
    if subscription_status == "fetch_failed":
        return SubmissionOutcome.FEED_FETCH_FAILED
    if subscription_status in {
        "missing_user",
        "missing_feed",
        "missing_feed_url",
        "missing_feed_type",
        "unsupported_feed_type",
        "unknown",
    }:
        return SubmissionOutcome.FEED_SUBSCRIPTION_FAILED

    return _content_status_outcome(status)


def _content_status_outcome(status: ContentStatus) -> SubmissionOutcome:
    if status in {ContentStatus.NEW, ContentStatus.PENDING}:
        return SubmissionOutcome.QUEUED
    if status in {ContentStatus.PROCESSING, ContentStatus.AWAITING_IMAGE}:
        return SubmissionOutcome.PROCESSING
    if status == ContentStatus.COMPLETED:
        return SubmissionOutcome.COMPLETED
    if status == ContentStatus.SKIPPED:
        return SubmissionOutcome.SKIPPED
    return SubmissionOutcome.FAILED


def _learning_deck_content_status(status: LearningDeckRunStatus) -> ContentStatus:
    if status == LearningDeckRunStatus.QUEUED:
        return ContentStatus.PENDING
    if status in LEARNING_DECK_ACTIVE_STATUSES:
        return ContentStatus.PROCESSING
    if status == LearningDeckRunStatus.COMPLETED:
        return ContentStatus.COMPLETED
    if status == LearningDeckRunStatus.CANCELLED:
        return ContentStatus.SKIPPED
    return ContentStatus.FAILED


def _learning_deck_attempt_status(status: str) -> str:
    return {
        LlmTaskStatus.RUNNING.value: LearningDeckRunStatus.GENERATING.value,
        LlmTaskStatus.AWAITING_APPROVAL.value: LearningDeckRunStatus.GENERATING.value,
        LlmTaskStatus.APPLYING.value: LearningDeckRunStatus.PUBLISHING.value,
        LlmTaskStatus.CANCELLED.value: LearningDeckRunStatus.CANCELLED.value,
    }.get(status, status)


def _learning_deck_outcome(status: LearningDeckRunStatus) -> SubmissionOutcome:
    if status == LearningDeckRunStatus.QUEUED:
        return SubmissionOutcome.QUEUED
    if status in LEARNING_DECK_ACTIVE_STATUSES:
        return SubmissionOutcome.PROCESSING
    if status == LearningDeckRunStatus.COMPLETED:
        return SubmissionOutcome.COMPLETED
    if status == LearningDeckRunStatus.CANCELLED:
        return SubmissionOutcome.SKIPPED
    return SubmissionOutcome.FAILED


def _llm_task_content_status(status: LlmTaskStatus) -> ContentStatus:
    if status in {LlmTaskStatus.QUEUED, LlmTaskStatus.PREPARING}:
        return ContentStatus.PENDING
    if status in LLM_TASK_ACTIVE_STATUSES:
        return ContentStatus.PROCESSING
    if status == LlmTaskStatus.COMPLETED:
        return ContentStatus.COMPLETED
    if status == LlmTaskStatus.CANCELLED:
        return ContentStatus.SKIPPED
    return ContentStatus.FAILED


def _llm_task_outcome(status: LlmTaskStatus) -> SubmissionOutcome:
    if status in {LlmTaskStatus.QUEUED, LlmTaskStatus.PREPARING}:
        return SubmissionOutcome.QUEUED
    if status in LLM_TASK_ACTIVE_STATUSES:
        return SubmissionOutcome.PROCESSING
    if status == LlmTaskStatus.COMPLETED:
        return SubmissionOutcome.COMPLETED
    if status == LlmTaskStatus.CANCELLED:
        return SubmissionOutcome.SKIPPED
    return SubmissionOutcome.FAILED


def _dict_or_none(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _clean_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return False
