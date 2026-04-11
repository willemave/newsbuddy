#!/usr/bin/env python3
"""Inspect and manage task queue state from the command line."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import create_engine, func
from sqlalchemy.orm import Session, sessionmaker

# Add parent directory for local imports.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.settings import get_settings  # noqa: E402
from app.models.schema import Content, ProcessingTask  # noqa: E402
from app.services.queue import TASK_QUEUE_BY_TYPE, TaskQueue, TaskStatus, TaskType  # noqa: E402

PROCESSING_TIMESTAMP_EXPR = func.coalesce(
    ProcessingTask.started_at,
    ProcessingTask.completed_at,
    ProcessingTask.created_at,
)

CONTENT_TYPE_CHOICES = ("article", "podcast", "news", "unknown")
MEDIA_TASK_TYPES = sorted(
    task_type.value
    for task_type, queue_name in TASK_QUEUE_BY_TYPE.items()
    if queue_name == TaskQueue.MEDIA
)


def _create_session_factory(database_url: str | None = None) -> tuple[Any, sessionmaker, str]:
    """Create a SQLAlchemy engine + session factory from app settings."""
    effective_database_url = database_url
    if not effective_database_url:
        settings = get_settings()
        effective_database_url = str(settings.database_url)

    engine = create_engine(effective_database_url)
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return engine, session_factory, effective_database_url


def _print_header(title: str) -> None:
    """Print a lightweight section header."""
    print(f"\n== {title} ==")


def show_status(session: Session, stale_hours: float, sample_limit: int) -> None:
    """Print queue, phase, and stale-processing snapshots."""
    _print_header("Queue Status")
    queue_rows = (
        session.query(
            ProcessingTask.queue_name,
            ProcessingTask.status,
            func.count(ProcessingTask.id).label("count"),
        )
        .group_by(ProcessingTask.queue_name, ProcessingTask.status)
        .order_by(ProcessingTask.queue_name, ProcessingTask.status)
        .all()
    )
    if not queue_rows:
        print("No tasks found.")
    else:
        for queue_name, status, count in queue_rows:
            print(f"{queue_name or 'unknown':12} {status or 'unknown':11} {int(count):6}")

    _print_header("Task Phases")
    task_rows = (
        session.query(
            ProcessingTask.task_type,
            ProcessingTask.status,
            func.count(ProcessingTask.id).label("count"),
        )
        .group_by(ProcessingTask.task_type, ProcessingTask.status)
        .order_by(ProcessingTask.task_type, ProcessingTask.status)
        .all()
    )
    for task_type, status, count in task_rows:
        print(f"{task_type or 'unknown':16} {status or 'unknown':11} {int(count):6}")

    _print_header("Pending Retry Buckets")
    retry_rows = (
        session.query(ProcessingTask.retry_count, func.count(ProcessingTask.id).label("count"))
        .filter(ProcessingTask.status == TaskStatus.PENDING.value)
        .group_by(ProcessingTask.retry_count)
        .order_by(ProcessingTask.retry_count)
        .all()
    )
    for retry_count, count in retry_rows:
        print(f"retry={int(retry_count):2} count={int(count):5}")

    cutoff = datetime.now(UTC) - timedelta(hours=stale_hours)
    stale_rows = (
        session.query(
            ProcessingTask.id,
            ProcessingTask.task_type,
            ProcessingTask.queue_name,
            ProcessingTask.content_id,
            ProcessingTask.started_at,
            ProcessingTask.created_at,
        )
        .filter(ProcessingTask.status == TaskStatus.PROCESSING.value)
        .filter(cutoff >= PROCESSING_TIMESTAMP_EXPR)
        .order_by(PROCESSING_TIMESTAMP_EXPR.asc())
        .limit(sample_limit)
        .all()
    )
    _print_header(f"Stale Processing (>{stale_hours}h)")
    if not stale_rows:
        print("None")
    else:
        for row in stale_rows:
            started_at = row.started_at.isoformat() if row.started_at else "None"
            created_at = row.created_at.isoformat() if row.created_at else "None"
            print(
                f"id={row.id} type={row.task_type} queue={row.queue_name} "
                f"content={row.content_id} started={started_at} created={created_at}"
            )

    pending_rows = (
        session.query(
            ProcessingTask.id,
            ProcessingTask.task_type,
            ProcessingTask.queue_name,
            ProcessingTask.content_id,
            ProcessingTask.retry_count,
            ProcessingTask.available_at,
        )
        .filter(ProcessingTask.status == TaskStatus.PENDING.value)
        .order_by(ProcessingTask.available_at.asc(), ProcessingTask.id.asc())
        .limit(sample_limit)
        .all()
    )
    _print_header(f"Oldest Pending (top {sample_limit})")
    if not pending_rows:
        print("None")
    else:
        for pending_row in pending_rows:
            available = pending_row.available_at.isoformat() if pending_row.available_at else "None"
            print(
                f"id={pending_row.id} type={pending_row.task_type} "
                f"queue={pending_row.queue_name} content={pending_row.content_id} "
                f"retry={pending_row.retry_count} available={available}"
            )


def _apply_common_filters(
    query: Any,
    *,
    statuses: list[str] | None,
    queue_name: str | None,
    task_type: str | None,
    content_ids: list[int] | None,
    content_type: str | None,
    older_than_hours: float | None,
) -> Any:
    """Apply shared task filters to a SQLAlchemy query."""
    if statuses:
        query = query.filter(ProcessingTask.status.in_(statuses))
    if queue_name:
        query = query.filter(ProcessingTask.queue_name == queue_name)
    if task_type:
        query = query.filter(ProcessingTask.task_type == task_type)
    if content_ids:
        query = query.filter(ProcessingTask.content_id.in_(content_ids))
    if older_than_hours is not None:
        cutoff = datetime.now(UTC) - timedelta(hours=older_than_hours)
        query = query.filter(cutoff >= PROCESSING_TIMESTAMP_EXPR)
    if content_type:
        query = query.join(Content, Content.id == ProcessingTask.content_id).filter(
            Content.content_type == content_type
        )
    return query


def clear_tasks(
    session: Session,
    *,
    statuses: list[str] | None,
    queue_name: str | None,
    task_type: str | None,
    content_ids: list[int] | None,
    content_type: str | None,
    older_than_hours: float | None,
    limit: int | None,
    dry_run: bool,
    force: bool,
) -> None:
    """Delete task rows matching the requested filters."""
    query = session.query(ProcessingTask.id)
    query = _apply_common_filters(
        query,
        statuses=statuses,
        queue_name=queue_name,
        task_type=task_type,
        content_ids=content_ids,
        content_type=content_type,
        older_than_hours=older_than_hours,
    )
    query = query.order_by(ProcessingTask.id.asc())
    if limit:
        task_ids = [int(row[0]) for row in query.limit(limit).all()]
    else:
        task_ids = [int(row[0]) for row in query.all()]

    print(f"Matched tasks: {len(task_ids)}")
    if task_ids:
        preview = ", ".join(str(task_id) for task_id in task_ids[:15])
        if len(task_ids) > 15:
            preview += ", ..."
        print(f"Task IDs: {preview}")

    if dry_run:
        print("Dry run only; no changes applied.")
        return

    if not force:
        raise SystemExit("Refusing to delete tasks without --yes")

    if not task_ids:
        print("No matching tasks to delete.")
        return

    deleted = (
        session.query(ProcessingTask)
        .filter(ProcessingTask.id.in_(task_ids))
        .delete(synchronize_session=False)
    )
    session.commit()
    print(f"Deleted tasks: {int(deleted)}")


def requeue_stale_processing(
    session: Session,
    *,
    hours: float,
    queue_name: str | None,
    task_type: str | None,
    dry_run: bool,
    force: bool,
) -> None:
    """Move stale processing tasks back to pending."""
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    query = session.query(ProcessingTask).filter(
        ProcessingTask.status == TaskStatus.PROCESSING.value
    )
    if queue_name:
        query = query.filter(ProcessingTask.queue_name == queue_name)
    if task_type:
        query = query.filter(ProcessingTask.task_type == task_type)
    query = query.filter(cutoff >= PROCESSING_TIMESTAMP_EXPR).order_by(
        PROCESSING_TIMESTAMP_EXPR.asc()
    )
    rows = query.all()

    print(f"Matched stale processing tasks: {len(rows)} (older than {hours}h)")
    for row in rows[:20]:
        started = row.started_at.isoformat() if row.started_at else "None"
        print(
            f"id={row.id} type={row.task_type} queue={row.queue_name} "
            f"content={row.content_id} started={started}"
        )
    if len(rows) > 20:
        print(f"... plus {len(rows) - 20} more")

    if dry_run:
        print("Dry run only; no changes applied.")
        return

    if not force:
        raise SystemExit("Refusing to update tasks without --yes")

    if not rows:
        print("No stale processing tasks found.")
        return

    now = datetime.now(UTC)
    for row in rows:
        row.status = TaskStatus.PENDING.value
        row.started_at = None
        row.completed_at = None
        row.available_at = now
        row.locked_at = None
        row.locked_by = None
        row.lease_expires_at = None
        row.error_message = None
        row.retry_count = int(row.retry_count or 0) + 1
    session.commit()
    print(f"Requeued tasks: {len(rows)}")


def move_media_tasks(
    session: Session,
    *,
    statuses: list[str],
    dry_run: bool,
    force: bool,
) -> None:
    """Move media tasks into the dedicated media queue."""
    rows = (
        session.query(ProcessingTask)
        .filter(ProcessingTask.task_type.in_(MEDIA_TASK_TYPES))
        .filter(ProcessingTask.status.in_(statuses))
        .filter(ProcessingTask.queue_name != TaskQueue.MEDIA.value)
        .order_by(ProcessingTask.id.asc())
        .all()
    )
    print(f"Media tasks to move: {len(rows)}")
    for row in rows[:20]:
        print(f"id={row.id} status={row.status} queue={row.queue_name} content={row.content_id}")
    if len(rows) > 20:
        print(f"... plus {len(rows) - 20} more")

    if dry_run:
        print("Dry run only; no changes applied.")
        return

    if not force:
        raise SystemExit("Refusing to move tasks without --yes")

    for row in rows:
        row.queue_name = TaskQueue.MEDIA.value
    session.commit()
    print(f"Moved tasks: {len(rows)}")


def move_tasks_between_queues(
    session: Session,
    *,
    from_queue: str,
    to_queue: str,
    statuses: list[str],
    task_type: str | None,
    dry_run: bool,
    force: bool,
) -> None:
    """Move tasks from one queue partition to another."""
    if from_queue == to_queue:
        raise SystemExit("--from-queue and --to-queue must differ")

    query = (
        session.query(ProcessingTask)
        .filter(ProcessingTask.queue_name == from_queue)
        .filter(ProcessingTask.status.in_(statuses))
    )
    if task_type:
        query = query.filter(ProcessingTask.task_type == task_type)

    rows = query.order_by(ProcessingTask.id.asc()).all()

    print(f"Tasks to move from {from_queue} to {to_queue}: {len(rows)}")
    for row in rows[:20]:
        print(
            f"id={row.id} status={row.status} queue={row.queue_name} "
            f"type={row.task_type} content={row.content_id}"
        )
    if len(rows) > 20:
        print(f"... plus {len(rows) - 20} more")

    if dry_run:
        print("Dry run only; no changes applied.")
        return

    if not force:
        raise SystemExit("Refusing to move tasks without --yes")

    for row in rows:
        row.queue_name = to_queue
    session.commit()
    print(f"Moved tasks: {len(rows)}")


def build_parser() -> argparse.ArgumentParser:
    """Build the queue-control CLI parser."""
    parser = argparse.ArgumentParser(
        description="Inspect queue state and clear/requeue targeted task groups."
    )
    parser.add_argument(
        "--database-url",
        help="Override database URL instead of using app settings/.env",
    )
    subparsers = parser.add_subparsers(dest="command")

    status_parser = subparsers.add_parser("status", help="Show queue/task status summary")
    status_parser.add_argument(
        "--stale-hours",
        type=float,
        default=2.0,
        help="Flag processing tasks older than this many hours (default: 2)",
    )
    status_parser.add_argument(
        "--sample-limit",
        type=int,
        default=20,
        help="Max rows to print for detailed sections (default: 20)",
    )

    clear_parser = subparsers.add_parser("clear", help="Delete targeted task rows")
    clear_parser.add_argument(
        "--status",
        dest="statuses",
        action="append",
        choices=[status.value for status in TaskStatus],
        help="Filter by task status; repeatable",
    )
    clear_parser.add_argument(
        "--queue",
        choices=[queue.value for queue in TaskQueue],
        help="Filter by queue partition",
    )
    clear_parser.add_argument(
        "--task-type",
        choices=[task_type.value for task_type in TaskType],
        help="Filter by task type",
    )
    clear_parser.add_argument(
        "--content-type",
        choices=CONTENT_TYPE_CHOICES,
        help="Filter by linked content type",
    )
    clear_parser.add_argument(
        "--content-id",
        action="append",
        type=int,
        dest="content_ids",
        help="Filter by content id; repeatable",
    )
    clear_parser.add_argument(
        "--older-than-hours",
        type=float,
        help="Filter to tasks older than this many hours",
    )
    clear_parser.add_argument(
        "--limit",
        type=int,
        help="Delete at most N matching rows",
    )
    clear_parser.add_argument("--dry-run", action="store_true", help="Preview only")
    clear_parser.add_argument(
        "--yes",
        action="store_true",
        help="Apply destructive changes",
    )

    stale_parser = subparsers.add_parser(
        "requeue-stale",
        help="Requeue stale processing tasks back to pending",
    )
    stale_parser.add_argument(
        "--hours",
        type=float,
        default=2.0,
        help="Treat processing tasks older than this as stale (default: 2)",
    )
    stale_parser.add_argument(
        "--queue",
        choices=[queue.value for queue in TaskQueue],
        help="Only requeue stale tasks in this queue",
    )
    stale_parser.add_argument(
        "--task-type",
        choices=[task_type.value for task_type in TaskType],
        help="Only requeue stale tasks for this task type",
    )
    stale_parser.add_argument("--dry-run", action="store_true", help="Preview only")
    stale_parser.add_argument("--yes", action="store_true", help="Apply changes")

    move_parser = subparsers.add_parser(
        "move-media",
        aliases=["move-transcribe"],
        help="Move media tasks into the media queue",
    )
    move_parser.add_argument(
        "--status",
        dest="statuses",
        action="append",
        choices=[status.value for status in TaskStatus],
        help="Statuses to move (default: pending + processing)",
    )
    move_parser.add_argument("--dry-run", action="store_true", help="Preview only")
    move_parser.add_argument("--yes", action="store_true", help="Apply changes")

    move_queue_parser = subparsers.add_parser(
        "move-queue",
        help="Move tasks between queue partitions",
    )
    move_queue_parser.add_argument(
        "--from-queue",
        required=True,
        choices=[queue.value for queue in TaskQueue],
        help="Current queue partition",
    )
    move_queue_parser.add_argument(
        "--to-queue",
        required=True,
        choices=[queue.value for queue in TaskQueue],
        help="Destination queue partition",
    )
    move_queue_parser.add_argument(
        "--status",
        dest="statuses",
        action="append",
        choices=[status.value for status in TaskStatus],
        help="Statuses to move (default: pending)",
    )
    move_queue_parser.add_argument(
        "--task-type",
        choices=[task_type.value for task_type in TaskType],
        help="Only move tasks for this task type",
    )
    move_queue_parser.add_argument("--dry-run", action="store_true", help="Preview only")
    move_queue_parser.add_argument("--yes", action="store_true", help="Apply changes")

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the queue-control CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        args.command = "status"

    engine, session_factory, effective_database_url = _create_session_factory(args.database_url)
    print(f"Database: {effective_database_url}")

    try:
        with session_factory() as session:
            if args.command == "status":
                show_status(
                    session,
                    stale_hours=float(args.stale_hours),
                    sample_limit=int(args.sample_limit),
                )
                return 0

            if args.command == "clear":
                clear_tasks(
                    session,
                    statuses=list(args.statuses or []),
                    queue_name=args.queue,
                    task_type=args.task_type,
                    content_ids=list(args.content_ids or []),
                    content_type=args.content_type,
                    older_than_hours=args.older_than_hours,
                    limit=args.limit,
                    dry_run=bool(args.dry_run),
                    force=bool(args.yes),
                )
                return 0

            if args.command == "requeue-stale":
                requeue_stale_processing(
                    session,
                    hours=float(args.hours),
                    queue_name=args.queue,
                    task_type=args.task_type,
                    dry_run=bool(args.dry_run),
                    force=bool(args.yes),
                )
                return 0

            if args.command in {"move-media", "move-transcribe"}:
                move_media_tasks(
                    session,
                    statuses=list(
                        args.statuses or [TaskStatus.PENDING.value, TaskStatus.PROCESSING.value]
                    ),
                    dry_run=bool(args.dry_run),
                    force=bool(args.yes),
                )
                return 0

            if args.command == "move-queue":
                move_tasks_between_queues(
                    session,
                    from_queue=args.from_queue,
                    to_queue=args.to_queue,
                    statuses=list(args.statuses or [TaskStatus.PENDING.value]),
                    task_type=args.task_type,
                    dry_run=bool(args.dry_run),
                    force=bool(args.yes),
                )
                return 0

    finally:
        engine.dispose()

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
