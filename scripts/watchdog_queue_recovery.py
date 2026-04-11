#!/usr/bin/env python3
"""Automated queue watchdog for recovery actions.

Runs the same safety actions operators have been running manually:
1. Move media tasks into the dedicated media queue.
2. Requeue stale media processing tasks.
3. Requeue stale content-pipeline processing tasks.

The script supports one-shot mode (cron) and loop mode (supervisor/systemd).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import create_engine, func
from sqlalchemy.orm import Session, sessionmaker

# Add parent directory for local imports.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.logging import get_logger, setup_logging  # noqa: E402
from app.core.observability import bound_log_context, build_log_extra  # noqa: E402
from app.core.settings import get_settings  # noqa: E402
from app.models.schema import ProcessingTask  # noqa: E402
from app.services.queue import TASK_QUEUE_BY_TYPE, TaskQueue, TaskStatus, TaskType  # noqa: E402

logger = get_logger(__name__)

PROCESSING_TIMESTAMP_EXPR = func.coalesce(
    ProcessingTask.started_at,
    ProcessingTask.completed_at,
    ProcessingTask.created_at,
)
MEDIA_TASK_TYPES = sorted(
    task_type.value
    for task_type, queue_name in TASK_QUEUE_BY_TYPE.items()
    if queue_name == TaskQueue.MEDIA
)


@dataclass
class ActionResult:
    """Result for a single watchdog action."""

    action_name: str
    touched_count: int
    task_ids: list[int]
    metadata: dict[str, Any]


@dataclass
class WatchdogRunResult:
    """Top-level watchdog result payload."""

    started_at: datetime
    finished_at: datetime
    dry_run: bool
    moved_media: ActionResult
    requeued_media: ActionResult
    requeued_process_content: ActionResult
    requeued_process_news_item: ActionResult
    requeued_generate_agent_digest: ActionResult

    @property
    def total_touched(self) -> int:
        """Return the total touched tasks across all actions."""
        return (
            self.moved_media.touched_count
            + self.requeued_media.touched_count
            + self.requeued_process_content.touched_count
            + self.requeued_process_news_item.touched_count
            + self.requeued_generate_agent_digest.touched_count
        )


def _env_float(name: str, default: float) -> float:
    """Read a float from env with fallback."""
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        logger.warning("Invalid float env %s=%s, using default=%s", name, value, default)
        return default


def _env_int(name: str, default: int) -> int:
    """Read an int from env with fallback."""
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning("Invalid int env %s=%s, using default=%s", name, value, default)
        return default


def _create_session_factory(database_url: str | None = None) -> tuple[sessionmaker, str]:
    """Create DB session factory from explicit URL or settings."""
    effective_database_url = database_url or str(get_settings().database_url)
    engine = create_engine(effective_database_url)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False), effective_database_url


def _move_media_tasks(
    session: Session,
    *,
    dry_run: bool,
    limit: int | None,
) -> ActionResult:
    """Move media tasks to the media queue."""
    query = (
        session.query(ProcessingTask)
        .filter(ProcessingTask.task_type.in_(MEDIA_TASK_TYPES))
        .filter(ProcessingTask.status.in_([TaskStatus.PENDING.value, TaskStatus.PROCESSING.value]))
        .filter(ProcessingTask.queue_name != TaskQueue.MEDIA.value)
        .order_by(ProcessingTask.id.asc())
    )
    if limit:
        query = query.limit(limit)

    rows = query.all()
    task_ids = [int(row.id) for row in rows if row.id is not None]

    if not dry_run:
        for row in rows:
            row.queue_name = TaskQueue.MEDIA.value

    return ActionResult(
        action_name="move_media",
        touched_count=len(rows),
        task_ids=task_ids,
        metadata={
            "target_queue": TaskQueue.MEDIA.value,
            "task_types": MEDIA_TASK_TYPES,
            "statuses": [TaskStatus.PENDING.value, TaskStatus.PROCESSING.value],
            "limit": limit,
        },
    )


def _requeue_stale_tasks(
    session: Session,
    *,
    task_types: list[str],
    action_name: str,
    stale_hours: float,
    dry_run: bool,
    limit: int | None,
) -> ActionResult:
    """Requeue stale processing tasks for one logical task family."""
    cutoff = datetime.now(UTC) - timedelta(hours=stale_hours)
    query = (
        session.query(ProcessingTask)
        .filter(ProcessingTask.status == TaskStatus.PROCESSING.value)
        .filter(ProcessingTask.task_type.in_(task_types))
        .filter(cutoff >= PROCESSING_TIMESTAMP_EXPR)
        .order_by(PROCESSING_TIMESTAMP_EXPR.asc(), ProcessingTask.id.asc())
    )
    if limit:
        query = query.limit(limit)

    rows = query.all()
    task_ids = [int(row.id) for row in rows if row.id is not None]

    if not dry_run:
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

    return ActionResult(
        action_name=action_name,
        touched_count=len(rows),
        task_ids=task_ids,
        metadata={
            "task_types": task_types,
            "stale_hours": stale_hours,
            "limit": limit,
        },
    )


def _record_watchdog_events(result: WatchdogRunResult) -> None:
    """Emit structured watchdog action/run events."""
    run_id = datetime.now(UTC).strftime("%Y%m%d%H%M%S")

    action_results = [
        result.moved_media,
        result.requeued_media,
        result.requeued_process_content,
        result.requeued_process_news_item,
        result.requeued_generate_agent_digest,
    ]
    for action in action_results:
        logger.info(
            "Queue watchdog action completed",
            extra=build_log_extra(
                component="queue_watchdog",
                operation=action.action_name,
                event_name="cron.run",
                status="completed",
                job_name="watchdog_queue_recovery",
                trigger="cron",
                context_data={
                    "run_id": run_id,
                    "touched_count": action.touched_count,
                    "task_ids": action.task_ids[:100],
                    "metadata": action.metadata,
                },
            ),
        )

    logger.info(
        "Queue watchdog run completed",
        extra=build_log_extra(
            component="queue_watchdog",
            operation="queue_recovery",
            event_name="cron.run",
            status="completed",
            duration_ms=max((result.finished_at - result.started_at).total_seconds() * 1000, 0.0),
            job_name="watchdog_queue_recovery",
            trigger="cron",
            context_data={
                "run_id": run_id,
                "total_touched": result.total_touched,
                "moved_media": result.moved_media.touched_count,
                "requeued_media": result.requeued_media.touched_count,
                "requeued_process_content": result.requeued_process_content.touched_count,
                "requeued_process_news_item": result.requeued_process_news_item.touched_count,
                "requeued_generate_agent_digest": (
                    result.requeued_generate_agent_digest.touched_count
                ),
                "dry_run": result.dry_run,
            },
        ),
    )


def _send_slack_alert(webhook_url: str, result: WatchdogRunResult) -> tuple[bool, str]:
    """Send a concise Slack alert for touched watchdog actions."""
    payload = {
        "text": (
            "Queue watchdog touched tasks"
            f" | total={result.total_touched}"
            f" move_media={result.moved_media.touched_count}"
            f" requeue_media={result.requeued_media.touched_count}"
            f" requeue_process_content={result.requeued_process_content.touched_count}"
            f" requeue_process_news_item={result.requeued_process_news_item.touched_count}"
        )
    }

    try:
        response = httpx.post(webhook_url, json=payload, timeout=10.0)
        response.raise_for_status()
        return True, "sent"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to send watchdog Slack alert: %s", exc)
        return False, str(exc)


def _record_watchdog_alert_event(
    *,
    result: WatchdogRunResult,
    status: str,
    detail: str,
    threshold: int,
) -> None:
    """Emit Slack alert attempt outcome for watchdog visibility."""
    logger_method = logger.info if status in {"completed", "sent", "skipped"} else logger.warning
    logger_method(
        "Queue watchdog alert handled",
        extra=build_log_extra(
            component="queue_watchdog",
            operation="slack_alert",
            event_name="cron.run",
            status=status,
            job_name="watchdog_queue_recovery",
            trigger="cron",
            context_data={
                "total_touched": result.total_touched,
                "alert_threshold": threshold,
                "detail": detail,
            },
        ),
    )


def run_watchdog_once(
    *,
    session: Session,
    media_stale_hours: float | None = None,
    transcribe_stale_hours: float | None = None,
    process_content_stale_hours: float,
    process_news_item_stale_hours: float | None = None,
    generate_agent_digest_stale_hours: float | None = None,
    alert_threshold: int,
    slack_webhook_url: str | None,
    dry_run: bool,
    action_limit: int | None,
) -> WatchdogRunResult:
    """Execute one watchdog cycle and optionally persist/alert."""
    started_at = datetime.now(UTC)
    effective_media_stale_hours = (
        media_stale_hours
        if media_stale_hours is not None
        else (transcribe_stale_hours if transcribe_stale_hours is not None else 2.0)
    )
    effective_generate_agent_digest_stale_hours = (
        generate_agent_digest_stale_hours if generate_agent_digest_stale_hours is not None else 2.0
    )

    moved_media = _move_media_tasks(
        session,
        dry_run=dry_run,
        limit=action_limit,
    )
    requeued_media = _requeue_stale_tasks(
        session,
        task_types=MEDIA_TASK_TYPES,
        action_name="requeue_stale_media",
        stale_hours=effective_media_stale_hours,
        dry_run=dry_run,
        limit=action_limit,
    )
    requeued_process_content = _requeue_stale_tasks(
        session,
        task_types=[TaskType.PROCESS_CONTENT.value],
        action_name="requeue_stale_process_content",
        stale_hours=process_content_stale_hours,
        dry_run=dry_run,
        limit=action_limit,
    )
    effective_process_news_item_stale_hours = (
        process_news_item_stale_hours
        if process_news_item_stale_hours is not None
        else process_content_stale_hours
    )
    requeued_process_news_item = _requeue_stale_tasks(
        session,
        task_types=[TaskType.PROCESS_NEWS_ITEM.value],
        action_name="requeue_stale_process_news_item",
        stale_hours=effective_process_news_item_stale_hours,
        dry_run=dry_run,
        limit=action_limit,
    )
    requeued_generate_agent_digest = _requeue_stale_tasks(
        session,
        task_types=[TaskType.GENERATE_AGENT_DIGEST.value],
        action_name="requeue_stale_generate_agent_digest",
        stale_hours=effective_generate_agent_digest_stale_hours,
        dry_run=dry_run,
        limit=action_limit,
    )

    finished_at = datetime.now(UTC)
    result = WatchdogRunResult(
        started_at=started_at,
        finished_at=finished_at,
        dry_run=dry_run,
        moved_media=moved_media,
        requeued_media=requeued_media,
        requeued_process_content=requeued_process_content,
        requeued_process_news_item=requeued_process_news_item,
        requeued_generate_agent_digest=requeued_generate_agent_digest,
    )

    if dry_run:
        return result

    _record_watchdog_events(result)

    if result.total_touched < alert_threshold:
        return result

    if not slack_webhook_url:
        _record_watchdog_alert_event(
            result=result,
            status="skipped",
            detail="No QUEUE_WATCHDOG_SLACK_WEBHOOK_URL configured",
            threshold=alert_threshold,
        )
        return result

    sent, detail = _send_slack_alert(slack_webhook_url, result)
    _record_watchdog_alert_event(
        result=result,
        status="sent" if sent else "failed",
        detail=detail,
        threshold=alert_threshold,
    )
    return result


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse watchdog CLI arguments."""
    parser = argparse.ArgumentParser(description="Queue recovery watchdog")
    parser.add_argument(
        "--database-url",
        help="Override database URL instead of using app settings/.env",
    )
    parser.add_argument(
        "--media-stale-hours",
        type=float,
        default=_env_float(
            "QUEUE_WATCHDOG_MEDIA_STALE_HOURS",
            _env_float("QUEUE_WATCHDOG_TRANSCRIBE_STALE_HOURS", 2.0),
        ),
        help="Requeue media processing tasks older than this many hours",
    )
    parser.add_argument(
        "--generate-agent-digest-stale-hours",
        type=float,
        default=_env_float("QUEUE_WATCHDOG_GENERATE_AGENT_DIGEST_STALE_HOURS", 2.0),
        help="Requeue generate_agent_digest tasks older than this many hours",
    )
    parser.add_argument(
        "--process-content-stale-hours",
        type=float,
        default=_env_float("QUEUE_WATCHDOG_PROCESS_CONTENT_STALE_HOURS", 2.0),
        help="Requeue process_content processing tasks older than this many hours",
    )
    parser.add_argument(
        "--process-news-item-stale-hours",
        type=float,
        default=_env_float(
            "QUEUE_WATCHDOG_PROCESS_NEWS_ITEM_STALE_HOURS",
            _env_float("QUEUE_WATCHDOG_PROCESS_CONTENT_STALE_HOURS", 2.0),
        ),
        help="Requeue process_news_item processing tasks older than this many hours",
    )
    parser.add_argument(
        "--alert-threshold",
        type=int,
        default=_env_int("QUEUE_WATCHDOG_ALERT_THRESHOLD", 1),
        help="Alert only when touched task total is >= threshold",
    )
    parser.add_argument(
        "--slack-webhook-url",
        default=os.getenv("QUEUE_WATCHDOG_SLACK_WEBHOOK_URL"),
        help="Optional Slack webhook URL for watchdog alerts",
    )
    parser.add_argument(
        "--action-limit",
        type=int,
        default=None,
        help="Cap rows touched per action for safety",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Run continuously with sleep interval between cycles",
    )
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=300,
        help="Loop interval in seconds (default: 300)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview only; no writes")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    return parser.parse_args(argv)


