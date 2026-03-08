#!/usr/bin/env python3
"""Cancel pending generated-image tasks outside visible feed eligibility rules.

Usage:
    python scripts/cancel_ineligible_generate_image_tasks.py
    python scripts/cancel_ineligible_generate_image_tasks.py --apply
    python scripts/cancel_ineligible_generate_image_tasks.py --apply --limit 500
"""

from __future__ import annotations

import argparse
import os
import sys

# Add parent directory so we can import from app.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.db import get_db  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.models.contracts import TaskStatus, TaskType  # noqa: E402
from app.models.schema import ProcessingTask  # noqa: E402
from app.services.long_form_images import (  # noqa: E402
    CANCELLED_NOT_VISIBLE_UNDER_FEED_RULES,
    cancel_ineligible_pending_generate_image_tasks,
    list_ineligible_pending_generate_image_task_ids,
)


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Cancel pending generate-image tasks outside visible feed rules"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply cancellations. Default is dry-run output only.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum number of pending tasks to inspect.",
    )
    return parser.parse_args()


def main() -> None:
    """Run the cancellation scan."""
    setup_logging()
    args = parse_args()

    with get_db() as db:
        total_pending = (
            db.query(ProcessingTask.id)
            .filter(ProcessingTask.task_type == TaskType.GENERATE_IMAGE.value)
            .filter(ProcessingTask.status == TaskStatus.PENDING.value)
            .count()
        )
        print("Scanning pending generate-image tasks")
        print(f"  apply={args.apply}")
        print(f"  limit={args.limit}")
        print(f"  total_pending={total_pending}")

        if not args.apply:
            task_ids = list_ineligible_pending_generate_image_task_ids(
                db,
                limit=args.limit,
            )
            print(f"  would_cancel={len(task_ids)}")
            print(f"  reason={CANCELLED_NOT_VISIBLE_UNDER_FEED_RULES}")
            return

        task_ids = cancel_ineligible_pending_generate_image_tasks(
            db,
            limit=args.limit,
        )
        print(f"  cancelled={len(task_ids)}")
        print(f"  reason={CANCELLED_NOT_VISIBLE_UNDER_FEED_RULES}")


if __name__ == "__main__":
    main()
