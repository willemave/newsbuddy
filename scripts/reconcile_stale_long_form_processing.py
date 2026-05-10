"""Mark orphaned long-form processing rows as failed.

Usage:
    python scripts/reconcile_stale_long_form_processing.py --apply
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, exists, or_, select

from app.core.db import get_db
from app.core.settings import get_settings
from app.models.contracts import ContentStatus, ContentType, TaskStatus
from app.models.db import Content, ContentStatusEntry, ProcessingTask

STALE_ERROR_MESSAGE = "stale_orphaned_processing_state"


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--hours-old",
        type=int,
        default=24,
        help="Only reconcile rows older than this many hours.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist the status update instead of running in dry-run mode.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="Maximum number of rows to inspect/update.",
    )
    return parser.parse_args()


def find_stale_content_ids(*, hours_old: int, limit: int) -> list[int]:
    """Return orphaned long-form content IDs eligible for reconciliation."""
    settings = get_settings()
    now_utc = datetime.now(UTC).replace(tzinfo=None)
    stale_cutoff = now_utc - timedelta(hours=hours_old)
    fresh_checkout_cutoff = now_utc - timedelta(minutes=settings.checkout_timeout_minutes)

    active_task_exists = exists(
        select(ProcessingTask.id).where(
            ProcessingTask.content_id == Content.id,
            ProcessingTask.status.in_([TaskStatus.PENDING.value, TaskStatus.PROCESSING.value]),
        )
    )
    has_current_checkout = and_(
        Content.checked_out_by.is_not(None),
        Content.checked_out_at.is_not(None),
        Content.checked_out_at >= fresh_checkout_cutoff,
    )
    long_form_filter = or_(
        Content.content_type.in_([ContentType.ARTICLE.value, ContentType.PODCAST.value]),
        and_(
            Content.platform == "youtube",
            Content.content_type != ContentType.NEWS.value,
        ),
    )

    with get_db() as db:
        rows = (
            db.query(Content.id)
            .join(ContentStatusEntry, ContentStatusEntry.content_id == Content.id)
            .filter(ContentStatusEntry.status == "inbox")
            .filter(long_form_filter)
            .filter(
                Content.status.in_(
                    [
                        ContentStatus.NEW.value,
                        ContentStatus.PENDING.value,
                        ContentStatus.PROCESSING.value,
                    ]
                )
            )
            .filter(Content.created_at < stale_cutoff)
            .filter(~active_task_exists)
            .filter(~has_current_checkout)
            .distinct()
            .order_by(Content.created_at.asc(), Content.id.asc())
            .limit(limit)
            .all()
        )
        return [row[0] for row in rows]


def main() -> int:
    """Run the reconciliation."""
    args = parse_args()
    stale_ids = find_stale_content_ids(hours_old=args.hours_old, limit=args.limit)
    if not stale_ids:
        print("No stale orphaned long-form rows found.")
        return 0

    print(f"Found {len(stale_ids)} stale orphaned long-form rows.")
    print(f"Sample IDs: {stale_ids[:10]}")
    if not args.apply:
        print("Dry run only. Re-run with --apply to mark them failed.")
        return 0

    with get_db() as db:
        rows = db.query(Content).filter(Content.id.in_(stale_ids)).all()
        for content in rows:
            content.status = ContentStatus.FAILED.value
            content.error_message = STALE_ERROR_MESSAGE
            content.checked_out_by = None
            content.checked_out_at = None
        db.commit()

    print(f"Marked {len(stale_ids)} rows as failed with error '{STALE_ERROR_MESSAGE}'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
