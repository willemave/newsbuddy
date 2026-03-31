"""Local CLI entrypoint for production operator workflows."""

from __future__ import annotations

import argparse
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any

from admin.config import AdminConfig, resolve_config
from admin.output import Envelope, EnvelopeError, emit
from admin.ssh import RemoteCommandError, rsync_from_remote, run_remote_module, run_remote_script


class AdminCLIError(RuntimeError):
    """Raised for expected CLI failures."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details


@dataclass(frozen=True)
class CommandResult:
    """Normalized command output."""

    data: Any
    warnings: list[str]


class AdminArgumentParser(argparse.ArgumentParser):
    """Argument parser with action-oriented error messages."""

    def error(self, message: str) -> None:
        hint = _build_parser_hint(self.prog, message)
        formatted = message if hint is None else f"{message}\n\n{hint}"
        super().error(formatted)


def main(argv: list[str] | None = None) -> int:
    """Run the local operator CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)
    config = resolve_config(args)
    command_name = _command_name(args)

    try:
        result = dispatch(args, config=config)
        emit(
            Envelope(ok=True, command=command_name, data=result.data, warnings=result.warnings),
            args.output,
        )
        return 0
    except (AdminCLIError, RemoteCommandError, ValueError, subprocess.CalledProcessError) as exc:
        details = exc.details if isinstance(exc, AdminCLIError) else None
        if isinstance(exc, RemoteCommandError) and exc.stderr:
            details = {"stderr": exc.stderr}
        if isinstance(exc, subprocess.CalledProcessError):
            details = {
                "returncode": exc.returncode,
                "stdout": exc.stdout,
                "stderr": exc.stderr,
            }
        emit(
            Envelope(
                ok=False,
                command=command_name,
                error=EnvelopeError(message=str(exc), details=details),
            ),
            args.output,
        )
        return 1


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level parser."""
    parser = AdminArgumentParser(
        prog="admin",
        description="Production operator CLI for Newsly operations.",
        epilog=(
            "Examples:\n"
            "  admin health snapshot\n"
            "  admin logs list\n"
            "  admin logs exceptions --limit 10\n"
            "  admin logs tail --source structured --limit 20\n"
            "  admin db query --sql 'select count(*) from content'"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--env-file", default=None, help="Override admin/.env path")
    parser.add_argument("--remote", default=None, help="Remote SSH target")
    parser.add_argument("--app-dir", default=None, help="Remote app checkout directory")
    parser.add_argument("--logs-dir", default=None, help="Primary remote logs directory")
    parser.add_argument("--service-log-dir", default=None, help="Remote service log directory")
    parser.add_argument("--remote-db-path", default=None, help="Remote SQLite database path")
    parser.add_argument("--remote-python", default=None, help="Remote Python executable path")
    parser.add_argument(
        "--remote-context-source",
        choices=("direct", "app-settings"),
        default=None,
        help=(
            "How remote read-only commands resolve DB/log paths. "
            "'direct' uses configured paths and avoids reading remote .env."
        ),
    )
    parser.add_argument("--local-logs-dir", default=None, help="Local synced log directory")
    parser.add_argument("--local-db-path", default=None, help="Local synced DB path")
    parser.add_argument(
        "--prompt-report-output-dir",
        default=None,
        help="Local output directory for prompt-report artifacts",
    )
    parser.add_argument(
        "--output",
        choices=("json", "text"),
        default="text",
        help="Output format. Use 'text' for terminal-friendly summaries or 'json' for automation.",
    )
    parser.add_argument(
        "--unsafe-raw",
        action="store_true",
        help="Disable redaction for read-only commands",
    )

    subparsers = parser.add_subparsers(dest="group", required=True)

    _build_db_parser(subparsers)
    _build_logs_parser(subparsers)
    _build_usage_parser(subparsers)
    _build_fix_parser(subparsers)
    _build_events_parser(subparsers)
    _build_health_parser(subparsers)
    _build_debug_parser(subparsers)

    return parser


def dispatch(args: argparse.Namespace, *, config: AdminConfig) -> CommandResult:
    """Route to the selected command handler."""
    if args.group == "db":
        return _handle_db(args, config=config)
    if args.group == "logs":
        return _handle_logs(args, config=config)
    if args.group == "usage":
        return _handle_usage(args, config=config)
    if args.group == "fix":
        return _handle_fix(args, config=config)
    if args.group == "events":
        return _handle_events(args, config=config)
    if args.group == "health":
        return _handle_health(config=config)
    if args.group == "debug":
        return _handle_debug(args, config=config)
    raise AdminCLIError(f"Unsupported command group: {args.group}")


def _handle_db(args: argparse.Namespace, *, config: AdminConfig) -> CommandResult:
    action_map = {
        "tables": "db.tables",
        "schema": "db.schema",
        "query": "db.query",
        "explain": "db.explain",
    }
    payload: dict[str, Any] = {"unsafe_raw": bool(args.unsafe_raw)}
    if args.db_command == "schema":
        payload["table_name"] = args.table_name
    if args.db_command in {"query", "explain"}:
        payload["sql"] = args.sql
    if args.db_command == "query":
        payload["limit"] = args.limit
    return CommandResult(
        data=_invoke_remote(action_map[args.db_command], config=config, payload=payload),
        warnings=[],
    )


def _handle_logs(args: argparse.Namespace, *, config: AdminConfig) -> CommandResult:
    if args.logs_command == "sync":
        destination = Path(args.destination or config.local_logs_dir)
        synced = _sync_logs(config, destination=destination)
        return CommandResult(data=synced, warnings=[])

    action_map = {
        "list": "logs.list",
        "exceptions": "logs.exceptions",
        "tail": "logs.tail",
        "range": "logs.range",
        "search": "logs.search",
    }
    payload: dict[str, Any] = {"unsafe_raw": bool(args.unsafe_raw)}
    if args.logs_command in {"tail", "range", "search"}:
        payload["source"] = args.source
    if args.logs_command in {"exceptions", "tail", "range", "search"}:
        payload["limit"] = args.limit
    if args.logs_command in {"exceptions", "range", "search"}:
        payload["since"] = args.since
        payload["until"] = args.until
    if args.logs_command == "exceptions":
        payload["component"] = args.component
        payload["operation"] = args.operation
    if args.logs_command == "search":
        payload["query"] = args.query
        payload["filters"] = {
            "component": args.component,
            "operation": args.operation,
            "content_id": args.content_id,
            "task_id": args.task_id,
            "user_id": args.user_id,
        }
    return CommandResult(
        data=_invoke_remote(action_map[args.logs_command], config=config, payload=payload),
        warnings=[],
    )


def _handle_usage(args: argparse.Namespace, *, config: AdminConfig) -> CommandResult:
    payload: dict[str, Any] = {"unsafe_raw": bool(args.unsafe_raw)}
    if args.usage_command == "summary":
        payload["since"] = args.since
        payload["until"] = args.until
        payload["group_by"] = args.group_by
        action = "usage.summary"
    elif args.usage_command == "user":
        payload["user_id"] = args.user_id
        payload["since"] = args.since
        payload["until"] = args.until
        payload["limit"] = args.limit
        action = "usage.user"
    elif args.usage_command == "content":
        payload["content_id"] = args.content_id
        payload["limit"] = args.limit
        action = "usage.content"
    else:
        raise AdminCLIError(f"Unsupported usage command: {args.usage_command}")

    return CommandResult(data=_invoke_remote(action, config=config, payload=payload), warnings=[])


def _handle_fix(args: argparse.Namespace, *, config: AdminConfig) -> CommandResult:
    if args.apply and not args.yes:
        raise AdminCLIError("Applying a fix requires both --apply and --yes")
    if args.yes and not args.apply:
        raise AdminCLIError("--yes is only valid together with --apply")

    if args.fix_command == "reset-content" and not args.apply:
        payload = {
            "cancel_only": bool(args.cancel_only),
            "hours": args.hours,
            "content_type": args.content_type,
        }
        return CommandResult(
            data=_invoke_remote("fix.preview-reset-content", config=config, payload=payload),
            warnings=["Preview only; add --apply --yes to execute the reset."],
        )

    if args.fix_command == "run-scraper" and not args.apply:
        return CommandResult(
            data={
                "preview": True,
                "remote": config.remote,
                "command": _build_fix_script_args(args),
            },
            warnings=["Preview only; add --apply --yes to run the scraper remotely."],
        )

    script_args = _build_fix_script_args(args)
    data = run_remote_script(config, script_args)
    warnings = [] if args.apply else ["Preview mode uses the underlying script dry-run output."]
    return CommandResult(data=data, warnings=warnings)


def _handle_events(args: argparse.Namespace, *, config: AdminConfig) -> CommandResult:
    payload = {
        "event_type": args.event_type,
        "event_name": args.event_name,
        "status": args.status,
        "since": args.since,
        "until": args.until,
        "limit": args.limit,
        "unsafe_raw": bool(args.unsafe_raw),
    }
    return CommandResult(
        data=_invoke_remote("events.list", config=config, payload=payload),
        warnings=[],
    )


def _handle_health(*, config: AdminConfig) -> CommandResult:
    return CommandResult(
        data=_invoke_remote("health.snapshot", config=config, payload={}),
        warnings=[],
    )


def _handle_debug(args: argparse.Namespace, *, config: AdminConfig) -> CommandResult:
    if args.debug_command != "prompt-report":
        raise AdminCLIError(f"Unsupported debug command: {args.debug_command}")

    logs_dir = Path(args.local_logs_dir or config.local_logs_dir)
    db_path = Path(args.local_db_path or config.local_db_path)
    output_dir = Path(args.output_dir or config.prompt_report_output_dir)
    components = tuple(
        args.components or ("summarization", "llm_summarization", "content_analyzer")
    )

    if not args.skip_sync_logs:
        _sync_logs(config, destination=logs_dir)
    if not args.skip_sync_db:
        _sync_database(config, destination=db_path)

    from app.services.prompt_debug_report import (
        PromptReportOptions,
        SyncOptions,
        build_prompt_debug_report,
        write_report_files,
    )

    options = PromptReportOptions(
        logs_dir=logs_dir,
        db_url=f"sqlite:///{db_path}",
        hours=args.hours,
        since=_parse_datetime_arg(args.since, end_of_day=False),
        until=_parse_datetime_arg(args.until, end_of_day=True),
        limit=args.limit,
        components=components,
        include_json=bool(args.include_json),
        output_dir=output_dir,
        sync=SyncOptions(enabled=False),
    )
    report = build_prompt_debug_report(options)
    markdown_path, json_path = write_report_files(report, options)
    return CommandResult(
        data={
            "markdown_path": str(markdown_path),
            "json_path": str(json_path) if json_path is not None else None,
            "total_failures": report.total_failures,
            "total_records_scanned": report.total_records_scanned,
            "logs_dir": str(logs_dir),
            "db_path": str(db_path),
        },
        warnings=[],
    )


def _invoke_remote(action: str, *, config: AdminConfig, payload: dict[str, Any]) -> Any:
    response = run_remote_module(config, action=action, payload=payload)
    if not response.get("ok"):
        error = response.get("error") or {}
        raise AdminCLIError(str(error.get("message", "Remote action failed")), details=error)
    return response.get("data")


def _sync_logs(config: AdminConfig, *, destination: Path) -> dict[str, Any]:
    destination.mkdir(parents=True, exist_ok=True)
    synced: dict[str, Any] = {"destination": str(destination), "paths": []}
    synced["paths"].append(
        rsync_from_remote(
            config,
            remote_path=f"{config.logs_dir.rstrip('/')}/",
            local_path=destination,
        )
    )
    if _remote_path_exists(config, config.service_log_dir):
        service_destination = destination / "service_logs"
        service_destination.mkdir(parents=True, exist_ok=True)
        synced["paths"].append(
            rsync_from_remote(
                config,
                remote_path=f"{config.service_log_dir.rstrip('/')}/",
                local_path=service_destination,
            )
        )
    return synced


def _sync_database(config: AdminConfig, *, destination: Path) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    remote_backup_path = "/tmp/news_app_admin_backup.db"
    backup_command = (
        f"sqlite3 {config.remote_db_path!r} '.backup {remote_backup_path}'"
    )
    cleanup_command = f"rm -f {remote_backup_path!r}"
    subprocess.run(["ssh", config.remote, backup_command], text=True, check=True)
    try:
        subprocess.run(
            ["rsync", "-avz", f"{config.remote}:{remote_backup_path}", str(destination)],
            text=True,
            check=True,
        )
    finally:
        subprocess.run(["ssh", config.remote, cleanup_command], text=True, check=False)
    return {"destination": str(destination)}


def _remote_path_exists(config: AdminConfig, remote_path: str) -> bool:
    completed = subprocess.run(
        ["ssh", config.remote, f"test -e {remote_path!r}"],
        text=True,
        capture_output=True,
        check=False,
    )
    return completed.returncode == 0


def _build_fix_script_args(args: argparse.Namespace) -> list[str]:
    if args.fix_command == "requeue-stale":
        script_args = [
            "scripts/queue_control.py",
            "requeue-stale",
            "--hours",
            str(args.hours),
        ]
        if args.queue:
            script_args.extend(["--queue", args.queue])
        if args.task_type:
            script_args.extend(["--task-type", args.task_type])
        script_args.append("--yes" if args.apply else "--dry-run")
        return script_args

    if args.fix_command == "move-transcribe":
        script_args = ["scripts/queue_control.py", "move-transcribe"]
        for status in args.statuses or []:
            script_args.extend(["--status", status])
        script_args.append("--yes" if args.apply else "--dry-run")
        return script_args

    if args.fix_command == "move-queue":
        script_args = [
            "scripts/queue_control.py",
            "move-queue",
            "--from-queue",
            args.from_queue,
            "--to-queue",
            args.to_queue,
        ]
        for status in args.statuses or []:
            script_args.extend(["--status", status])
        if args.task_type:
            script_args.extend(["--task-type", args.task_type])
        script_args.append("--yes" if args.apply else "--dry-run")
        return script_args

    if args.fix_command == "reset-content":
        script_args = ["scripts/reset_content_processing.py"]
        if args.cancel_only:
            script_args.append("--cancel-only")
        if args.hours is not None:
            script_args.extend(["--hours", str(args.hours)])
        if args.content_type:
            script_args.extend(["--content-type", args.content_type])
        return script_args

    if args.fix_command == "run-scraper":
        script_args = ["scripts/run_scrapers.py", "--scrapers", *args.scrapers]
        if args.show_stats:
            script_args.append("--show-stats")
        if args.debug_mode:
            script_args.append("--debug")
        return script_args

    raise AdminCLIError(f"Unsupported fix command: {args.fix_command}")


def _command_name(args: argparse.Namespace) -> str:
    parts = [args.group]
    for attr in (
        "db_command",
        "logs_command",
        "usage_command",
        "fix_command",
        "events_command",
        "health_command",
        "debug_command",
    ):
        value = getattr(args, attr, None)
        if value:
            parts.append(value)
    return ".".join(parts)


def _parse_datetime_arg(raw: str | None, *, end_of_day: bool) -> datetime | None:
    if raw in (None, ""):
        return None
    text = str(raw).strip()
    if "T" not in text:
        day = datetime.fromisoformat(text).date()
        return datetime.combine(day, time.max if end_of_day else time.min).replace(tzinfo=UTC)
    normalized = text.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _build_db_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    db_parser = subparsers.add_parser(
        "db",
        help="Read-only production DB access",
        description="Inspect the production database without mutating it.",
    )
    db_subparsers = db_parser.add_subparsers(dest="db_command", required=True)

    db_subparsers.add_parser("tables", help="List database tables")

    schema_parser = db_subparsers.add_parser("schema", help="Inspect schema")
    schema_parser.add_argument("table_name", nargs="?", default=None)

    query_parser = db_subparsers.add_parser("query", help="Execute a read-only SQL query")
    query_parser.add_argument("--sql", required=True)
    query_parser.add_argument("--limit", type=int, default=200)

    explain_parser = db_subparsers.add_parser("explain", help="Explain a query plan")
    explain_parser.add_argument("--sql", required=True)


def _build_logs_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    logs_parser = subparsers.add_parser(
        "logs",
        help="Inspect production logs",
        description=(
            "List, tail, search, or sync production logs.\n\n"
            "Sources are either:\n"
            "  structured, errors\n"
            "  or a service log stem such as server, worker, scraper\n\n"
            "Run `admin logs list` first to see the sources available on the remote host."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    logs_subparsers = logs_parser.add_subparsers(dest="logs_command", required=True)

    logs_subparsers.add_parser("list", help="List log files by source")

    exceptions_parser = logs_subparsers.add_parser(
        "exceptions",
        help="Show the most recent exception/error records",
    )
    exceptions_parser.add_argument("--component", default=None)
    exceptions_parser.add_argument("--operation", default=None)
    exceptions_parser.add_argument("--since", default=None)
    exceptions_parser.add_argument("--until", default=None)
    exceptions_parser.add_argument("--limit", type=int, default=20)

    tail_parser = logs_subparsers.add_parser("tail", help="Tail one log source")
    tail_parser.add_argument(
        "--source",
        required=True,
        help="Log source to read, for example structured, errors, server, worker, or scraper.",
    )
    tail_parser.add_argument("--limit", type=int, default=50)

    range_parser = logs_subparsers.add_parser("range", help="Query logs by time range")
    range_parser.add_argument(
        "--source",
        required=True,
        help="Log source to read. Discover valid values with `admin logs list`.",
    )
    range_parser.add_argument("--since", default=None)
    range_parser.add_argument("--until", default=None)
    range_parser.add_argument("--limit", type=int, default=100)

    search_parser = logs_subparsers.add_parser("search", help="Search logs")
    search_parser.add_argument(
        "--source",
        required=True,
        help="Log source to search. Discover valid values with `admin logs list`.",
    )
    search_parser.add_argument("--query", default=None)
    search_parser.add_argument("--component", default=None)
    search_parser.add_argument("--operation", default=None)
    search_parser.add_argument("--content-id", type=int, default=None)
    search_parser.add_argument("--task-id", type=int, default=None)
    search_parser.add_argument("--user-id", type=int, default=None)
    search_parser.add_argument("--since", default=None)
    search_parser.add_argument("--until", default=None)
    search_parser.add_argument("--limit", type=int, default=100)

    sync_parser = logs_subparsers.add_parser("sync", help="Sync logs locally")
    sync_parser.add_argument("--destination", default=None)


def _build_usage_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    usage_parser = subparsers.add_parser(
        "usage",
        help="Query persisted LLM usage",
        description="Summarize or inspect persisted LLM usage rows.",
    )
    usage_subparsers = usage_parser.add_subparsers(dest="usage_command", required=True)

    summary_parser = usage_subparsers.add_parser("summary", help="Summarize usage totals")
    summary_parser.add_argument("--since", default=None)
    summary_parser.add_argument("--until", default=None)
    summary_parser.add_argument(
        "--group-by",
        choices=("user", "feature", "operation", "provider", "model", "source"),
        default="feature",
    )

    user_parser = usage_subparsers.add_parser("user", help="Detailed usage for one user")
    user_parser.add_argument("--user-id", required=True, type=int)
    user_parser.add_argument("--since", default=None)
    user_parser.add_argument("--until", default=None)
    user_parser.add_argument("--limit", type=int, default=200)

    content_parser = usage_subparsers.add_parser(
        "content",
        help="Detailed usage for one content item",
    )
    content_parser.add_argument("--content-id", required=True, type=int)
    content_parser.add_argument("--limit", type=int, default=200)


def _build_fix_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    fix_parser = subparsers.add_parser(
        "fix",
        help="Allowlisted production fixes",
        description="Preview or apply a small set of allowlisted repair operations.",
    )
    fix_parser.add_argument("--apply", action="store_true", help="Apply the requested fix")
    fix_parser.add_argument("--yes", action="store_true", help="Acknowledge mutation intent")
    fix_subparsers = fix_parser.add_subparsers(dest="fix_command", required=True)

    stale_parser = fix_subparsers.add_parser("requeue-stale", help="Requeue stale tasks")
    stale_parser.add_argument("--hours", type=float, default=2.0)
    stale_parser.add_argument("--queue", default=None)
    stale_parser.add_argument("--task-type", default=None)

    move_transcribe_parser = fix_subparsers.add_parser(
        "move-transcribe",
        help="Move transcribe tasks",
    )
    move_transcribe_parser.add_argument("--status", dest="statuses", action="append")

    move_queue_parser = fix_subparsers.add_parser("move-queue", help="Move tasks between queues")
    move_queue_parser.add_argument("--from-queue", required=True)
    move_queue_parser.add_argument("--to-queue", required=True)
    move_queue_parser.add_argument("--status", dest="statuses", action="append")
    move_queue_parser.add_argument("--task-type", default=None)

    reset_parser = fix_subparsers.add_parser("reset-content", help="Reset content processing")
    reset_parser.add_argument("--cancel-only", action="store_true", dest="cancel_only")
    reset_parser.add_argument("--hours", type=float, default=None)
    reset_parser.add_argument("--content-type", default=None)

    run_scraper_parser = fix_subparsers.add_parser("run-scraper", help="Run selected scrapers")
    run_scraper_parser.add_argument("--scraper", dest="scrapers", action="append", required=True)
    run_scraper_parser.add_argument("--show-stats", action="store_true")
    run_scraper_parser.add_argument("--debug", action="store_true", dest="debug_mode")


def _build_events_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    events_parser = subparsers.add_parser(
        "events",
        help="Query event logs",
        description="Inspect rows from the event_log table with optional filters.",
    )
    events_subparsers = events_parser.add_subparsers(dest="events_command", required=True)
    list_parser = events_subparsers.add_parser("list", help="List event-log rows")
    list_parser.add_argument("--event-type", default=None)
    list_parser.add_argument("--event-name", default=None)
    list_parser.add_argument("--status", default=None)
    list_parser.add_argument("--since", default=None)
    list_parser.add_argument("--until", default=None)
    list_parser.add_argument("--limit", type=int, default=100)


def _build_health_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    health_parser = subparsers.add_parser(
        "health",
        help="Coarse operational health snapshot",
        description="Summarize content, task, event, and usage counts.",
    )
    health_subparsers = health_parser.add_subparsers(dest="health_command", required=True)
    health_subparsers.add_parser("snapshot", help="Snapshot current operational counts")


def _build_debug_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    debug_parser = subparsers.add_parser(
        "debug",
        help="Local debug artifacts from production data",
        description="Pull production data locally and build debug artifacts.",
    )
    debug_subparsers = debug_parser.add_subparsers(dest="debug_command", required=True)
    prompt_parser = debug_subparsers.add_parser("prompt-report", help="Build prompt debug report")
    prompt_parser.add_argument("--skip-sync-logs", action="store_true")
    prompt_parser.add_argument("--skip-sync-db", action="store_true")
    prompt_parser.add_argument("--local-logs-dir", default=None)
    prompt_parser.add_argument("--local-db-path", default=None)
    prompt_parser.add_argument("--since", default=None)
    prompt_parser.add_argument("--until", default=None)
    prompt_parser.add_argument("--hours", type=int, default=24)
    prompt_parser.add_argument("--limit", type=int, default=200)
    prompt_parser.add_argument(
        "--component",
        dest="components",
        action="append",
        default=None,
    )
    prompt_parser.add_argument("--include-json", action="store_true")
    prompt_parser.add_argument("--output-dir", default=None)


def _build_parser_hint(prog: str, message: str) -> str | None:
    normalized = message.lower()
    if "the following arguments are required: logs_command" in normalized:
        return (
            "Pick a logs subcommand:\n"
            "  admin logs list\n"
            "  admin logs exceptions --limit 10\n"
            "  admin logs tail --source structured --limit 20\n"
            "  admin logs search --source errors --query timeout"
        )
    if (
        "the following arguments are required: --source" in normalized
        and prog.endswith("logs tail")
    ):
        return (
            "Choose one log source with `--source`.\n"
            "Run `admin logs list` to discover valid values, then retry with one of:\n"
            "  admin logs tail --source structured --limit 20\n"
            "  admin logs tail --source errors --limit 20\n"
            "  admin logs tail --source server --limit 50"
        )
    if (
        "the following arguments are required: --source" in normalized
        and prog.endswith("logs range")
    ):
        return (
            "Choose one log source with `--source`, for example:\n"
            "  admin logs range --source structured --since 2026-03-29T00:00:00Z"
        )
    if (
        "the following arguments are required: --source" in normalized
        and prog.endswith("logs search")
    ):
        return (
            "Choose one log source with `--source`, for example:\n"
            "  admin logs search --source errors --query permission"
        )
    if "the following arguments are required: group" in normalized:
        return (
            "Pick a top-level command group:\n"
            "  admin health snapshot\n"
            "  admin logs list\n"
            "  admin db tables"
        )
    return None
