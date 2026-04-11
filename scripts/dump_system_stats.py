#!/usr/bin/env python3
"""Dump aggregated database and task queue statistics to stdout."""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Ensure application package imports resolve when executed as a script
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import Row, func  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.db import get_session_factory  # noqa: E402
from app.models.schema import (  # noqa: E402
    Content,
    ContentKnowledgeSave,
    ContentReadStatus,
    ContentUnlikes,
    ProcessingTask,
)


class StatsOptions(BaseModel):
    """Validated CLI options for the stats dump."""

    model_config = ConfigDict(extra="forbid")

    output_format: Literal["table", "json"] = Field(
        default="table", description="Output format for the stats report."
    )
    source_limit: int = Field(
        default=10,
        description="Maximum number of top sources to include in the report.",
    )
    platform_limit: int = Field(
        default=10,
        description="Maximum number of top platforms to include in the report.",
    )

    @field_validator("source_limit", "platform_limit")
    @classmethod
    def validate_positive_limit(cls, value: int) -> int:
        """Ensure limits are positive integers."""

        if value <= 0:
            msg = "Limits must be greater than zero"
            raise ValueError(msg)
        return value


class LabeledCount(BaseModel):
    """Simple labeled counter model for ranked metrics."""

    model_config = ConfigDict(extra="forbid")

    label: str
    count: int


class ContentStats(BaseModel):
    """Aggregated statistics about stored content."""

    model_config = ConfigDict(extra="forbid")

    total: int
    by_type: dict[str, int]
    by_status: dict[str, int]
    by_type_and_status: dict[str, dict[str, int]]
    classification: dict[str, int]
    aggregate_counts: dict[str, int]
    top_sources: list[LabeledCount]
    top_platforms: list[LabeledCount]
    latest_created_at: datetime | None
    latest_processed_at: datetime | None


class TaskStats(BaseModel):
    """Aggregated statistics about processing tasks."""

    model_config = ConfigDict(extra="forbid")

    total: int
    by_status: dict[str, int]
    pending_by_type: dict[str, int]
    processing_by_type: dict[str, int]
    recent_failures_last_hour: int
    oldest_pending_created_at: datetime | None
    max_retry_count: int


class EngagementStats(BaseModel):
    """Counts of read marks, favorites, and unlikes."""

    model_config = ConfigDict(extra="forbid")

    total_read_marks: int
    total_favorites: int
    total_unlikes: int


class SystemStats(BaseModel):
    """Composite statistics returned by the script."""

    model_config = ConfigDict(extra="forbid")

    content: ContentStats
    tasks: TaskStats
    engagement: EngagementStats


def parse_args(argv: list[str] | None = None) -> StatsOptions:
    """Parse raw argument values into validated options.

    Args:
        argv: Optional list of argument strings (defaults to sys.argv).

    Returns:
        Validated StatsOptions instance.
    """

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format (table or json).",
    )
    parser.add_argument(
        "--sources",
        type=int,
        default=10,
        help="Maximum number of sources to display.",
    )
    parser.add_argument(
        "--platforms",
        type=int,
        default=10,
        help="Maximum number of platforms to display.",
    )

    args = parser.parse_args(args=argv)
    return StatsOptions(
        output_format=args.format,
        source_limit=args.sources,
        platform_limit=args.platforms,
    )


def _result_dict(rows: Sequence[tuple[Any, int] | Row[tuple[Any, int]]]) -> dict[str, int]:
    """Convert grouped SQL rows into a normalized dictionary."""

    result: dict[str, int] = {}
    for key, count in rows:
        label = str(key) if key not in (None, "") else "unknown"
        result[label] = int(count or 0)
    return result


def _collect_labeled_counts(
    rows: Sequence[tuple[Any, int] | Row[tuple[Any, int]]],
) -> list[LabeledCount]:
    """Convert grouped SQL rows into labeled count models."""

    labeled: list[LabeledCount] = []
    for label, count in rows:
        normalized_label = str(label) if label not in (None, "") else "unknown"
        labeled.append(LabeledCount(label=normalized_label, count=int(count or 0)))
    return labeled


