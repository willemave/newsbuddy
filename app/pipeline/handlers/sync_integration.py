"""Task handler for scheduled external integration sync jobs."""

from __future__ import annotations

from app.core.db import get_db
from app.core.logging import get_logger
from app.core.settings import get_settings
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope, TaskResult
from app.services.queue import TaskType
from app.services.x_integration import sync_x_sources_for_user

logger = get_logger(__name__)


class SyncIntegrationHandler:
    """Run X integration sync tasks for connected integrations."""

    task_type = TaskType.SYNC_INTEGRATION

    def handle(self, task: TaskEnvelope, context: TaskContext) -> TaskResult:
        """Sync user integrations for this task payload."""
        payload = task.payload or {}
        user_id = payload.get("user_id")
        provider = str(payload.get("provider") or "x").strip().lower()
        trigger = str(payload.get("trigger") or "cron").strip().lower()

        if not isinstance(user_id, int):
            return TaskResult.fail("Missing user_id in sync_integration payload", retryable=False)
        if provider != "x":
            return TaskResult.fail(
                f"Unsupported integration provider: {provider}",
                retryable=False,
            )
        if not get_settings().x_bookmark_sync_enabled:
            logger.info(
                "Integration sync skipped because X sync is disabled",
                extra={
                    "component": "sync_integration",
                    "operation": "sync_x_sources",
                    "item_id": str(user_id),
                    "context_data": {"provider": provider},
                },
            )
            return TaskResult.ok()

        try:
            with get_db() as db:
                summary = sync_x_sources_for_user(
                    db,
                    user_id=user_id,
                    force=trigger != "cron",
                )
            logger.info(
                "Integration sync completed",
                extra={
                    "component": "sync_integration",
                    "operation": "sync_x_sources",
                    "item_id": str(user_id),
                    "context_data": {
                        "status": summary.status,
                        "fetched": summary.fetched,
                        "accepted": summary.accepted,
                        "filtered_out": summary.filtered_out,
                        "errored": summary.errored,
                        "created": summary.created,
                        "reused": summary.reused,
                        "channels": {
                            name: {
                                "status": channel.status,
                                "fetched": channel.fetched,
                                "accepted": channel.accepted,
                                "filtered_out": channel.filtered_out,
                                "errored": channel.errored,
                                "created": channel.created,
                                "reused": channel.reused,
                            }
                            for name, channel in summary.channels.items()
                        },
                    },
                },
            )
            return TaskResult.ok()
        except ValueError as exc:
            logger.error(
                "Integration sync rejected",
                extra={
                    "component": "sync_integration",
                    "operation": "sync_x_sources",
                    "item_id": str(user_id),
                    "context_data": {"error": str(exc)},
                },
            )
            return TaskResult.fail(str(exc), retryable=False)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Integration sync failed",
                extra={
                    "component": "sync_integration",
                    "operation": "sync_x_sources",
                    "item_id": str(user_id),
                    "context_data": {"error": str(exc)},
                },
            )
            return TaskResult.fail(str(exc))
