"""Remote execution entrypoint for the operator CLI."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from admin.remote_ops import (
    RemoteContext,
    db_explain,
    db_query,
    db_schema,
    db_tables,
    events_list,
    health_snapshot,
    logs_exceptions,
    logs_list,
    logs_range,
    logs_search,
    logs_tail,
    preview_reset_content,
    usage_by_content,
    usage_by_user,
    usage_summary,
)
from app.core.settings import get_settings


def main(argv: list[str] | None = None) -> int:
    """Execute one remote admin action and print JSON."""
    try:
        args = argv or sys.argv[1:]
        if not args:
            _print_payload({"ok": False, "error": {"message": "Remote action is required"}})
            return 1

        request = _load_stdin_payload()
        payload = _extract_payload(request)
        context = _resolve_context(request)

        action = args[0]
        result = _dispatch(action, context=context, payload=payload)
        _print_payload({"ok": True, "data": result})
        return 0
    except Exception as exc:  # noqa: BLE001
        _print_payload({"ok": False, "error": {"message": str(exc)}})
        return 1


def _dispatch(action: str, *, context: RemoteContext, payload: dict[str, Any]) -> dict[str, Any]:
    if action == "db.tables":
        return db_tables(context)
    if action == "db.schema":
        return db_schema(context, table_name=payload.get("table_name"))
    if action == "db.query":
        return db_query(
            context,
            sql=str(payload["sql"]),
            limit=int(payload.get("limit", 200)),
            unsafe_raw=bool(payload.get("unsafe_raw")),
        )
    if action == "db.explain":
        return db_explain(context, sql=str(payload["sql"]))
    if action == "logs.list":
        return logs_list(context)
    if action == "logs.tail":
        return logs_tail(
            context,
            source=str(payload["source"]),
            limit=int(payload.get("limit", 50)),
            unsafe_raw=bool(payload.get("unsafe_raw")),
        )
    if action == "logs.exceptions":
        return logs_exceptions(
            context,
            since=_parse_datetime(payload.get("since")),
            until=_parse_datetime(payload.get("until")),
            component=payload.get("component"),
            operation=payload.get("operation"),
            limit=int(payload.get("limit", 20)),
            unsafe_raw=bool(payload.get("unsafe_raw")),
        )
    if action == "logs.range":
        return logs_range(
            context,
            source=str(payload["source"]),
            since=_parse_datetime(payload.get("since")),
            until=_parse_datetime(payload.get("until")),
            limit=int(payload.get("limit", 100)),
            unsafe_raw=bool(payload.get("unsafe_raw")),
        )
    if action == "logs.search":
        return logs_search(
            context,
            source=str(payload["source"]),
            query=payload.get("query"),
            filters=dict(payload.get("filters") or {}),
            since=_parse_datetime(payload.get("since")),
            until=_parse_datetime(payload.get("until")),
            limit=int(payload.get("limit", 100)),
            unsafe_raw=bool(payload.get("unsafe_raw")),
        )
    if action == "usage.summary":
        return usage_summary(
            context,
            since=_parse_datetime(payload.get("since")),
            until=_parse_datetime(payload.get("until")),
            group_by=str(payload.get("group_by", "feature")),
        )
    if action == "usage.user":
        return usage_by_user(
            context,
            user_id=int(payload["user_id"]),
            since=_parse_datetime(payload.get("since")),
            until=_parse_datetime(payload.get("until")),
            limit=int(payload.get("limit", 200)),
            unsafe_raw=bool(payload.get("unsafe_raw")),
        )
    if action == "usage.content":
        return usage_by_content(
            context,
            content_id=int(payload["content_id"]),
            limit=int(payload.get("limit", 200)),
            unsafe_raw=bool(payload.get("unsafe_raw")),
        )
    if action == "events.list":
        return events_list(
            context,
            event_type=payload.get("event_type"),
            event_name=payload.get("event_name"),
            status=payload.get("status"),
            since=_parse_datetime(payload.get("since")),
            until=_parse_datetime(payload.get("until")),
            limit=int(payload.get("limit", 100)),
            unsafe_raw=bool(payload.get("unsafe_raw")),
        )
    if action == "health.snapshot":
        return health_snapshot(context)
    if action == "fix.preview-reset-content":
        return preview_reset_content(
            context,
            cancel_only=bool(payload.get("cancel_only")),
            hours=float(payload["hours"]) if payload.get("hours") is not None else None,
            content_type=payload.get("content_type"),
        )
    raise ValueError(f"Unsupported remote action: {action}")


def _load_stdin_payload() -> dict[str, Any]:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    return dict(json.loads(raw))


def _extract_payload(request: dict[str, Any]) -> dict[str, Any]:
    payload = request.get("payload")
    if isinstance(payload, dict):
        return payload
    return request


def _resolve_context(request: dict[str, Any]) -> RemoteContext:
    context_override = request.get("context_override")
    if isinstance(context_override, dict):
        return RemoteContext(
            database_url=str(context_override["database_url"]),
            logs_dir=Path(str(context_override["logs_dir"])),
            service_log_dir=Path(str(context_override["service_log_dir"])),
        )

    settings = get_settings()
    return RemoteContext(
        database_url=str(settings.database_url),
        logs_dir=Path(settings.logs_dir),
        service_log_dir=Path("/var/log/news_app"),
    )


def _parse_datetime(raw: Any) -> datetime | None:
    if raw in (None, ""):
        return None
    text = str(raw).strip()
    normalized = text.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _print_payload(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, default=str))


if __name__ == "__main__":
    raise SystemExit(main())