def collect_content_stats(
    session: Session, *, source_limit: int, platform_limit: int
) -> ContentStats:
    """Gather aggregated content statistics from the database.

    Args:
        session: Active SQLAlchemy session bound to the target database.
        source_limit: Maximum number of top sources to include.
        platform_limit: Maximum number of top platforms to include.

    Returns:
        Aggregated content statistics.
    """

    total = int(session.query(func.count(Content.id)).scalar() or 0)

    type_rows = (
        session.query(Content.content_type, func.count(Content.id))
        .group_by(Content.content_type)
        .all()
    )
    status_rows = (
        session.query(Content.status, func.count(Content.id)).group_by(Content.status).all()
    )
    type_status_rows = (
        session.query(Content.content_type, Content.status, func.count(Content.id))
        .group_by(Content.content_type, Content.status)
        .all()
    )
    classification_rows = (
        session.query(Content.classification, func.count(Content.id))
        .group_by(Content.classification)
        .all()
    )
    aggregate_rows = (
        session.query(Content.is_aggregate, func.count(Content.id))
        .group_by(Content.is_aggregate)
        .all()
    )
    source_rows = (
        session.query(Content.source, func.count(Content.id))
        .group_by(Content.source)
        .order_by(func.count(Content.id).desc())
        .limit(source_limit)
        .all()
    )
    platform_rows = (
        session.query(Content.platform, func.count(Content.id))
        .group_by(Content.platform)
        .order_by(func.count(Content.id).desc())
        .limit(platform_limit)
        .all()
    )

    by_type_and_status: dict[str, dict[str, int]] = defaultdict(dict)
    for content_type, status, count in type_status_rows:
        type_label = str(content_type) if content_type not in (None, "") else "unknown"
        status_label = str(status) if status not in (None, "") else "unknown"
        by_type_and_status[type_label][status_label] = int(count or 0)

    latest_created_at = session.query(func.max(Content.created_at)).scalar()
    latest_processed_at = session.query(func.max(Content.processed_at)).scalar()

    aggregate_counts: dict[str, int] = {}
    for is_aggregate, count in aggregate_rows:
        key = "aggregate" if bool(is_aggregate) else "non_aggregate"
        aggregate_counts[key] = int(count or 0)

    return ContentStats(
        total=total,
        by_type=_result_dict(type_rows),
        by_status=_result_dict(status_rows),
        by_type_and_status=dict(by_type_and_status),
        classification=_result_dict(classification_rows),
        aggregate_counts=aggregate_counts,
        top_sources=_collect_labeled_counts(source_rows),
        top_platforms=_collect_labeled_counts(platform_rows),
        latest_created_at=latest_created_at,
        latest_processed_at=latest_processed_at,
    )


def collect_task_stats(session: Session) -> TaskStats:
    """Gather task queue statistics from the database."""

    total = int(session.query(func.count(ProcessingTask.id)).scalar() or 0)

    status_rows = (
        session.query(ProcessingTask.status, func.count(ProcessingTask.id))
        .group_by(ProcessingTask.status)
        .all()
    )

    pending_rows = (
        session.query(ProcessingTask.task_type, func.count(ProcessingTask.id))
        .filter(ProcessingTask.status == "pending")
        .group_by(ProcessingTask.task_type)
        .all()
    )

    processing_rows = (
        session.query(ProcessingTask.task_type, func.count(ProcessingTask.id))
        .filter(ProcessingTask.status == "processing")
        .group_by(ProcessingTask.task_type)
        .all()
    )

    one_hour_ago = datetime.utcnow() - timedelta(hours=1)
    recent_failures = (
        session.query(func.count(ProcessingTask.id))
        .filter(ProcessingTask.status == "failed")
        .filter(ProcessingTask.completed_at != None)  # noqa: E711
        .filter(ProcessingTask.completed_at >= one_hour_ago)
        .scalar()
    )

    oldest_pending = (
        session.query(func.min(ProcessingTask.created_at))
        .filter(ProcessingTask.status == "pending")
        .scalar()
    )

    max_retry = session.query(func.max(ProcessingTask.retry_count)).scalar()

    return TaskStats(
        total=total,
        by_status=_result_dict(status_rows),
        pending_by_type=_result_dict(pending_rows),
        processing_by_type=_result_dict(processing_rows),
        recent_failures_last_hour=int(recent_failures or 0),
        oldest_pending_created_at=oldest_pending,
        max_retry_count=int(max_retry or 0),
    )


def collect_engagement_stats(session: Session) -> EngagementStats:
    """Return aggregate counts for engagement tables."""

    read_marks = int(session.query(func.count(ContentReadStatus.id)).scalar() or 0)
    favorites = int(session.query(func.count(ContentKnowledgeSave.id)).scalar() or 0)
    unlikes = int(session.query(func.count(ContentUnlikes.id)).scalar() or 0)

    return EngagementStats(
        total_read_marks=read_marks,
        total_favorites=favorites,
        total_unlikes=unlikes,
    )