def _print_result(result: WatchdogRunResult) -> None:
    """Print watchdog run summary."""
    print("Queue watchdog run summary")
    print(f"  started_at: {result.started_at.isoformat()}")
    print(f"  finished_at: {result.finished_at.isoformat()}")
    print(f"  dry_run: {result.dry_run}")
    print(f"  move_media: {result.moved_media.touched_count}")
    print(f"  requeue_stale_media: {result.requeued_media.touched_count}")
    print(f"  requeue_stale_process_content: {result.requeued_process_content.touched_count}")
    print(f"  requeue_stale_process_news_item: {result.requeued_process_news_item.touched_count}")
    print(
        "  requeue_stale_generate_agent_digest: "
        f"{result.requeued_generate_agent_digest.touched_count}"
    )
    print(f"  total_touched: {result.total_touched}")


def main(argv: list[str] | None = None) -> int:
    """Run the queue watchdog in one-shot or loop mode."""
    args = _parse_args(argv)
    setup_logging(level="DEBUG" if args.debug else "INFO")

    session_factory, effective_database_url = _create_session_factory(args.database_url)
    with bound_log_context(job_name="watchdog_queue_recovery", trigger="cron", source="cron"):
        logger.info(
            "Queue watchdog targeting database",
            extra=build_log_extra(
                component="queue_watchdog",
                operation="startup",
                event_name="cron.run",
                status="started",
                job_name="watchdog_queue_recovery",
                trigger="cron",
                context_data={
                    "database_url": effective_database_url,
                    "dry_run": bool(args.dry_run),
                },
            ),
        )

        def _run_cycle() -> int:
            with session_factory() as session:
                try:
                    result = run_watchdog_once(
                        session=session,
                        media_stale_hours=float(args.media_stale_hours),
                        generate_agent_digest_stale_hours=float(
                            args.generate_agent_digest_stale_hours
                        ),
                        process_content_stale_hours=float(args.process_content_stale_hours),
                        process_news_item_stale_hours=float(args.process_news_item_stale_hours),
                        alert_threshold=max(int(args.alert_threshold), 1),
                        slack_webhook_url=args.slack_webhook_url,
                        dry_run=bool(args.dry_run),
                        action_limit=args.action_limit,
                    )
                    if not args.dry_run:
                        session.commit()
                    _print_result(result)
                    return 0
                except Exception as exc:  # noqa: BLE001
                    session.rollback()
                    logger.exception(
                        "Queue watchdog cycle failed",
                        extra=build_log_extra(
                            component="queue_watchdog",
                            operation="queue_recovery",
                            event_name="cron.run",
                            status="failed",
                            job_name="watchdog_queue_recovery",
                            trigger="cron",
                            context_data={"failure_class": type(exc).__name__},
                        ),
                    )
                    return 1

        if not args.loop:
            return _run_cycle()

        exit_code = 0
        interval_seconds = max(int(args.interval_seconds), 30)
        logger.info(
            "Starting watchdog loop",
            extra=build_log_extra(
                component="queue_watchdog",
                operation="loop",
                event_name="cron.run",
                status="started",
                job_name="watchdog_queue_recovery",
                trigger="cron",
                context_data={"interval_seconds": interval_seconds},
            ),
        )

        try:
            while True:
                cycle_code = _run_cycle()
                if cycle_code != 0:
                    exit_code = cycle_code
                time.sleep(interval_seconds)
        except KeyboardInterrupt:
            logger.info("Queue watchdog loop interrupted by user")
        return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
