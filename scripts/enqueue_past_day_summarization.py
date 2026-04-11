#!/usr/bin/env python3
"""Script to enqueue content from the past day for summarization."""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Activate virtual environment if it exists
venv_path = project_root / ".venv"
if venv_path.exists():
    activate_this = venv_path / "bin" / "activate_this.py"
    if activate_this.exists():
        with open(activate_this) as handle:
            exec(handle.read(), {"__file__": str(activate_this)})

from sqlalchemy import and_  # noqa: E402

from app.core.db import get_db  # noqa: E402
from app.core.logging import get_logger, setup_logging  # noqa: E402
from app.models.metadata import ContentStatus  # noqa: E402
from app.models.schema import Content  # noqa: E402
from app.services.queue import QueueService, TaskType  # noqa: E402

# Set up logging
setup_logging()
logger = get_logger(__name__)


def enqueue_past_day_for_summarization(
    dry_run: bool = False,
    limit: int | None = None,
    days_back: float = 1,
    content_types: list[str] | None = None,
):
    """
    Enqueue content from the past day(s) for summarization.

    Args:
        dry_run: If True, just show what would be enqueued without making changes
        limit: Maximum number of items to enqueue
        days_back: Number of days to look back (default 1)
        content_types: List of content types to process (default all)
    """
    cutoff_date = datetime.now(UTC) - timedelta(days=days_back)

    print("Starting enqueue_past_day_for_summarization")
    print(f"  dry_run={dry_run}")
    print(f"  limit={limit}")
    print(f"  days_back={days_back}")
    print(f"  cutoff_date={cutoff_date.isoformat()}")
    print(f"  content_types={content_types or 'all'}")

    queue_service = QueueService()

    with get_db() as db:
        # Build query for content from the past day(s)
        # Note: Content.created_at is stored without timezone info
        cutoff_date_naive = cutoff_date.replace(tzinfo=None)
        query = db.query(Content).filter(
            and_(
                Content.created_at >= cutoff_date_naive,
                Content.status == ContentStatus.COMPLETED.value,
            )
        )

        # Filter by content types if specified
        if content_types:
            query = query.filter(Content.content_type.in_(content_types))

        # Order by creation date (oldest first)
        query = query.order_by(Content.created_at)

        if limit:
            query = query.limit(limit)

        content_items = query.all()

        print(f"Found {len(content_items)} content items from the past {days_back} day(s)")

        if dry_run:
            print("DRY RUN - No tasks will be enqueued")
            print("\nContent to be enqueued:")
            for item in content_items:
                title_preview = item.title[:60] if item.title else "No title"
                created_at = item.created_at.isoformat() if item.created_at else "unknown"
                print(
                    f"  [{item.content_type}] ID={item.id} {title_preview} (created: {created_at})"
                )
            return

        enqueued_count = 0
        skipped_count = 0

        total_items = len(content_items)
        for i, content in enumerate(content_items, 1):
            try:
                logger.info(
                    "[%d/%d] Enqueueing %s ID=%s: %s",
                    i,
                    total_items,
                    content.content_type,
                    content.id,
                    content.title,
                )

                # Check if content has necessary data for summarization
                skip_item = False
                content_metadata = (
                    content.content_metadata if isinstance(content.content_metadata, dict) else {}
                )

                if content.content_type == "podcast" and not content_metadata.get("transcript"):
                    logger.warning(f"No transcript found for podcast {content.id}, skipping")
                    skipped_count += 1
                    skip_item = True
                elif content.content_type == "article" and not content_metadata.get("content"):
                    logger.warning(f"No content found for article {content.id}, skipping")
                    skipped_count += 1
                    skip_item = True

                if skip_item:
                    continue

                # Enqueue summarization task
                task_id = queue_service.enqueue(
                    task_type=TaskType.SUMMARIZE,
                    content_id=content.id,
                    payload={
                        "force_resummarize": True,
                        "source": "enqueue_past_day_script",
                        "enqueued_at": datetime.now(UTC).isoformat(),
                    },
                )

                logger.info(f"Enqueued task {task_id} for content {content.id}")
                enqueued_count += 1

            except Exception as e:
                logger.error(
                    f"Error enqueueing {content.content_type} {content.id}: {e}", exc_info=True
                )

        print("\nSummary:")
        print(f"Total content items: {len(content_items)}")
        print(f"Successfully enqueued: {enqueued_count}")
        print(f"Skipped (no content/transcript): {skipped_count}")


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Enqueue content from the past day(s) for summarization"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be enqueued without making changes",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit number of items to enqueue",
    )
    parser.add_argument(
        "--days-back",
        type=float,
        default=1,
        help="Number of days to look back (default: 1, can be decimal like 0.5)",
    )
    parser.add_argument(
        "--types",
        nargs="+",
        choices=["article", "podcast", "video"],
        help="Content types to process (default: all)",
    )

    args = parser.parse_args()

    enqueue_past_day_for_summarization(
        dry_run=args.dry_run,
        limit=args.limit,
        days_back=args.days_back,
        content_types=args.types,
    )


if __name__ == "__main__":
    main()
