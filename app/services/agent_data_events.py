"""Small event boundary for enqueueing incremental user-corpus updates."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.contracts import AgentDataBackfillStage, TaskStatus, TaskType
from app.models.db import ProcessingTask, User
from app.services.gateways.task_queue_gateway import get_task_queue_gateway
from app.services.queue_enqueue import TaskEnqueueRequest


def enqueue_agent_data_sync(
    db: Session,
    *,
    user_id: int,
    content_ids: tuple[int, ...] = (),
    news_item_ids: tuple[int, ...] = (),
    chat_session_ids: tuple[int, ...] = (),
    briefing_dates: tuple[str, ...] = (),
) -> int:
    """Enqueue one deduplicated low-priority corpus revision in this transaction."""
    request = prepare_agent_data_sync_requests(
        db,
        [
            build_agent_data_sync_request(
                user_id=user_id,
                content_ids=content_ids,
                news_item_ids=news_item_ids,
                chat_session_ids=chat_session_ids,
                briefing_dates=briefing_dates,
            )
        ],
    )[0]
    return get_task_queue_gateway().enqueue_many_in_session(db, [request])[0]


def build_agent_data_sync_request(
    *,
    user_id: int,
    content_ids: tuple[int, ...] = (),
    news_item_ids: tuple[int, ...] = (),
    chat_session_ids: tuple[int, ...] = (),
    briefing_dates: tuple[str, ...] = (),
) -> TaskEnqueueRequest:
    """Build the canonical request so event fanout can enqueue one batch."""
    payload = {
        "user_id": int(user_id),
        "content_ids": sorted(set(content_ids)),
        "news_item_ids": sorted(set(news_item_ids)),
        "chat_session_ids": sorted(set(chat_session_ids)),
        "briefing_dates": sorted(set(briefing_dates)),
    }
    return TaskEnqueueRequest(
        task_type=TaskType.SYNC_AGENT_DATA,
        payload=payload,
        owner_user_id=int(user_id),
        dedupe=True,
        # Coalesce identical events but do not let an incremental event hide a
        # different content ID while the first one is in flight.
        dedupe_key=(f"agent-sync|user:{int(user_id)}|payload:{_stable_payload_key(payload)}"),
    )


def prepare_agent_data_sync_requests(
    db: Session,
    requests: list[TaskEnqueueRequest],
) -> list[TaskEnqueueRequest]:
    """Coalesce pending syncs while preserving events racing active handlers."""
    if not requests:
        return []

    base_keys_by_user: dict[int, set[str]] = {}
    for request in requests:
        owner_user_id, base_key = _agent_sync_request_identity(request)
        base_keys_by_user.setdefault(owner_user_id, set()).add(base_key)

    user_ids = sorted(base_keys_by_user)
    active_user_ids = {
        int(user_id)
        for (user_id,) in db.query(User.id)
        .filter(User.id.in_(user_ids), User.is_active.is_(True))
        .order_by(User.id)
        .with_for_update(read=True)
        .all()
    }
    if active_user_ids != set(user_ids):
        raise ValueError("Task user is missing or inactive")

    # The sync handler takes an exclusive lock on this same user row before it
    # renders. Holding shared locks through the caller's commit guarantees that
    # a reused pending task cannot render ahead of the domain event it represents.
    active_tasks = (
        db.query(ProcessingTask)
        .filter(
            ProcessingTask.owner_user_id.in_(user_ids),
            ProcessingTask.task_type == TaskType.SYNC_AGENT_DATA.value,
            ProcessingTask.status.in_((TaskStatus.PENDING.value, TaskStatus.PROCESSING.value)),
        )
        .order_by(ProcessingTask.owner_user_id, ProcessingTask.id)
        .all()
    )
    latest_by_base_key: dict[tuple[int, str], ProcessingTask] = {}
    for task in active_tasks:
        task_owner_user_id = task.owner_user_id
        dedupe_key = task.dedupe_key
        if task_owner_user_id is None or dedupe_key is None:
            continue
        for base_key in base_keys_by_user.get(int(task_owner_user_id), set()):
            if dedupe_key == base_key or dedupe_key.startswith(f"{base_key}|after:"):
                latest_by_base_key[(int(task_owner_user_id), base_key)] = task
                break

    prepared: list[TaskEnqueueRequest] = []
    for request in requests:
        owner_user_id, base_key = _agent_sync_request_identity(request)
        latest = latest_by_base_key.get((owner_user_id, base_key))
        if latest is None:
            prepared.append(request)
            continue
        if latest.status == TaskStatus.PENDING.value:
            prepared.append(replace(request, dedupe_key=str(latest.dedupe_key)))
            continue
        prepared.append(
            replace(
                request,
                dedupe_key=f"{base_key}|after:{_require_task_id(latest)}",
            )
        )
    return prepared


def enqueue_agent_data_reconcile(
    db: Session,
    *,
    user_id: int,
    before_id: int | None = None,
) -> int:
    """Enqueue one bounded checksum-ledger reconciliation page."""
    request = build_agent_data_reconcile_request(user_id=user_id, before_id=before_id)
    return get_task_queue_gateway().enqueue_many_in_session(db, [request])[0]


def build_agent_data_reconcile_request(
    *,
    user_id: int,
    before_id: int | None = None,
) -> TaskEnqueueRequest:
    payload: dict[str, object] = {"user_id": int(user_id)}
    if before_id is not None:
        payload["before_id"] = int(before_id)
    return TaskEnqueueRequest(
        task_type=TaskType.RECONCILE_AGENT_DATA,
        payload=payload,
        owner_user_id=int(user_id),
        dedupe=True,
        dedupe_key=(f"agent-reconcile|user:{int(user_id)}|payload:{_stable_payload_key(payload)}"),
    )


def briefing_date_key(value: datetime | None) -> str:
    return (value or datetime.now(UTC)).strftime("%Y-%m-%d")


def enqueue_agent_data_index(
    db: Session,
    *,
    user_id: int,
    delay_seconds: int = 300,
) -> int:
    """Debounce index publication, including events racing an active index task."""
    active = (
        db.query(ProcessingTask)
        .filter(
            ProcessingTask.owner_user_id == user_id,
            ProcessingTask.task_type == TaskType.INDEX_AGENT_DATA.value,
            ProcessingTask.status.in_((TaskStatus.PENDING.value, TaskStatus.PROCESSING.value)),
        )
        .order_by(ProcessingTask.id.desc())
        .first()
    )
    if active is not None and active.status == TaskStatus.PENDING.value:
        return _require_task_id(active)
    predecessor = f"after:{_require_task_id(active)}" if active is not None else "base"
    request = TaskEnqueueRequest(
        task_type=TaskType.INDEX_AGENT_DATA,
        payload={"user_id": int(user_id)},
        owner_user_id=int(user_id),
        dedupe=True,
        dedupe_key=f"agent-index|user:{int(user_id)}|{predecessor}",
        available_at=datetime.now(UTC).replace(tzinfo=None)
        + timedelta(seconds=max(0, delay_seconds)),
    )
    return get_task_queue_gateway().enqueue_many_in_session(db, [request])[0]


def enqueue_agent_data_backfill(
    db: Session,
    *,
    user_id: int,
    stage: AgentDataBackfillStage = AgentDataBackfillStage.KNOWLEDGE,
    before_id: int | None = None,
    predecessor_task_id: int | None = None,
) -> int:
    """Keep exactly one bounded backfill chain active for a user."""
    query = db.query(ProcessingTask).filter(
        ProcessingTask.owner_user_id == user_id,
        ProcessingTask.task_type == TaskType.BACKFILL_AGENT_DATA.value,
        ProcessingTask.status.in_((TaskStatus.PENDING.value, TaskStatus.PROCESSING.value)),
    )
    if predecessor_task_id is not None:
        query = query.filter(ProcessingTask.id != predecessor_task_id)
    active = query.order_by(ProcessingTask.id.desc()).first()
    if active is not None:
        return _require_task_id(active)

    payload: dict[str, object] = {
        "user_id": int(user_id),
        "stage": stage.value,
    }
    if before_id is not None:
        payload["before_id"] = int(before_id)
    chain_key = f"after:{predecessor_task_id}" if predecessor_task_id is not None else "initial"
    request = TaskEnqueueRequest(
        task_type=TaskType.BACKFILL_AGENT_DATA,
        payload=payload,
        owner_user_id=int(user_id),
        dedupe=True,
        dedupe_key=f"agent-backfill|user:{int(user_id)}|{chain_key}",
    )
    return get_task_queue_gateway().enqueue_many_in_session(db, [request])[0]


def _stable_payload_key(payload: dict[str, object]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:24]


def _agent_sync_request_identity(request: TaskEnqueueRequest) -> tuple[int, str]:
    if (
        request.task_type != TaskType.SYNC_AGENT_DATA
        or request.owner_user_id is None
        or request.dedupe_key is None
    ):
        raise ValueError("Expected canonical agent-data sync requests")
    return int(request.owner_user_id), request.dedupe_key


def _require_task_id(task: ProcessingTask) -> int:
    task_id = task.id
    if task_id is None:
        raise RuntimeError("Agent-data task must be persisted before it can be reused")
    return task_id
