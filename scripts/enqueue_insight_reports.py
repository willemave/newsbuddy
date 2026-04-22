#!/usr/bin/env python3
"""Nightly trigger: enqueue insight_report generation for eligible users.

A user is eligible when:
1. They are active (``users.is_active = true``).
2. They have at least ``--min-saves`` knowledge saves since their most recent
   ``insight_report`` content row (or ever, if none yet).
3. They don't already have a pending/processing ``generate_insight_report``
   task in the queue (enforced via the standard dedupe_key path).

Run this from the supercronic crontab — see ``docker/crontab``.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from app.core.db import get_db
from app.core.logging import get_logger, setup_logging
from app.core.observability import build_log_extra
from app.models.user import User
from app.services.insight_report import (
    DEFAULT_MIN_SAVES_FOR_TRIGGER,
    SYNTHESIS_EFFORT,
    SYNTHESIS_MODEL,
    count_knowledge_saves_since,
    last_insight_report_for_user,
)
from app.services.queue import TaskType, get_queue_service

logger = get_logger(__name__)


def main() -> int:
    setup_logging()
    parser = argparse.ArgumentParser(description="Enqueue nightly insight reports")
    parser.add_argument(
        "--min-saves",
        type=int,
        default=DEFAULT_MIN_SAVES_FOR_TRIGGER,
        help="Minimum new knowledge saves since the user's last report to trigger a new one",
    )
    parser.add_argument(
        "--synthesis-model",
        default=SYNTHESIS_MODEL,
        help="Pydantic-ai model spec for synthesis (overrides service default)",
    )
    parser.add_argument(
        "--effort",
        default=SYNTHESIS_EFFORT,
        choices=["low", "medium", "high", "max"],
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log eligible users without enqueueing tasks",
    )
    parser.add_argument(
        "--user-id",
        type=int,
        default=None,
        help="If set, only consider this single user (still subject to min-saves).",
    )
    args = parser.parse_args()

    queue_service = get_queue_service()
    now_utc = datetime.now(UTC).replace(tzinfo=None)
    enqueued: list[int] = []
    skipped: list[tuple[int, str]] = []

    with get_db() as db:
        user_stmt = select(User.id).where(User.is_active.is_(True))
        if args.user_id is not None:
            user_stmt = user_stmt.where(User.id == args.user_id)
        user_ids = [row[0] for row in db.execute(user_stmt).all()]

        for user_id in user_ids:
            last_report = last_insight_report_for_user(db, user_id=user_id)
            last_at = last_report[1] if last_report else None
            save_count = count_knowledge_saves_since(db, user_id=user_id, since=last_at)
            if save_count < args.min_saves:
                skipped.append((user_id, f"only {save_count} new saves"))
                continue

            if args.dry_run:
                enqueued.append(user_id)
                logger.info(
                    "DRY RUN: would enqueue insight_report for user_id=%s (new_saves=%d)",
                    user_id,
                    save_count,
                    extra=build_log_extra(
                        component="enqueue_insight_reports",
                        operation="enqueue",
                        event_name="insight_report.trigger_evaluated",
                        status="dry_run",
                        context_data={"user_id": user_id, "new_saves": save_count},
                    ),
                )
                continue

            task_id = queue_service.enqueue(
                TaskType.GENERATE_INSIGHT_REPORT,
                payload={
                    "user_id": user_id,
                    "synthesis_model": args.synthesis_model,
                    "effort": args.effort,
                    "triggered_at": now_utc.isoformat(),
                },
                dedupe=True,
                dedupe_key=f"insight_report|user:{user_id}|nightly",
            )
            enqueued.append(user_id)
            logger.info(
                "Enqueued insight_report task_id=%s user_id=%s new_saves=%d",
                task_id,
                user_id,
                save_count,
                extra=build_log_extra(
                    component="enqueue_insight_reports",
                    operation="enqueue",
                    event_name="insight_report.trigger_enqueued",
                    status="completed",
                    task_id=task_id,
                    context_data={"user_id": user_id, "new_saves": save_count},
                ),
            )

    print(
        f"Enqueued {len(enqueued)} insight_report task(s); "
        f"skipped {len(skipped)} user(s) under threshold "
        f"(min_saves={args.min_saves})."
    )
    if skipped:
        logger.debug("Skipped users: %s", skipped)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
