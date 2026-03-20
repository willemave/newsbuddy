"""Enqueue weekly feed discovery jobs for eligible users."""

from __future__ import annotations

import argparse

from sqlalchemy import func

from app.core.db import get_db
from app.core.logging import get_logger, setup_logging
from app.models.schema import ContentReadStatus
from app.models.user import User
from app.services.queue import QueueService, TaskType

logger = get_logger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enqueue feed discovery tasks")
    parser.add_argument("--user-id", type=int, default=None)
    parser.add_argument("--min-recent-reads", type=int, default=None)
    return parser.parse_args()


def main() -> None:
    setup_logging()
    args = _parse_args()
    min_recent_reads = 0 if args.min_recent_reads is None else max(args.min_recent_reads, 0)

    queue = QueueService()
    enqueued = 0

    with get_db() as db:
        if args.user_id:
            user = db.query(User).filter(User.id == args.user_id).first()
            if user is None or not user.has_completed_onboarding:
                logger.info("Skipping user %s (not onboarded)", args.user_id)
                return
            count = (
                db.query(func.count(ContentReadStatus.id))
                .filter(ContentReadStatus.user_id == args.user_id)
                .scalar()
                or 0
            )
            if count < min_recent_reads:
                logger.info(
                    "Skipping user %s (recent_reads=%s, min=%s)",
                    args.user_id,
                    count,
                    min_recent_reads,
                )
                return
            queue.enqueue(
                TaskType.DISCOVER_FEEDS,
                payload={"user_id": args.user_id, "trigger": "cron"},
            )
            enqueued = 1
        else:
            rows = (
                db.query(User.id)
                .filter(User.has_completed_onboarding.is_(True))
                .all()
            )
            user_ids = [row[0] for row in rows]
            for user_id in user_ids:
                read_count = (
                    db.query(func.count(ContentReadStatus.id))
                    .filter(ContentReadStatus.user_id == user_id)
                    .scalar()
                    or 0
                )
                if read_count < min_recent_reads:
                    continue
                queue.enqueue(
                    TaskType.DISCOVER_FEEDS,
                    payload={"user_id": user_id, "trigger": "cron"},
                )
                enqueued += 1

    logger.info("Enqueued %s feed discovery tasks", enqueued)


if __name__ == "__main__":
    main()
