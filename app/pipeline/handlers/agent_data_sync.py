"""Low-priority handlers for incremental, backfill, and repair corpus syncs."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.models.contracts import AgentDataBackfillStage, TaskType
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope, TaskResult
from app.services.agent_data_documents import (
    briefing_dates_for_backfill_page,
    next_agent_data_backfill_page,
)
from app.services.agent_data_events import (
    enqueue_agent_data_backfill,
    enqueue_agent_data_index,
    enqueue_agent_data_reconcile,
)
from app.services.agent_data_sync import (
    AgentDataSyncResult,
    AgentDataSyncSelection,
    next_agent_data_reconcile_page,
    publish_agent_data_index,
    read_agent_data_manifest,
    sync_agent_data_for_user,
)


class SyncAgentDataHandler:
    task_type = TaskType.SYNC_AGENT_DATA

    def handle(self, task: TaskEnvelope, context: TaskContext) -> TaskResult:
        payload = task.payload if isinstance(task.payload, dict) else {}
        user_id = payload.get("user_id")
        if not isinstance(user_id, int) or user_id <= 0:
            return TaskResult.fail("Missing or invalid user_id", retryable=False)
        selection = AgentDataSyncSelection(
            content_ids=frozenset(_positive_ints(payload.get("content_ids"))),
            news_item_ids=frozenset(_positive_ints(payload.get("news_item_ids"))),
            chat_session_ids=frozenset(_positive_ints(payload.get("chat_session_ids"))),
            briefing_dates=frozenset(_strings(payload.get("briefing_dates"))),
        )
        try:
            with context.db_factory() as db:
                result = sync_agent_data_for_user(db, user_id=user_id, selection=selection)
                manifest = read_agent_data_manifest(user_id)
                if _manifest_revision(manifest) != result.revision:
                    enqueue_agent_data_index(db, user_id=user_id)
                if manifest is None or manifest.get("complete") is not True:
                    enqueue_agent_data_backfill(
                        db,
                        user_id=user_id,
                    )
                db.commit()
        except Exception as exc:  # noqa: BLE001
            return TaskResult.fail(str(exc))
        return TaskResult.ok()


class IndexAgentDataHandler:
    task_type = TaskType.INDEX_AGENT_DATA

    def handle(self, task: TaskEnvelope, context: TaskContext) -> TaskResult:
        payload = task.payload if isinstance(task.payload, dict) else {}
        user_id = payload.get("user_id")
        if not isinstance(user_id, int) or user_id <= 0:
            return TaskResult.fail("Missing or invalid user_id", retryable=False)
        try:
            with context.db_factory() as db:
                publish_agent_data_index(db, user_id=user_id)
                db.commit()
        except Exception as exc:  # noqa: BLE001
            return TaskResult.fail(str(exc))
        return TaskResult.ok()


class BackfillAgentDataHandler:
    task_type = TaskType.BACKFILL_AGENT_DATA

    def handle(self, task: TaskEnvelope, context: TaskContext) -> TaskResult:
        payload = task.payload if isinstance(task.payload, dict) else {}
        user_id = payload.get("user_id")
        if not isinstance(user_id, int) or user_id <= 0:
            return TaskResult.fail("Missing or invalid user_id", retryable=False)
        raw_stage = payload.get("stage")
        before_id = payload.get("before_id")
        try:
            stage = AgentDataBackfillStage(raw_stage or AgentDataBackfillStage.KNOWLEDGE)
        except (TypeError, ValueError):
            return TaskResult.fail("Invalid stage", retryable=False)
        if before_id is not None and (
            not isinstance(before_id, int) or isinstance(before_id, bool) or before_id <= 0
        ):
            return TaskResult.fail("Invalid before_id", retryable=False)

        try:
            with context.db_factory() as db:
                page = next_agent_data_backfill_page(
                    db,
                    user_id=user_id,
                    stage=stage,
                    before_id=before_id,
                    limit=get_settings().agent_data_backfill_batch_size,
                )
                if page is None:
                    publish_agent_data_index(db, user_id=user_id, mark_complete=True)
                    db.commit()
                    return TaskResult.ok()

                selection = _selection_for_backfill_page(
                    db,
                    user_id=user_id,
                    stage=page.stage,
                    ids=page.ids,
                )
                result = sync_agent_data_for_user(db, user_id=user_id, selection=selection)
                _publish_agent_data_index_if_stale(db, result=result)
                enqueue_agent_data_backfill(
                    db,
                    user_id=user_id,
                    stage=page.next_stage or AgentDataBackfillStage.KNOWLEDGE,
                    before_id=page.next_before_id,
                    predecessor_task_id=task.id,
                )
                db.commit()
        except Exception as exc:  # noqa: BLE001
            return TaskResult.fail(str(exc))
        return TaskResult.ok()


class ReconcileAgentDataHandler:
    task_type = TaskType.RECONCILE_AGENT_DATA

    def handle(self, task: TaskEnvelope, context: TaskContext) -> TaskResult:
        payload = task.payload if isinstance(task.payload, dict) else {}
        user_id = payload.get("user_id")
        before_id = payload.get("before_id")
        if not isinstance(user_id, int) or user_id <= 0:
            return TaskResult.fail("Missing or invalid user_id", retryable=False)
        if before_id is not None and (
            not isinstance(before_id, int) or isinstance(before_id, bool) or before_id <= 0
        ):
            return TaskResult.fail("Invalid before_id", retryable=False)

        try:
            with context.db_factory() as db:
                page = next_agent_data_reconcile_page(
                    db,
                    user_id=user_id,
                    before_id=before_id,
                    limit=get_settings().agent_data_backfill_batch_size,
                )
                if page is None:
                    publish_agent_data_index(db, user_id=user_id)
                    enqueue_agent_data_backfill(
                        db,
                        user_id=user_id,
                        predecessor_task_id=task.id,
                    )
                    db.commit()
                    return TaskResult.ok()

                result = sync_agent_data_for_user(
                    db,
                    user_id=user_id,
                    selection=page.selection,
                )
                _publish_agent_data_index_if_stale(db, result=result)
                enqueue_agent_data_reconcile(
                    db,
                    user_id=user_id,
                    before_id=page.next_before_id,
                )
                db.commit()
        except Exception as exc:  # noqa: BLE001
            return TaskResult.fail(str(exc))
        return TaskResult.ok()


def _positive_ints(value: object) -> list[int]:
    if not isinstance(value, list):
        return []
    return [
        item for item in value if isinstance(item, int) and not isinstance(item, bool) and item > 0
    ]


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _selection_for_backfill_page(
    db: Session,
    *,
    user_id: int,
    stage: AgentDataBackfillStage,
    ids: tuple[int, ...],
) -> AgentDataSyncSelection:
    if stage in {AgentDataBackfillStage.KNOWLEDGE, AgentDataBackfillStage.CONTENT}:
        return AgentDataSyncSelection(content_ids=frozenset(ids))
    if stage == AgentDataBackfillStage.NEWS:
        return AgentDataSyncSelection(news_item_ids=frozenset(ids))
    if stage == AgentDataBackfillStage.CHATS:
        return AgentDataSyncSelection(chat_session_ids=frozenset(ids))
    if stage == AgentDataBackfillStage.BRIEFINGS:
        return AgentDataSyncSelection(
            briefing_dates=briefing_dates_for_backfill_page(
                db,
                user_id=user_id,
                segment_ids=ids,
            )
        )
    raise ValueError(f"Unsupported agent-data backfill stage: {stage}")


def _publish_agent_data_index_if_stale(
    db: Session,
    *,
    result: AgentDataSyncResult,
) -> None:
    if _manifest_revision(read_agent_data_manifest(result.user_id)) != result.revision:
        publish_agent_data_index(db, user_id=result.user_id)


def _manifest_revision(manifest: dict[str, object] | None) -> int | None:
    if manifest is None:
        return None
    revision = manifest.get("revision")
    if isinstance(revision, int) and not isinstance(revision, bool):
        return revision
    return None
