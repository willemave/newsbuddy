#!/usr/bin/env python3
"""
Run one worker process for a queue, with N claim-loop threads inside it.
"""

import argparse
import os
import sys
import threading
import time

# Add parent directory so we can import from app
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.db import init_db
from app.core.logging import get_logger, setup_logging
from app.core.settings import get_settings
from app.pipeline.threaded_task_processor import ThreadedTaskProcessor
from app.services.queue import TaskQueue, get_queue_service

logger = get_logger(__name__)

# Each claim thread holds one session while processing; the rest covers lease
# heartbeats and finalization bursts.
WORKER_DB_POOL_MARGIN = 4


def main():
    parser = argparse.ArgumentParser(description="Run the queue task processor")
    parser.add_argument(
        "--max-tasks",
        type=int,
        default=None,
        help=(
            "Maximum number of tasks to process before exiting. "
            "Bounded runs use one claim loop to keep the cap exact."
        ),
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--stats-interval",
        type=int,
        default=30,
        help="Show stats every N seconds (default: 30, 0 to disable)",
    )
    parser.add_argument(
        "--queue",
        choices=[queue.value for queue in TaskQueue],
        default=TaskQueue.CONTENT.value,
        help="Queue partition to process",
    )
    parser.add_argument(
        "--worker-slot",
        type=int,
        default=1,
        help="Worker slot number for stable worker IDs",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=None,
        help=(
            "Claim-loop threads in this process (default: per-queue setting). "
            "Use 1 for the historical sequential worker."
        ),
    )
    args = parser.parse_args()

    # Setup logging
    log_level = "DEBUG" if args.debug else "INFO"
    setup_logging(level=log_level)

    threads = (
        args.threads if args.threads is not None else get_settings().worker_thread_count(args.queue)
    )
    if threads < 1:
        logger.error("--threads must be at least 1 (got %s)", threads)
        return 1

    logger.info("=" * 60)
    logger.info("Queue Task Processor")
    logger.info("=" * 60)
    logger.info("Queue: %s", args.queue)
    logger.info("Worker slot: %s", args.worker_slot)
    logger.info("Claim threads: %s", threads)

    # Initialize database. Pool sizing follows the thread count so that the
    # worker processes together stay well inside Postgres max_connections.
    logger.info("Initializing database...")
    init_db(pool_size=threads + WORKER_DB_POOL_MARGIN, max_overflow=threads)

    # Check initial queue stats
    queue_service = get_queue_service()
    stats = queue_service.get_queue_stats()
    pending_total = stats.get("pending_by_queue", {}).get(args.queue, 0)

    by_status = stats.get("by_status", {})
    logger.info("Initial queue state:")
    logger.info(f"  Total tasks: {sum(by_status.values())}")
    logger.info(f"  Pending: {by_status.get('pending', 0)}")
    logger.info(f"  Processing: {by_status.get('processing', 0)}")
    logger.info(f"  Completed: {by_status.get('completed', 0)}")
    logger.info(f"  Failed: {by_status.get('failed', 0)}")

    queue_pending_by_type = stats.get("pending_by_queue_type", {}).get(args.queue, {})

    if pending_total > 0:
        logger.info("\nPending tasks by type (queue=%s):", args.queue)
        for task_type, count in queue_pending_by_type.items():
            logger.info(f"  {task_type}: {count}")

    # Start processor
    logger.info("\nStarting task processor...")
    if args.max_tasks:
        logger.info(f"Will process up to {args.max_tasks} tasks")
    logger.info("Press Ctrl+C to stop")

    processor = ThreadedTaskProcessor(
        queue_name=args.queue,
        worker_slot=args.worker_slot,
        threads=threads,
    )

    # Start stats thread if enabled
    stats_stopped = threading.Event()
    if args.stats_interval > 0:

        def show_stats():
            while not stats_stopped.wait(args.stats_interval):
                stats = queue_service.get_queue_stats()
                pending = stats.get("pending_by_queue", {}).get(args.queue, 0)
                by_status = stats.get("by_status", {})
                logger.info(
                    "Queue stats (%s) - Pending: %s, Completed: %s, Failed: %s",
                    args.queue,
                    pending,
                    by_status.get("completed", 0),
                    by_status.get("failed", 0),
                )

        threading.Thread(target=show_stats, daemon=True).start()

    try:
        logger.debug("Calling processor.run()...")
        processor.run(max_tasks=args.max_tasks)
    except KeyboardInterrupt:
        logger.info("\nShutting down gracefully...")

        # Show final stats
        time.sleep(1)  # Let workers finish
        final_stats = queue_service.get_queue_stats()
        final_by_status = final_stats.get("by_status", {})
        final_pending = final_stats.get("pending_by_queue", {}).get(args.queue, 0)
        logger.info("\nFinal queue stats:")
        logger.info(f"  Completed: {final_by_status.get('completed', 0)}")
        logger.info(f"  Failed: {final_by_status.get('failed', 0)}")
        logger.info(f"  Remaining in {args.queue}: {final_pending}")
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        return 1
    finally:
        stats_stopped.set()

    return 0


if __name__ == "__main__":
    sys.exit(main())
