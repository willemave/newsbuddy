"""Backfill news_items from legacy contents rows and optionally rebuild digest runs."""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.db import get_db
from app.core.logging import get_logger, setup_logging
from app.models.contracts import NewsItemStatus
from app.models.metadata import ContentType
from app.models.schema import (
    Content,
    NewsDigest,
    NewsDigestBullet,
    NewsDigestBulletSource,
    NewsItem,
    NewsItemDigestCoverage,
)
from app.models.user import User
from app.services.news_digests import (
    enqueue_news_digest_generation,
    get_news_digest_trigger_decision,
)
from app.services.news_ingestion import backfill_news_items_from_contents
from app.services.queue import TaskType, get_queue_service

logger = get_logger(__name__)


def _has_existing_summary(item: NewsItem) -> bool:
    if isinstance(item.summary_text, str) and item.summary_text.strip():
        return True
    key_points: Any = item.summary_key_points
    return isinstance(key_points, list) and any(
        isinstance(point, str) and point.strip() for point in key_points
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill news_items from legacy contents")
    parser.add_argument("--limit", type=int, default=None, help="Optional max rows to backfill")
    parser.add_argument(
        "--last-n",
        type=int,
        default=None,
        help="Only backfill the most recent N legacy news rows",
    )
    parser.add_argument(
        "--include-existing",
        action="store_true",
        help="Update existing linked rows instead of skipping them",
    )
    parser.add_argument(
        "--enqueue-processing",
        action="store_true",
        help="Enqueue processing for backfilled rows that are not ready yet",
    )
    parser.add_argument(
        "--allow-summary-generation",
        action="store_true",
        help="Allow enqueueing legacy rows that would require fresh LLM summarization",
    )
    parser.add_argument(
        "--rebuild-digests",
        action="store_true",
        help="Delete news-native digests and coverage rows before re-enqueuing digest generation",
    )
    parser.add_argument(
        "--enqueue-digests",
        action="store_true",
        help="Evaluate active users and enqueue digest generation after backfill",
    )
    return parser.parse_args()


def _resolve_recent_content_ids(last_n: int) -> list[int]:
    with get_db() as db:
        rows = (
            db.query(Content.id)
            .filter(Content.content_type == ContentType.NEWS.value)
            .order_by(Content.id.desc())
            .limit(last_n)
            .all()
        )
    return sorted(int(row[0]) for row in rows)


def main() -> None:
    setup_logging()
    args = _parse_args()
    if args.limit is not None and args.last_n is not None:
        raise SystemExit("Use either --limit or --last-n, not both.")

    content_ids = _resolve_recent_content_ids(args.last_n) if args.last_n else None
    pending_news_item_ids: list[int] = []
    skipped_summary_generation = 0

    with get_db() as db:
        stats = backfill_news_items_from_contents(
            db,
            limit=args.limit,
            only_missing=not args.include_existing,
            content_ids=content_ids,
        )
        logger.info(
            "Backfilled news items",
            extra={
                "component": "news_backfill",
                "operation": "backfill_news_items",
                "context_data": {
                    "created": stats.created,
                    "updated": stats.updated,
                    "skipped": stats.skipped,
                },
            },
        )

        if args.enqueue_processing:
            pending_query = (
                db.query(NewsItem)
                .filter(NewsItem.status != NewsItemStatus.READY.value)
                .order_by(NewsItem.id.asc())
            )
            if content_ids:
                pending_query = pending_query.filter(NewsItem.legacy_content_id.in_(content_ids))
            pending_items = pending_query.all()
            if args.allow_summary_generation:
                pending_news_item_ids = [item.id for item in pending_items]
            else:
                pending_news_item_ids = [
                    item.id for item in pending_items if _has_existing_summary(item)
                ]
                skipped_summary_generation = len(pending_items) - len(pending_news_item_ids)

        if args.rebuild_digests:
            db.query(NewsDigestBulletSource).delete()
            db.query(NewsDigestBullet).delete()
            db.query(NewsItemDigestCoverage).delete()
            db.query(NewsDigest).delete()
            db.commit()

    if args.enqueue_processing:
        queue_service = get_queue_service()
        for news_item_id in pending_news_item_ids:
            queue_service.enqueue(
                TaskType.PROCESS_NEWS_ITEM,
                payload={"news_item_id": news_item_id},
                dedupe=False,
            )
        logger.info(
            "Queued legacy news items for processing",
            extra={
                "component": "news_backfill",
                "operation": "enqueue_processing",
                "context_data": {
                    "enqueued": len(pending_news_item_ids),
                    "skipped_requires_summary_generation": skipped_summary_generation,
                    "allow_summary_generation": args.allow_summary_generation,
                },
            },
        )

    if args.enqueue_digests:
        with get_db() as db:
            users = db.query(User).filter(User.is_active.is_(True)).order_by(User.id.asc()).all()
            for user in users:
                decision = get_news_digest_trigger_decision(db, user=user)
                if not decision.should_generate:
                    continue
                enqueue_news_digest_generation(
                    db,
                    user_id=user.id,
                    trigger_reason=decision.trigger_reason or "backfill",
                )


if __name__ == "__main__":
    main()
