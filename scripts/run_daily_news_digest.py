"""Enqueue daily per-user news digest jobs for users at local 03:00.

Suggested cron (hourly):
0 * * * * cd /opt/news_app && /opt/news_app/.venv/bin/python \
scripts/run_daily_news_digest.py >> /var/log/news_app/daily-news-digest.log 2>&1
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.db import get_db
from app.core.logging import get_logger, setup_logging
from app.models.user import User
from app.services.daily_news_digest import (
    enqueue_daily_news_digest_task,
    normalize_timezone,
    resolve_target_local_date_for_generation,
)

logger = get_logger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enqueue daily news digest tasks")
    parser.add_argument(
        "--user-id",
        type=int,
        action="append",
        dest="user_ids",
        help="Only evaluate specific user ID(s). Can be passed multiple times.",
    )
    parser.add_argument(
        "--now-utc",
        type=str,
        default=None,
        help="Override current UTC time (ISO8601) for testing.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would be enqueued without writing queue tasks.",
    )
    return parser.parse_args()


def _parse_now_utc(raw_value: str | None) -> datetime:
    if not raw_value:
        return datetime.now(UTC)
    parsed = datetime.fromisoformat(raw_value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def main() -> None:
    setup_logging()
    args = _parse_args()
    now_utc = _parse_now_utc(args.now_utc)

    users_considered = 0
    users_due_now = 0
    task_ready_count = 0
    existing_digest_count = 0

    with get_db() as db:
        query = db.query(User).filter(User.is_active.is_(True)).order_by(User.id.asc())
        if args.user_ids:
            query = query.filter(User.id.in_(args.user_ids))

        for user in query.yield_per(200):
            users_considered += 1
            timezone_name = normalize_timezone(getattr(user, "news_digest_timezone", None))
            target_date = resolve_target_local_date_for_generation(
                timezone_name,
                now_utc=now_utc,
            )
            if target_date is None:
                continue

            users_due_now += 1
            if args.dry_run:
                logger.info(
                    "[dry-run] Would enqueue daily digest user=%s local_date=%s timezone=%s",
                    user.id,
                    target_date.isoformat(),
                    timezone_name,
                )
                continue

            task_id = enqueue_daily_news_digest_task(
                db,
                user_id=user.id,
                local_date=target_date,
                timezone_name=timezone_name,
                trigger="cron",
            )
            if task_id is None:
                existing_digest_count += 1
                continue

            task_ready_count += 1

    logger.info(
        (
            "Daily digest enqueue summary considered=%s due_now=%s "
            "task_ready=%s existing_digest=%s dry_run=%s now_utc=%s"
        ),
        users_considered,
        users_due_now,
        task_ready_count,
        existing_digest_count,
        args.dry_run,
        now_utc.isoformat(),
    )


if __name__ == "__main__":
    main()
