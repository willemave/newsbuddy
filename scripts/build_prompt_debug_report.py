#!/usr/bin/env python3
"""Sync logs and build a local prompt-debug report."""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, time
from pathlib import Path

# Ensure app imports resolve when running as a script.
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.prompt_debug_report import (  # noqa: E402
    DEFAULT_COMPONENTS,
    PromptReportOptions,
    SyncOptions,
    build_prompt_debug_report,
    write_report_files,
)


def _parse_datetime_arg(raw: str | None, *, end_of_day: bool) -> datetime | None:
    """Parse datetime/date CLI values into UTC datetimes."""
    if raw is None:
        return None

    text = raw.strip()
    if not text:
        return None

    if "T" not in text:
        try:
            day = datetime.fromisoformat(text).date()
        except ValueError as exc:
            msg = f"Invalid date value: {raw}"
            raise ValueError(msg) from exc
        parsed = datetime.combine(day, time.max if end_of_day else time.min)
    else:
        normalized = text.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            msg = f"Invalid datetime value: {raw}"
            raise ValueError(msg) from exc

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for prompt report generation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sync", action="store_true", help="Sync logs from remote before parsing.")
    parser.add_argument("--remote-user", default="willem", help="Remote SSH user.")
    parser.add_argument("--remote-host", default="news-app-server", help="Remote SSH host.")
    parser.add_argument(
        "--remote-logs-dir",
        default="/data/logs",
        help="Primary remote log directory.",
    )
    parser.add_argument(
        "--remote-app-dir",
        default="/opt/news_app",
        help="Remote app directory (used for /logs sync).",
    )
    parser.add_argument(
        "--local-logs-dir",
        default="./logs_from_server",
        help="Local directory containing synced logs.",
    )
    parser.add_argument(
        "--db-url",
        default=None,
        help="Database URL override. Defaults to app settings.",
    )
    parser.add_argument(
        "--since",
        default=None,
        help="Start datetime filter (ISO format or YYYY-MM-DD).",
    )
    parser.add_argument(
        "--until",
        default=None,
        help="End datetime filter (ISO format or YYYY-MM-DD).",
    )
    parser.add_argument("--hours", type=int, default=24, help="Time window in hours.")
    parser.add_argument("--limit", type=int, default=200, help="Maximum failures to include.")
    parser.add_argument(
        "--components",
        default=",".join(DEFAULT_COMPONENTS),
        help="Comma-separated component filter list.",
    )
    parser.add_argument(
        "--include-json",
        action="store_true",
        help="Write JSON report alongside Markdown output.",
    )
    parser.add_argument(
        "--output-dir",
        default="./outputs",
        help="Directory for report artifacts.",
    )
    return parser.parse_args(args=argv)


def main(argv: list[str] | None = None) -> int:
    """Build prompt-debug report and write artifacts."""
    args = parse_args(argv)
    components = tuple(part.strip() for part in args.components.split(",") if part.strip())
    since = _parse_datetime_arg(args.since, end_of_day=False)
    until = _parse_datetime_arg(args.until, end_of_day=True)

    options = PromptReportOptions(
        logs_dir=Path(args.local_logs_dir),
        db_url=args.db_url,
        hours=args.hours,
        since=since,
        until=until,
        limit=args.limit,
        components=components,
        include_json=args.include_json,
        output_dir=Path(args.output_dir),
        sync=SyncOptions(
            enabled=args.sync,
            remote_user=args.remote_user,
            remote_host=args.remote_host,
            remote_logs_dir=args.remote_logs_dir,
            remote_app_dir=args.remote_app_dir,
            local_logs_dir=Path(args.local_logs_dir),
        ),
    )

    report = build_prompt_debug_report(options)
    markdown_path, json_path = write_report_files(report, options)

    print(f"Prompt debug report written: {markdown_path}")
    if json_path is not None:
        print(f"JSON report written: {json_path}")

    print(f"Failures analyzed: {report.total_failures}")
    print(f"Records scanned: {report.total_records_scanned}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
