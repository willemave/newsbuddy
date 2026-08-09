"""Enqueue weekly feed discovery jobs for eligible users."""

from __future__ import annotations

import argparse
import os
import sys
from time import perf_counter

from sqlalchemy import func

# Match the other cron-driven entrypoints so `python scripts/run_feed_discovery.py`
# resolves the repo-local `app` package on production.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.db import get_db
from app.core.logging import get_logger, setup_logging
from app.core.observability import bound_log_context, build_log_extra
from app.models.db import ContentReadStatus
from app.models.db.users import User
from app.services.queue import QueueService, TaskEnqueueRequest, TaskType

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
    run_started_at = perf_counter()

    queue = QueueService()
    enqueued = 0
    skipped = 0

    with bound_log_context(job_name="run_feed_discovery", trigger="cron", source="cron"):
        logger.info(
            "Feed discovery cron run started",
            extra=build_log_extra(
                component="cron",
                operation="run_feed_discovery",
                event_name="cron.run",
                status="started",
                job_name="run_feed_discovery",
                trigger="cron",
                context_data={
                    "user_id": args.user_id,
                    "min_recent_reads": min_recent_reads,
                },
            ),
        )

        with get_db() as db:
            if args.user_id:
                user = (
                    db.query(User).filter(User.id == args.user_id, User.is_active.is_(True)).first()
                )
                if user is None or not user.has_completed_onboarding:
                    skipped += 1
                    logger.info(
                        "Skipping feed discovery user",
                        extra=build_log_extra(
                            component="cron",
                            operation="run_feed_discovery",
                            event_name="cron.run",
                            status="skipped",
                            job_name="run_feed_discovery",
                            trigger="cron",
                            user_id=args.user_id,
                            context_data={"reason": "not_onboarded"},
                        ),
                    )
                    return
                count = (
                    db.query(func.count(ContentReadStatus.id))
                    .filter(ContentReadStatus.user_id == args.user_id)
                    .scalar()
                    or 0
                )
                if count < min_recent_reads:
                    skipped += 1
                    logger.info(
                        "Skipping feed discovery user",
                        extra=build_log_extra(
                            component="cron",
                            operation="run_feed_discovery",
                            event_name="cron.run",
                            status="skipped",
                            job_name="run_feed_discovery",
                            trigger="cron",
                            user_id=args.user_id,
                            context_data={"reason": "insufficient_reads", "recent_reads": count},
                        ),
                    )
                    return
                queue.enqueue_many_in_session(
                    db,
                    [
                        TaskEnqueueRequest(
                            TaskType.DISCOVER_FEEDS,
                            payload={"user_id": args.user_id, "trigger": "cron"},
                            owner_user_id=args.user_id,
                        )
                    ],
                )
                enqueued = 1
            else:
                rows = (
                    db.query(User.id)
                    .filter(
                        User.is_active.is_(True),
                        User.has_completed_onboarding.is_(True),
                    )
                    .all()
                )
                user_ids = [user_id for (user_id,) in rows]
                read_counts = (
                    {
                        user_id: int(read_count)
                        for user_id, read_count in (
                            db.query(
                                ContentReadStatus.user_id,
                                func.count(ContentReadStatus.id),
                            )
                            .filter(ContentReadStatus.user_id.in_(user_ids))
                            .group_by(ContentReadStatus.user_id)
                            .all()
                        )
                    }
                    if user_ids
                    else {}
                )
                requests: list[TaskEnqueueRequest] = []
                for user_id in user_ids:
                    read_count = read_counts.get(user_id, 0)
                    if read_count < min_recent_reads:
                        skipped += 1
                        continue
                    requests.append(
                        TaskEnqueueRequest(
                            TaskType.DISCOVER_FEEDS,
                            payload={"user_id": user_id, "trigger": "cron"},
                            owner_user_id=user_id,
                        )
                    )
                enqueued = len(requests)
                if requests:
                    queue.enqueue_many_in_session(db, requests)

        logger.info(
            "Feed discovery cron run completed",
            extra=build_log_extra(
                component="cron",
                operation="run_feed_discovery",
                event_name="cron.run",
                status="completed",
                duration_ms=(perf_counter() - run_started_at) * 1000,
                job_name="run_feed_discovery",
                trigger="cron",
                context_data={
                    "considered_count": 1 if args.user_id else enqueued + skipped,
                    "enqueued_count": enqueued,
                    "skipped_count": skipped,
                    "failed_count": 0,
                },
            ),
        )


if __name__ == "__main__":
    main()
