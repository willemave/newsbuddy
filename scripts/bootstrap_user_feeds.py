#!/usr/bin/env python3
"""
Bootstrap user feeds by populating inbox from existing content.

This script creates ContentStatusEntry records to add existing content
from the shared content pool into user inboxes. It does NOT run scrapers.

Use this to:
1. Initialize feeds for new users
2. Backfill user inboxes with existing content
3. Reset/rebuild user feeds

For scraping new content, use: python scripts/run_scrapers.py

Usage:
    python scripts/bootstrap_user_feeds.py [--users USER_ID [USER_ID ...]] \
        [--days N] [--content-types TYPE [TYPE ...]]
"""

import argparse
import os
import sys
from datetime import datetime, timedelta
from time import perf_counter

# Add parent directory so we can import from app
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.db import get_db, init_db
from app.core.logging import get_logger, setup_logging
from app.core.observability import bound_log_context, build_log_extra
from app.models.contracts import ContentStatus, ContentType
from app.models.db import Content, ContentStatusEntry, User
from app.services.scraper_configs import ensure_inbox_status

logger = get_logger(__name__)


def get_active_user_ids(db, specific_user_ids: list[int] | None = None) -> list[int]:
    """Get all active user IDs or specific user IDs."""
    query = db.query(User.id).filter(User.is_active.is_(True))

    if specific_user_ids:
        query = query.filter(User.id.in_(specific_user_ids))

    user_ids = [row[0] for row in query.all()]
    logger.info(f"Found {len(user_ids)} active user(s) to bootstrap")
    return user_ids


def get_existing_content_ids(
    db,
    days: int | None = None,
    content_types: list[str] | None = None,
    statuses: list[str] | None = None,
) -> list[int]:
    """
    Get content IDs from the shared content pool.

    Args:
        db: Database session
        days: Only get content from last N days (None = all content)
        content_types: Filter by content types (article, podcast, news)
        statuses: Filter by processing status (completed, new, pending, etc.)

    Returns:
        List of content IDs
    """
    query = db.query(Content.id)

    # Filter by date
    if days is not None:
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        query = query.filter(Content.created_at >= cutoff_date)

    # Filter by content type
    if content_types:
        query = query.filter(Content.content_type.in_(content_types))

    # Filter by status
    if statuses:
        query = query.filter(Content.status.in_(statuses))
    else:
        # Default: only completed content (skip failed/processing)
        query = query.filter(
            Content.status.in_(
                [
                    ContentStatus.COMPLETED.value,
                    ContentStatus.NEW.value,
                ]
            )
        )

    content_ids = [row[0] for row in query.all()]
    logger.info(f"Found {len(content_ids)} existing content items")
    return content_ids


def populate_user_inbox(
    db,
    user_id: int,
    content_ids: list[int],
    skip_existing: bool = True,
) -> dict[str, int]:
    """
    Populate a user's inbox with content.

    Args:
        db: Database session
        user_id: User ID to populate
        content_ids: Content IDs to add
        skip_existing: Skip content already in inbox (default: True)

    Returns:
        Dictionary with stats: {added, skipped, errors}
    """
    stats = {"added": 0, "skipped": 0, "errors": 0}

    for content_id in content_ids:
        try:
            # Get content to check type
            content = db.query(Content).filter(Content.id == content_id).first()
            if not content:
                stats["errors"] += 1
                continue

            # Skip news items (they're shown to everyone without inbox entries)
            if content.content_type == ContentType.NEWS.value:
                stats["skipped"] += 1
                continue

            # Check if already in inbox
            if skip_existing:
                existing = (
                    db.query(ContentStatusEntry)
                    .filter(
                        ContentStatusEntry.user_id == user_id,
                        ContentStatusEntry.content_id == content_id,
                    )
                    .first()
                )
                if existing:
                    stats["skipped"] += 1
                    continue

            # Create inbox entry
            created = ensure_inbox_status(
                db,
                user_id=user_id,
                content_id=content_id,
                content_type=content.content_type,
            )

            if created:
                stats["added"] += 1
            else:
                stats["skipped"] += 1

        except Exception as e:
            logger.error(f"Error adding content {content_id} to user {user_id} inbox: {e}")
            stats["errors"] += 1

    if stats["added"] > 0:
        db.commit()

    return stats


def bootstrap_users(
    user_ids: list[int],
    days: int | None,
    content_types: list[str] | None,
    statuses: list[str] | None,
) -> dict[str, int]:
    """
    Bootstrap feeds for multiple users.

    Returns:
        Dictionary with aggregate stats
    """
    total_stats = {"added": 0, "skipped": 0, "errors": 0}

    with get_db() as db:
        # Get existing content to add to inboxes
        content_ids = get_existing_content_ids(
            db,
            days=days,
            content_types=content_types,
            statuses=statuses,
        )

        if not content_ids:
            logger.warning("No content found to bootstrap")
            return total_stats

        # Populate each user's inbox
        for user_id in user_ids:
            logger.info(f"\nBootstrapping user {user_id}...")

            stats = populate_user_inbox(
                db,
                user_id=user_id,
                content_ids=content_ids,
                skip_existing=True,
            )

            logger.info(
                f"  Added: {stats['added']}, Skipped: {stats['skipped']}, Errors: {stats['errors']}"
            )

            # Accumulate stats
            for key in total_stats:
                total_stats[key] += stats[key]

    return total_stats


