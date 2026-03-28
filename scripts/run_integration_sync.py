"""Enqueue scheduled X integration sync tasks for connected users.

Suggested cron:
0 * * * * cd /opt/news_app && /opt/news_app/.venv/bin/python \
scripts/run_integration_sync.py >> /var/log/news_app/integration-sync.log 2>&1
"""
# ruff: noqa: E402

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.db import get_db
from app.core.logging import get_logger, setup_logging
from app.core.settings import get_settings
from app.models.schema import UserIntegrationConnection
from app.services.queue import QueueService, TaskType
from app.services.x_integration import X_PROVIDER

logger = get_logger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enqueue X integration sync tasks")
    parser.add_argument("--user-id", type=int, default=None, help="Sync one user only")
    return parser.parse_args()


def enqueue_x_sync_tasks(*, user_id: int | None = None) -> int:
    """Enqueue per-user X sync tasks for all active connections."""
    settings = get_settings()
    if not settings.x_bookmark_sync_enabled:
        logger.info(
            "X integration sync is disabled (X_BOOKMARK_SYNC_ENABLED=false); skipping enqueue."
        )
        return 0

    queue = QueueService()

    enqueued = 0
    with get_db() as db:
        query = (
            db.query(UserIntegrationConnection.user_id)
            .filter(UserIntegrationConnection.provider == X_PROVIDER)
            .filter(UserIntegrationConnection.is_active.is_(True))
            .distinct()
            .order_by(UserIntegrationConnection.user_id.asc())
        )
        if user_id is not None:
            query = query.filter(UserIntegrationConnection.user_id == user_id)

        for (user_id,) in query.yield_per(200):
            queue.enqueue(
                TaskType.SYNC_INTEGRATION,
                payload={
                    "user_id": int(user_id),
                    "provider": X_PROVIDER,
                    "trigger": "cron",
                },
            )
            enqueued += 1

    return enqueued


def main() -> None:
    setup_logging()
    args = _parse_args()
    enqueued = enqueue_x_sync_tasks(user_id=args.user_id)
    logger.info("Enqueued %s X integration sync tasks", enqueued)


if __name__ == "__main__":
    main()
