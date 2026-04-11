#!/usr/bin/env python3
"""
Reset errored or stuck articles/podcasts for re-processing.
This script:
1. Finds articles/podcasts with 'failed' status OR stuck in 'processing' status
2. Optionally includes 'completed' content missing a summary
3. Optionally filters by date range
4. Resets status to 'new' and clears error data
5. Creates new processing tasks for the content

Note: Only processes articles and podcasts, not news items.
"""

import argparse
import json
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

# Add parent directory to path for imports (use os.path for Python 3.13 compatibility)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, func, or_
from sqlalchemy.orm import sessionmaker

from app.core.settings import get_settings
from app.models.schema import Content, ContentStatus, ProcessingTask

LOG_FILE_SUFFIXES = (".jsonl", ".log", ".txt")


def parse_datetime(value: str) -> datetime:
    """Parse a datetime string in various formats.

    Supports:
        - ISO format: 2024-01-15T10:30:00
        - Date only: 2024-01-15 (assumes start of day)
        - Date and time: 2024-01-15 10:30
    """
    formats = [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(
        f"Invalid datetime format: {value}. Use YYYY-MM-DD or YYYY-MM-DD HH:MM"
    )


def export_raw_logs_for_content(
    content_ids: list[int],
    logs_dir: str,
    output_path: str,
) -> tuple[int, int, str]:
    """Export raw log entries that mention the selected content ids.

    Args:
        content_ids: Target content ids being reset.
        logs_dir: Root directory containing log files.
        output_path: JSONL output file path for matching raw lines.

    Returns:
        Tuple of (matched_lines, matched_files, manifest_path).
        The manifest file contains one matched source log file per line.
    """
    if not content_ids:
        raise ValueError("No content ids provided for raw log export")

    if not os.path.isdir(logs_dir):
        raise FileNotFoundError(f"Logs directory not found: {logs_dir}")

    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    target_ids = {str(content_id) for content_id in content_ids}
    candidate_files: list[str] = []
    for root, _dirs, files in os.walk(logs_dir):
        for filename in files:
            if filename.endswith(LOG_FILE_SUFFIXES):
                candidate_files.append(os.path.join(root, filename))

    matched_lines = 0
    matched_paths: set[str] = set()
    with open(output_path, "w", encoding="utf-8") as output_file:
        for file_path in sorted(candidate_files):
            try:
                with open(file_path, encoding="utf-8", errors="replace") as source_file:
                    for line_number, line in enumerate(source_file, start=1):
                        numeric_tokens = set(re.findall(r"\b\d+\b", line))
                        if not numeric_tokens or target_ids.isdisjoint(numeric_tokens):
                            continue

                        record: dict[str, Any] = {
                            "file": file_path,
                            "line_number": line_number,
                            "line": line.rstrip("\n"),
                        }
                        output_file.write(json.dumps(record, ensure_ascii=True) + "\n")
                        matched_lines += 1
                        matched_paths.add(file_path)
            except OSError as exc:
                print(f"Warning: could not read log file {file_path}: {exc}", file=sys.stderr)

    manifest_path = f"{output_path}.files.txt"
    with open(manifest_path, "w", encoding="utf-8") as manifest_file:
        for matched_path in sorted(matched_paths):
            manifest_file.write(f"{matched_path}\n")

    return matched_lines, len(matched_paths), manifest_path


def reset_errored_content(
    days: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    stuck_hours: float | None = None,
    missing_summary: bool = False,
    dry_run: bool = False,
    return_raw_logs: bool = False,
    raw_logs_dir: str = "logs_from_server",
    raw_logs_output: str | None = None,
) -> None:
    """Reset errored or stuck articles/podcasts for re-processing.

    Args:
        days: Only reset content errored within this many days (None = all errored content)
        since: Only reset content errored on or after this datetime
        until: Only reset content errored before this datetime
        stuck_hours: Also include content stuck in 'processing' for more than X hours
        missing_summary: Also include 'completed' content that's missing a summary
        dry_run: If True, show what would be reset without making changes
        return_raw_logs: If True, export matching raw log entries for affected ids
        raw_logs_dir: Directory containing raw logs to scan
        raw_logs_output: Optional JSONL output path for raw log export
    """
    # Get database settings
    settings = get_settings()

    # Create engine and session
    engine = create_engine(str(settings.database_url))
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    with SessionLocal() as db:
        try:
            # Only process articles and podcasts, not news
            allowed_types = ["article", "podcast"]
            print(f"Filtering to content types: {', '.join(allowed_types)}")

            # Build query for errored content and optionally stuck processing content
            status_conditions = [Content.status == ContentStatus.FAILED.value]

            # Add stuck processing condition if specified
            if stuck_hours is not None:
                stuck_cutoff = datetime.now(UTC) - timedelta(hours=stuck_hours)
                stuck_condition = (Content.status == ContentStatus.PROCESSING.value) & (
                    Content.updated_at < stuck_cutoff
                )
                status_conditions.append(stuck_condition)
                print(f"Including content stuck in 'processing' for more than {stuck_hours} hours")

            # Add missing summary condition if specified
            if missing_summary:
                # Content is 'completed' but has no summary in metadata
                # Use json_extract for SQLite compatibility
                missing_summary_condition = (Content.status == ContentStatus.COMPLETED.value) & (
                    func.json_extract(Content.content_metadata, "$.summary").is_(None)
                )
                status_conditions.append(missing_summary_condition)
                print("Including 'completed' content missing a summary")

            query = db.query(Content).filter(
                Content.content_type.in_(allowed_types), or_(*status_conditions)
            )

            # Add date filters
            if days:
                cutoff_date = datetime.now(UTC) - timedelta(days=days)
                query = query.filter(Content.updated_at >= cutoff_date)
                cutoff_str = cutoff_date.strftime("%Y-%m-%d %H:%M:%S")
                print(f"Filtering to content errored since {cutoff_str} UTC")
            if since:
                query = query.filter(Content.updated_at >= since)
                since_str = since.strftime("%Y-%m-%d %H:%M:%S")
                print(f"Filtering to content errored on or after {since_str}")
            if until:
                query = query.filter(Content.updated_at < until)
                print(f"Filtering to content errored before {until.strftime('%Y-%m-%d %H:%M:%S')}")

            # Get errored/stuck content
            affected_content = query.all()

            if not affected_content:
                print("No errored, stuck, or incomplete content found matching criteria")
                return

            # Count by status for reporting
            failed_count = sum(
                1 for c in affected_content if c.status == ContentStatus.FAILED.value
            )
            stuck_count = sum(
                1 for c in affected_content if c.status == ContentStatus.PROCESSING.value
            )
            missing_summary_count = sum(
                1 for c in affected_content if c.status == ContentStatus.COMPLETED.value
            )
            print(f"Found {len(affected_content)} content items to reset:")
            if failed_count:
                print(f"  - {failed_count} with 'failed' status")
            if stuck_count:
                print(f"  - {stuck_count} stuck in 'processing' status")
            if missing_summary_count:
                print(f"  - {missing_summary_count} 'completed' but missing summary")

            content_ids = [content.id for content in affected_content if content.id is not None]
            if return_raw_logs:
                default_name = (
                    "reset_errored_content_raw_logs_"
                    f"{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.jsonl"
                )
                output_path = raw_logs_output or os.path.join("outputs", default_name)
                matched_lines, matched_files, manifest_path = export_raw_logs_for_content(
                    content_ids=content_ids,
                    logs_dir=raw_logs_dir,
                    output_path=output_path,
                )
                print(
                    f"Raw log export complete: {matched_lines} matching lines from "
                    f"{matched_files} files"
                )
                print(f"  - Lines output: {output_path}")
                print(f"  - File manifest: {manifest_path}")

            if dry_run:
                print("\nDRY RUN - Would reset the following content:")
                for content in affected_content[:20]:  # Show first 20 in dry run
                    ctype = content.content_type
                    status = content.status
                    print(f"  - ID: {content.id}, Type: {ctype}, Status: {status}")
                    print(f"    Source: {content.source}, URL: {(content.url or '')[:60]}...")
                    if content.error_message:
                        print(f"    Error: {content.error_message[:100]}...")
                if len(affected_content) > 20:
                    print(f"  ... and {len(affected_content) - 20} more")
                return

            # Delete existing processing tasks for affected content
            deleted_tasks = (
                db.query(ProcessingTask)
                .filter(ProcessingTask.content_id.in_(content_ids))
                .delete(synchronize_session=False)
            )
            print(f"Deleted {deleted_tasks} existing processing tasks")

            # Reset content status and clear error data
            reset_count = 0
            new_tasks = []

            for content in affected_content:
                # Capture original status before reset
                original_status = content.status
                original_error_message = content.error_message

                # Reset content fields
                content.status = ContentStatus.NEW.value
                content.error_message = None
                content.retry_count = 0
                content.checked_out_by = None
                content.checked_out_at = None
                content.processed_at = None
                # Keep content_metadata as it may contain useful partial data

                # Create new processing task
                task = ProcessingTask(
                    task_type="process_content",
                    content_id=content.id,
                    status="pending",
                    payload={
                        "content_type": content.content_type,
                        "url": content.url,
                        "source": content.source,
                        "reset_from_status": original_status,
                        "original_error": original_error_message[:500]
                        if original_error_message
                        else None,
                    },
                )
                new_tasks.append(task)
                reset_count += 1

            # Add all new tasks
            db.add_all(new_tasks)

            # Commit all changes
            db.commit()

            print(f"\nSuccessfully reset {reset_count} content items")
            print(f"Created {len(new_tasks)} new processing tasks")
            print("\nYou can now run 'python scripts/run_workers.py' to process the reset content")

            # Show summary by content type
            type_counts: dict[str, int] = {}
            for content in affected_content:
                if content.content_type is not None:
                    type_counts[content.content_type] = type_counts.get(content.content_type, 0) + 1

            print("\nContent reset by type:")
            for content_type, count in sorted(type_counts.items()):
                print(f"  - {content_type}: {count}")

        except Exception as e:
            db.rollback()
            print(f"Error: {e}")
            raise


def main():
    parser = argparse.ArgumentParser(
        description="Reset errored or stuck articles/podcasts for re-processing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Reset all failed articles/podcasts
  python scripts/reset_errored_content.py

  # Reset failed + stuck + missing summary content from last 14 days
  python scripts/reset_errored_content.py --days 14 --stuck-hours 24 --missing-summary

  # Reset content stuck in 'processing' for 24+ hours
  python scripts/reset_errored_content.py --stuck-hours 24

  # Reset 'completed' content that's missing a summary
  python scripts/reset_errored_content.py --missing-summary

  # Dry run to see what would be reset
  python scripts/reset_errored_content.py --days 14 --stuck-hours 24 --missing-summary --dry-run

  # Reset content errored since a specific date
  python scripts/reset_errored_content.py --since 2024-12-01

  # Reset and export matching raw log lines + matching raw log file list
  python scripts/reset_errored_content.py --since 2026-02-10 --until 2026-02-14 \\
    --return-raw-logs --raw-logs-dir logs_from_server

Note: Only processes articles and podcasts, not news items.
        """,
    )

    parser.add_argument(
        "--days",
        type=int,
        help="Only reset content errored within this many days (default: all errored content)",
    )

    parser.add_argument(
        "--since",
        type=parse_datetime,
        help="Only reset content errored on or after this datetime (YYYY-MM-DD)",
    )

    parser.add_argument(
        "--until",
        type=parse_datetime,
        help="Only reset content errored before this datetime (YYYY-MM-DD or YYYY-MM-DD HH:MM)",
    )

    parser.add_argument(
        "--stuck-hours",
        type=float,
        help="Also reset content stuck in 'processing' status for more than X hours",
    )

    parser.add_argument(
        "--missing-summary",
        action="store_true",
        help="Also reset 'completed' content that's missing a summary",
    )

    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be reset without making changes"
    )

    parser.add_argument(
        "--return-raw-logs",
        action="store_true",
        help="Export matching raw log entries for the selected content ids",
    )

    parser.add_argument(
        "--raw-logs-dir",
        default="logs_from_server",
        help="Directory containing raw logs to scan (default: logs_from_server)",
    )

    parser.add_argument(
        "--raw-logs-output",
        help="Optional JSONL output path for raw log export (default: outputs/<timestamp>.jsonl)",
    )

    args = parser.parse_args()

    # Validate conflicting options
    if args.days and (args.since or args.until):
        parser.error("Cannot use --days together with --since/--until")

    if args.dry_run:
        print("DRY RUN MODE - No changes will be made\n")

    reset_errored_content(
        days=args.days,
        since=args.since,
        until=args.until,
        stuck_hours=args.stuck_hours,
        missing_summary=args.missing_summary,
        dry_run=args.dry_run,
        return_raw_logs=args.return_raw_logs,
        raw_logs_dir=args.raw_logs_dir,
        raw_logs_output=args.raw_logs_output,
    )


if __name__ == "__main__":
    main()