def show_final_statistics(user_ids: list[int] | None = None):
    """Show final database statistics."""
    with get_db() as db:
        logger.info("\n" + "=" * 60)
        logger.info("FINAL STATISTICS")
        logger.info("=" * 60)

        # Content stats by status
        logger.info("\nContent by status:")
        for status in ContentStatus:
            count = db.query(Content).filter(Content.status == status.value).count()
            logger.info(f"  {status.value}: {count}")

        # Content stats by type
        logger.info("\nContent by type:")
        for content_type in ContentType:
            count = db.query(Content).filter(Content.content_type == content_type.value).count()
            logger.info(f"  {content_type.value}s: {count}")

        # User inbox stats
        logger.info("\nUser inbox statistics:")

        if user_ids:
            # Show only specified users
            target_user_ids = user_ids
        else:
            # Show all users with content
            target_user_ids = [
                row[0] for row in db.query(ContentStatusEntry.user_id).distinct().all()
            ]

        for user_id in target_user_ids:
            user = db.query(User).filter(User.id == user_id).first()
            user_email = user.email if user else "Unknown"

            inbox_count = (
                db.query(ContentStatusEntry)
                .filter(
                    ContentStatusEntry.user_id == user_id,
                    ContentStatusEntry.status == "inbox",
                )
                .count()
            )

            logger.info(f"  User {user_id} ({user_email}): {inbox_count} items in inbox")


def main():
    parser = argparse.ArgumentParser(
        description="Bootstrap user feeds from existing content",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Bootstrap all users with all existing content
  %(prog)s

  # Bootstrap specific user
  %(prog)s --users 1

  # Bootstrap multiple users
  %(prog)s --users 1 2 3

  # Only content from last 7 days
  %(prog)s --days 7

  # Only articles and podcasts
  %(prog)s --content-types article podcast

  # Only completed content
  %(prog)s --statuses completed

  # Combine filters
  %(prog)s --users 1 --days 30 --content-types article
        """,
    )

    parser.add_argument(
        "--users",
        nargs="+",
        type=int,
        metavar="USER_ID",
        help="Bootstrap feeds only for specific user IDs (space-separated)",
    )
    parser.add_argument(
        "--days",
        type=int,
        metavar="N",
        help="Only include content from last N days (default: all content)",
    )
    parser.add_argument(
        "--content-types",
        nargs="+",
        choices=["article", "podcast", "news"],
        help="Filter by content type (default: all types)",
    )
    parser.add_argument(
        "--statuses",
        nargs="+",
        choices=["new", "pending", "processing", "completed", "failed", "skipped"],
        help="Filter by processing status (default: completed, new)",
    )
    parser.add_argument(
        "--show-stats",
        action="store_true",
        default=True,
        help="Show detailed statistics after bootstrap (default: True)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    # Setup logging
    log_level = "DEBUG" if args.debug else "INFO"
    setup_logging(level=log_level)
    run_started_at = perf_counter()

    logger.info("=" * 60)
    logger.info("User Feed Bootstrap")
    logger.info("=" * 60)

    # Initialize database
    logger.info("Initializing database...")
    init_db()

    try:
        # Get user IDs to bootstrap
        with get_db() as db:
            user_ids = get_active_user_ids(db, specific_user_ids=args.users)

        if not user_ids:
            logger.warning("No active users found. Exiting.")
            return 0

        # Track the bootstrap run
        run_config = {
            "debug": args.debug,
            "specific_users": args.users,
            "user_count": len(user_ids),
            "days": args.days,
            "content_types": args.content_types,
            "statuses": args.statuses,
        }

        with bound_log_context(job_name="bootstrap_user_feeds", trigger="manual", source="manual"):
            logger.info(
                "Bootstrap run started",
                extra=build_log_extra(
                    component="cron",
                    operation="bootstrap_user_feeds",
                    event_name="cron.run",
                    status="started",
                    job_name="bootstrap_user_feeds",
                    trigger="manual",
                    context_data=run_config,
                ),
            )
            # Bootstrap users
            stats = bootstrap_users(
                user_ids=user_ids,
                days=args.days,
                content_types=args.content_types,
                statuses=args.statuses,
            )
            logger.info(
                "Bootstrap run completed",
                extra=build_log_extra(
                    component="cron",
                    operation="bootstrap_user_feeds",
                    event_name="cron.run",
                    status="completed",
                    duration_ms=(perf_counter() - run_started_at) * 1000,
                    job_name="bootstrap_user_feeds",
                    trigger="manual",
                    context_data={"user_count": len(user_ids), **stats},
                ),
            )

            # Summary
            logger.info("\n" + "=" * 60)
            logger.info("Bootstrap Summary:")
            logger.info(f"  Users processed: {len(user_ids)}")
            logger.info(f"  Items added: {stats['added']}")
            logger.info(f"  Items skipped: {stats['skipped']}")
            logger.info(f"  Errors: {stats['errors']}")

            # Show final statistics
            if args.show_stats:
                show_final_statistics(user_ids=user_ids)

        logger.info("\nBootstrap completed successfully!")
        logger.info("\nNext steps:")
        logger.info("  1. To scrape new content: python scripts/run_scrapers.py")
        logger.info("  2. To process content: ./scripts/start_workers.sh")

        return 0

    except KeyboardInterrupt:
        logger.warning("\nProcess interrupted by user")
        return 1
    except Exception as e:
        logger.error(f"Error during bootstrap: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