def gather_system_stats(session: Session, *, options: StatsOptions) -> SystemStats:
    """Collect all system statistics using the provided session and options.

    Args:
        session: Active database session.
        options: Validated CLI options controlling limits and format.

    Returns:
        Composite SystemStats object.
    """

    content_stats = collect_content_stats(
        session,
        source_limit=options.source_limit,
        platform_limit=options.platform_limit,
    )
    task_stats = collect_task_stats(session)
    engagement_stats = collect_engagement_stats(session)

    return SystemStats(
        content=content_stats,
        tasks=task_stats,
        engagement=engagement_stats,
    )


def _format_dict_lines(data: dict[str, int], indent: int = 2) -> list[str]:
    """Format a dictionary into aligned lines for human-readable output."""

    if not data:
        return [" " * indent + "(none)"]

    lines: list[str] = []
    width = max(len(key) for key in data)
    for key, value in sorted(data.items(), key=lambda item: item[1], reverse=True):
        lines.append(" " * indent + f"{key.ljust(width)} : {value}")
    return lines


def _format_labeled_counts(items: list[LabeledCount], indent: int = 2) -> list[str]:
    """Format labeled count rows for display."""

    if not items:
        return [" " * indent + "(none)"]

    width = max(len(item.label) for item in items)
    lines = [" " * indent + f"{item.label.ljust(width)} : {item.count}" for item in items]
    return lines


def format_system_stats(stats: SystemStats, *, output_format: str) -> str:
    """Render the system statistics in either table or JSON format."""

    if output_format == "json":
        return stats.model_dump_json(indent=2)

    lines: list[str] = []

    lines.append("== Content ==")
    lines.append(f"Total content: {stats.content.total}")
    lines.append("By type:")
    lines.extend(_format_dict_lines(stats.content.by_type))
    lines.append("By status:")
    lines.extend(_format_dict_lines(stats.content.by_status))
    lines.append("By classification:")
    lines.extend(_format_dict_lines(stats.content.classification))
    lines.append("Aggregate vs non-aggregate:")
    lines.extend(_format_dict_lines(stats.content.aggregate_counts))
    lines.append("Top sources:")
    lines.extend(_format_labeled_counts(stats.content.top_sources))
    lines.append("Top platforms:")
    lines.extend(_format_labeled_counts(stats.content.top_platforms))

    latest_created = (
        stats.content.latest_created_at.isoformat(timespec="seconds")
        if stats.content.latest_created_at
        else "n/a"
    )
    latest_processed = (
        stats.content.latest_processed_at.isoformat(timespec="seconds")
        if stats.content.latest_processed_at
        else "n/a"
    )
    lines.append(f"Latest content created at: {latest_created}")
    lines.append(f"Latest content processed at: {latest_processed}")

    lines.append("")
    lines.append("== Tasks ==")
    lines.append(f"Total tasks: {stats.tasks.total}")
    lines.append("By status:")
    lines.extend(_format_dict_lines(stats.tasks.by_status))
    lines.append("Pending by type:")
    lines.extend(_format_dict_lines(stats.tasks.pending_by_type))
    lines.append("Processing by type:")
    lines.extend(_format_dict_lines(stats.tasks.processing_by_type))
    lines.append(f"Recent failures (last hour): {stats.tasks.recent_failures_last_hour}")
    oldest_pending = (
        stats.tasks.oldest_pending_created_at.isoformat(timespec="seconds")
        if stats.tasks.oldest_pending_created_at
        else "n/a"
    )
    lines.append(f"Oldest pending task created at: {oldest_pending}")
    lines.append(f"Max retry count: {stats.tasks.max_retry_count}")

    lines.append("")
    lines.append("== Engagement ==")
    lines.append(f"Read marks: {stats.engagement.total_read_marks}")
    lines.append(f"Favorites: {stats.engagement.total_favorites}")
    lines.append(f"Unlikes: {stats.engagement.total_unlikes}")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for dumping system statistics."""

    options = parse_args(argv)
    session_factory = get_session_factory()
    with session_factory() as session:
        stats = gather_system_stats(session, options=options)

    report = format_system_stats(stats, output_format=options.output_format)
    print(report)
    return 0


if __name__ == "__main__":  # pragma: no cover - command line entry point
    raise SystemExit(main())
