#!/usr/bin/env python3
"""
Run scrapers to populate content links without processing.
This script only runs the scrapers and saves content to the database.
Use run_workers.py to process the scraped content.
"""

import argparse
import os
import sys
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

# Add parent directory so we can import from app
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func

from app.core.db import get_db, init_db
from app.core.logging import get_logger, setup_logging
from app.core.observability import bound_log_context, build_log_extra
from app.models.metadata import ContentStatus, ContentType
from app.models.schema import Content
from app.scraping.runner import ScraperRunner
from app.services.queue import get_queue_service

logger = get_logger(__name__)


def _get_backpressure_status() -> dict[str, object]:
    """Return queue backlog health for scraper admission control."""
    return get_queue_service().get_backpressure_status()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run scrapers to populate content links")
    parser.add_argument(
        "--scrapers",
        nargs="*",
        help="Specific scrapers to run (e.g., hackernews reddit). If not specified, runs all.",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--show-stats", action="store_true", help="Show detailed statistics after scraping"
    )
    args = parser.parse_args()

    # Setup logging
    log_level = "DEBUG" if args.debug else "INFO"
    setup_logging(level=log_level)
    run_started_at = perf_counter()

    logger.info("=" * 60)
    logger.info("Content Scrapers")
    logger.info("=" * 60)

    # Initialize database
    logger.info("Initializing database...")
    init_db()

    try:
        # Create scraper runner
        scraper_runner = ScraperRunner()

        # Show initial statistics
        if args.show_stats:
            with get_db() as db:
                total_content = db.query(Content).count()
                new_content = (
                    db.query(Content).filter(Content.status == ContentStatus.NEW.value).count()
                )
                logger.info("Initial database stats:")
                logger.info(f"  Total content: {total_content}")
                logger.info(f"  New content: {new_content}")

        # Determine run type
        if args.scrapers:
            # If specific scrapers are provided, use the first one as run type
            # or 'custom' if multiple
            run_type = args.scrapers[0] if len(args.scrapers) == 1 else "custom"
        else:
            run_type = "all"

        # Create run configuration
        run_config = {"debug": args.debug, "specific_scrapers": args.scrapers}

        with bound_log_context(job_name="run_scrapers", trigger="manual", source="cron"):
            backpressure = _get_backpressure_status()
            if bool(backpressure["should_throttle"]):
                logger.warning(
                    "Skipping scraper cron run due to queue backpressure",
                    extra=build_log_extra(
                        component="cron",
                        operation="run_scrapers",
                        event_name="cron.run",
                        status="skipped",
                        job_name="run_scrapers",
                        trigger="manual",
                        context_data={
                            "skip_reason": "queue_backpressure",
                            "backpressure": backpressure,
                        },
                    ),
                )
                return 0

            logger.info(
                "Scraper cron run started",
                extra=build_log_extra(
                    component="cron",
                    operation="run_scrapers",
                    event_name="cron.run",
                    status="started",
                    job_name="run_scrapers",
                    trigger="manual",
                    context_data=run_config | {"run_type": run_type},
                ),
            )
            # Show available scrapers
            available_scrapers = scraper_runner.list_scrapers()
            logger.info(f"Available scrapers: {', '.join(available_scrapers)}")

            scraper_results: dict[str, int] = {}
            scraper_stats: dict[str, Any] = {}
            scrapers_to_run = args.scrapers or available_scrapers
            stopped_due_to_backpressure = False

            if not args.scrapers:
                logger.info("\nRunning all scrapers...")

            for index, scraper_name in enumerate(scrapers_to_run):
                if index > 0:
                    backpressure = _get_backpressure_status()
                    if bool(backpressure["should_throttle"]):
                        stopped_due_to_backpressure = True
                        logger.warning(
                            "Stopping scraper cron run after current backlog crossed threshold",
                            extra=build_log_extra(
                                component="cron",
                                operation="run_scrapers",
                                event_name="cron.run",
                                status="degraded",
                                job_name="run_scrapers",
                                trigger="manual",
                                source=scraper_name,
                                context_data={
                                    "stop_reason": "queue_backpressure",
                                    "backpressure": backpressure,
                                },
                            ),
                        )
                        break

                logger.info(f"\nRunning {scraper_name} scraper...")
                stats = scraper_runner.run_scraper_with_stats(scraper_name)
                if stats:
                    scraper_results[scraper_name] = stats.saved
                    scraper_stats[scraper_name] = stats
                    logger.info(
                        "Scraper summary",
                        extra=build_log_extra(
                            component="cron",
                            operation="run_scrapers",
                            event_name="scraper.run",
                            status="completed",
                            job_name="run_scrapers",
                            source=scraper_name,
                            context_data={
                                "scraped": stats.scraped,
                                "saved": stats.saved,
                                "duplicates": stats.duplicates,
                                "errors": stats.errors,
                            },
                        ),
                    )
                    logger.info(
                        "  Scraped: %s, Saved: %s, Duplicates: %s, Errors: %s",
                        stats.scraped,
                        stats.saved,
                        stats.duplicates,
                        stats.errors,
                    )
                else:
                    scraper_results[scraper_name] = 0
                    logger.warning(f"  No stats returned for {scraper_name}")

            if scraper_stats:
                total_scraped = sum(s.scraped for s in scraper_stats.values())
                total_saved = sum(s.saved for s in scraper_stats.values())
                total_duplicates = sum(s.duplicates for s in scraper_stats.values())
                total_errors = sum(s.errors for s in scraper_stats.values())
                logger.info(
                    "Scraper run summary",
                    extra=build_log_extra(
                        component="cron",
                        operation="run_scrapers",
                        event_name="scraper.run",
                        status="completed" if not stopped_due_to_backpressure else "degraded",
                        job_name="run_scrapers",
                        context_data={
                            "total_scraped": total_scraped,
                            "total_saved": total_saved,
                            "total_duplicates": total_duplicates,
                            "total_errors": total_errors,
                            "stopped_due_to_backpressure": stopped_due_to_backpressure,
                            "scraper_stats": {
                                name: {
                                    "scraped": s.scraped,
                                    "saved": s.saved,
                                    "duplicates": s.duplicates,
                                    "errors": s.errors,
                                }
                                for name, s in scraper_stats.items()
                            },
                        },
                    ),
                )

            # Summary
            total_scraped = sum(scraper_results.values())
            logger.info("\n" + "=" * 60)
            logger.info("Scraping completed. Summary:")
            for scraper, count in scraper_results.items():
                logger.info(f"  {scraper}: {count} new items")
            logger.info(f"  Total: {total_scraped} new items")

            # Show final statistics
            if args.show_stats:
                with get_db() as db:
                    logger.info("\n" + "=" * 60)
                    logger.info("FINAL STATISTICS")
                    logger.info("=" * 60)

                    # Content stats
                    logger.info("Content Statistics:")
                    # Count by status
                    for status in ContentStatus:
                        count = db.query(Content).filter(Content.status == status.value).count()
                        logger.info(f"  {status.value}: {count}")

                    # Count by type
                    logger.info("\nContent by type:")
                    for content_type in ContentType:
                        count = (
                            db.query(Content)
                            .filter(Content.content_type == content_type.value)
                            .count()
                        )
                        logger.info(f"  {content_type.value}s: {count}")

                    # Recent activity
                    today = datetime.now(UTC).date()
                    scraped_today = (
                        db.query(Content).filter(func.date(Content.created_at) >= today).count()
                    )
                    logger.info(f"\nScraped today: {scraped_today}")

                    # NEW content ready for processing
                    new_content = (
                        db.query(Content).filter(Content.status == ContentStatus.NEW.value).count()
                    )
                    logger.info(f"\nContent ready for processing: {new_content}")
                    if new_content > 0:
                        logger.info(
                            "Run 'python scripts/run_workers.py' to process the scraped content"
                        )

            logger.info(
                "Scraper cron run completed",
                extra=build_log_extra(
                    component="cron",
                    operation="run_scrapers",
                    event_name="cron.run",
                    status="completed" if not stopped_due_to_backpressure else "degraded",
                    duration_ms=(perf_counter() - run_started_at) * 1000,
                    job_name="run_scrapers",
                    trigger="manual",
                    context_data={
                        "run_type": run_type,
                        "considered_count": len(args.scrapers or available_scrapers),
                        "saved_count": sum(scraper_results.values()),
                        "failed_count": sum(
                            1 for stats in scraper_stats.values() if stats.errors > 0
                        ),
                    },
                ),
            )
        return 0

    except KeyboardInterrupt:
        logger.warning("\nProcess interrupted by user")
        return 1
    except Exception as e:
        logger.exception(
            "Scraper cron run failed",
            extra=build_log_extra(
                component="cron",
                operation="run_scrapers",
                event_name="cron.run",
                status="failed",
                duration_ms=(perf_counter() - run_started_at) * 1000,
                job_name="run_scrapers",
                trigger="manual",
                context_data={"failure_class": type(e).__name__},
            ),
        )
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
