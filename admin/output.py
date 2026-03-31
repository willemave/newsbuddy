"""Output helpers for the operator CLI."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TextIO


@dataclass(frozen=True)
class EnvelopeError:
    """Serializable error payload."""

    message: str
    details: dict[str, Any] | None = None


@dataclass(frozen=True)
class Envelope:
    """Stable CLI envelope."""

    ok: bool
    command: str
    data: Any = None
    warnings: list[str] = field(default_factory=list)
    error: EnvelopeError | None = None


def emit(envelope: Envelope, output_format: str, stream: TextIO | None = None) -> None:
    """Write an envelope in the requested format."""
    target = stream or sys.stdout
    if output_format == "text":
        rendered = _format_text(envelope)
        target.write(rendered)
        if not rendered.endswith("\n"):
            target.write("\n")
        return

    payload = {
        "ok": envelope.ok,
        "command": envelope.command,
        "data": envelope.data,
    }
    if envelope.warnings:
        payload["warnings"] = envelope.warnings
    if envelope.error is not None:
        payload["error"] = {
            "message": envelope.error.message,
            "details": envelope.error.details,
        }
    json.dump(payload, target, ensure_ascii=False, indent=2, default=str)
    target.write("\n")


def _format_text(envelope: Envelope) -> str:
    if envelope.ok:
        body = _format_success_text(envelope.command, envelope.data)
        if envelope.warnings:
            warning_block = "\n".join(f"warning: {warning}" for warning in envelope.warnings)
            return f"{body}\n{warning_block}"
        return body

    body = envelope.error.message if envelope.error is not None else "Unknown error"
    if envelope.error and envelope.error.details:
        details = _format_error_details(envelope.error.details)
        return f"error: {body}\n{details}"
    return f"error: {body}"


def _format_success_text(command: str, data: Any) -> str:
    if isinstance(data, str):
        return data
    if not isinstance(data, dict):
        return json.dumps(data, ensure_ascii=False, indent=2, default=str)

    if command == "db.tables":
        tables = data.get("tables") or []
        if not tables:
            return "No tables found."
        return "Tables:\n" + "\n".join(f"- {table}" for table in tables)

    if command == "logs.list":
        return _format_logs_list(data)
    if command in {"logs.tail", "logs.range", "logs.search"}:
        return _format_log_records(data)
    if command == "logs.exceptions":
        return _format_exceptions(data)
    if command == "logs.sync":
        return _format_sync_result(data)
    if command == "health.snapshot":
        return _format_health_snapshot(data)
    if command == "usage.summary":
        return _format_usage_summary(data)
    if command in {"usage.user", "usage.content"}:
        return _format_usage_rows(data)
    if command == "events.list":
        return _format_events(data)

    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _format_error_details(details: dict[str, Any]) -> str:
    stderr = details.get("stderr")
    if isinstance(stderr, str) and "PermissionError" in stderr and "/opt/news_app/.env" in stderr:
        return (
            "The remote command reached the host, but the remote process could not read "
            "`/opt/news_app/.env`.\n"
            "Retry with `--remote-context-source direct` (or set "
            "`ADMIN_REMOTE_CONTEXT_SOURCE=direct`) to avoid reading remote app settings, or "
            "check the SSH user in `ADMIN_REMOTE`."
        )
    return json.dumps(details, ensure_ascii=False, indent=2, default=str)


def _format_logs_list(data: dict[str, Any]) -> str:
    sources = data.get("sources") or {}
    if not sources:
        return "No log sources found."

    sections = ["Available log sources:"]
    for source, files in sorted(sources.items()):
        sections.append(f"- {source} ({len(files)} file{'s' if len(files) != 1 else ''})")
    sections.append("")
    sections.append("Example:")
    sections.append("  admin logs tail --source structured --limit 20")
    return "\n".join(sections)


def _format_log_records(data: dict[str, Any]) -> str:
    records = data.get("records") or []
    if not records:
        return "No log records matched."

    lines = [f"Showing {len(records)} log record{'s' if len(records) != 1 else ''}:"]
    for record in records:
        lines.append(_format_log_record(record))
    return "\n".join(lines)


def _format_exceptions(data: dict[str, Any]) -> str:
    exceptions = data.get("exceptions") or []
    if not exceptions:
        return "No exception records matched."

    count = len(exceptions)
    label = "records" if count != 1 else "record"
    lines = [f"Showing {count} recent exception {label}:"]
    for record in exceptions:
        timestamp = _coerce_text(record.get("timestamp") or "unknown-time")
        component = _coerce_text(record.get("component") or "unknown-component")
        operation = _coerce_text(record.get("operation") or "unknown-operation")
        error_type = _coerce_text(record.get("error_type") or "LogError")
        error_message = _coerce_text(
            record.get("error_message") or record.get("message") or "Unknown error"
        ).strip()
        lines.append(
            f"- [{timestamp}] {component}/{operation} {error_type}: {error_message}"
        )
    return "\n".join(lines)


def _format_log_record(record: dict[str, Any]) -> str:
    timestamp = _coerce_text(
        record.get("timestamp")
        or record.get("created_at")
        or record.get("@timestamp")
        or "unknown-time"
    )
    source = _coerce_text(record.get("source") or "unknown-source")
    level = _coerce_text(record.get("level") or record.get("severity") or "INFO").upper()
    message = _coerce_text(
        record.get("message")
        or record.get("event")
        or record.get("error")
        or record.get("body")
        or record
    ).strip()
    return f"- [{timestamp}] {source} {level}: {message}"


def _format_sync_result(data: dict[str, Any]) -> str:
    destination = _coerce_text(data.get("destination") or "unknown")
    paths = data.get("paths") or []
    lines = [f"Synced logs to {destination}."]
    if paths:
        lines.append("Transfers:")
        for path in paths:
            target = _coerce_text(path.get("destination") or destination)
            lines.append(f"- {target}")
    return "\n".join(lines)


def _format_health_snapshot(data: dict[str, Any]) -> str:
    content = data.get("content") or {}
    tasks = data.get("tasks") or {}
    events = data.get("events") or {}
    usage = data.get("usage") or {}
    return "\n".join(
        [
            "Health snapshot:",
            f"- content: {content.get('total', 0)} total",
            f"- tasks: {tasks.get('total', 0)} total",
            f"- events: {events.get('total', 0)} total",
            f"- latest usage record: {_coerce_text(usage.get('latest_record_at') or 'none')}",
        ]
    )


def _format_usage_summary(data: dict[str, Any]) -> str:
    group_by = _coerce_text(data.get("group_by") or "feature")
    totals = data.get("totals") or {}
    groups = data.get("groups") or []
    lines = [
        f"Usage summary grouped by {group_by}:",
        _format_usage_totals(totals),
    ]
    if groups:
        lines.append("Groups:")
        for group in groups:
            lines.append(
                f"- {_coerce_text(group.get('key') or 'unknown')}: "
                f"{group.get('call_count', 0)} calls, "
                f"{group.get('total_tokens', 0)} tokens, "
                f"${float(group.get('cost_usd', 0.0)):.4f}"
            )
    return "\n".join(lines)


def _format_usage_rows(data: dict[str, Any]) -> str:
    header = data.get("user") or data.get("content") or {}
    rows = data.get("rows") or []
    lines = [_format_usage_subject(header), _format_usage_totals(data.get("totals") or {})]
    if rows:
        lines.append("Recent rows:")
        for row in rows[:10]:
            lines.append(
                f"- {_coerce_text(row.get('created_at') or 'unknown-time')}: "
                f"{_coerce_text(row.get('provider') or 'unknown')}/"
                f"{_coerce_text(row.get('model') or 'unknown')} "
                f"{row.get('total_tokens', 0)} tokens "
                f"${float(row.get('cost_usd', 0.0)):.4f}"
            )
    return "\n".join(lines)


def _format_usage_subject(subject: dict[str, Any]) -> str:
    if "email" in subject:
        email = _coerce_text(subject.get("email") or "unknown-email")
        return f"Usage for user {subject.get('id')}: {email}"
    if "url" in subject or "title" in subject:
        title = _coerce_text(subject.get("title") or subject.get("url") or "unknown-content")
        return f"Usage for content {subject.get('id')}: {title}"
    return "Usage details:"


def _format_usage_totals(totals: dict[str, Any]) -> str:
    return (
        f"Totals: {totals.get('call_count', 0)} calls, "
        f"{totals.get('total_tokens', 0)} tokens, "
        f"${float(totals.get('cost_usd', 0.0)):.4f}"
    )


def _format_events(data: dict[str, Any]) -> str:
    rows = data.get("rows") or []
    if not rows:
        return "No events matched."
    lines = [f"Showing {len(rows)} event{'s' if len(rows) != 1 else ''}:"]
    for row in rows:
        lines.append(
            f"- [{_coerce_text(row.get('created_at') or 'unknown-time')}] "
            f"{_coerce_text(row.get('event_type') or 'unknown')}/"
            f"{_coerce_text(row.get('event_name') or 'unknown')} "
            f"status={_coerce_text(row.get('status') or 'unknown')}"
        )
    return "\n".join(lines)


def _coerce_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return value
    return str(value)
