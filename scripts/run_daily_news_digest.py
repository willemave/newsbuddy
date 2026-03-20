"""Enqueue same-day digest checkpoint jobs when a recent local checkpoint is due.

Suggested cron (every 3 hours):
0 */3 * * * cd /opt/news_app && /opt/news_app/.venv/bin/python \
scripts/run_daily_news_digest.py --lookback-hours 6 >> /var/log/news_app/daily-news-digest.log 2>&1
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
    normalize_news_digest_interval_hours,
    normalize_timezone,
    resolve_daily_digest_generation_target,
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
    parser.add_argument(
        "--lookback-hours",
        "--hours",
        type=int,
        dest="lookback_hours",
        choices=(3, 6, 12, 24),
        default=6,
        help="Look back this many hours for each user's latest due digest checkpoint.",
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
            interval_hours = normalize_news_digest_interval_hours(
                getattr(user, "news_digest_interval_hours", None)
            )
            target = resolve_daily_digest_generation_target(
                timezone_name,
                now_utc=now_utc,
                interval_hours=interval_hours,
                lookback_hours=args.lookback_hours,
            )
            if target is None:
                continue

            users_due_now += 1
            if args.dry_run:
                logger.info(
                    (
                        "[dry-run] Would enqueue daily digest user=%s local_date=%s "
                        "timezone=%s interval_hours=%s coverage_end_at=%s"
                    ),
                    user.id,
                    target.local_date.isoformat(),
                    timezone_name,
                    interval_hours,
                    target.coverage_end_at.isoformat(),
                )
                continue

            task_id = enqueue_daily_news_digest_task(
                db,
                user_id=user.id,
                local_date=target.local_date,
                timezone_name=timezone_name,
                trigger="cron",
                coverage_end_at=target.coverage_end_at,
                skip_if_empty=True,
            )
            if task_id is None:
                existing_digest_count += 1
                continue

            task_ready_count += 1

    logger.info(
        (
            "Daily digest enqueue summary considered=%s due_now=%s "
            "task_ready=%s existing_digest=%s dry_run=%s now_utc=%s window_hours=%s"
        ),
        users_considered,
        users_due_now,
        task_ready_count,
        existing_digest_count,
        args.dry_run,
        now_utc.isoformat(),
        args.lookback_hours,
    )


if __name__ == "__main__":
    main()
