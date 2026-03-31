"""Pure remote operations for the operator CLI."""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, func, inspect, text
from sqlalchemy.orm import sessionmaker

from admin.log_parsing import (
    parse_jsonl_record,
    parse_record_timestamp,
    parse_service_log_line,
    record_matches_filters,
    record_matches_query,
)
from admin.sql_guard import validate_readonly_sql
from app.core.redaction import redact_value

DEFAULT_ROW_LIMIT = 200
MAX_ROW_LIMIT = 1000


@dataclass(frozen=True)
class RemoteContext:
    """Resolved context for remote read-only operations."""

    database_url: str
    logs_dir: Path
    service_log_dir: Path


@lru_cache(maxsize=1)
def _load_schema_models() -> tuple[Any, Any, Any, Any]:
    """Load schema models only for DB-backed commands."""
    from app.models.schema import Content, LlmUsageRecord, ProcessingTask

    try:
        from app.models.schema import EventLog
    except ImportError:  # pragma: no cover - legacy compatibility when EventLog is absent
        EventLog = None

    return Content, LlmUsageRecord, ProcessingTask, EventLog


@lru_cache(maxsize=1)
def _load_user_model() -> Any:
    """Load the user model only when needed."""
    from app.models.user import User

    return User


def db_tables(context: RemoteContext) -> dict[str, Any]:
    """List database tables."""
    engine = create_engine(context.database_url, pool_pre_ping=True)
    try:
        inspector = inspect(engine)
        return {"tables": sorted(inspector.get_table_names())}
    finally:
        engine.dispose()


def db_schema(context: RemoteContext, *, table_name: str | None = None) -> dict[str, Any]:
    """Inspect one table or the whole schema."""
    engine = create_engine(context.database_url, pool_pre_ping=True)
    try:
        inspector = inspect(engine)
        table_names = [table_name] if table_name else sorted(inspector.get_table_names())
        schemas: list[dict[str, Any]] = []
        for current_table in table_names:
            columns = []
            for column in inspector.get_columns(current_table):
                columns.append(
                    {
                        "name": column["name"],
                        "type": str(column["type"]),
                        "nullable": bool(column["nullable"]),
                        "default": column.get("default"),
                        "primary_key": bool(column.get("primary_key")),
                    }
                )
            schemas.append({"table": current_table, "columns": columns})
        return {"tables": schemas}
    finally:
        engine.dispose()


def db_query(
    context: RemoteContext,
    *,
    sql: str,
    limit: int = DEFAULT_ROW_LIMIT,
    unsafe_raw: bool = False,
) -> dict[str, Any]:
    """Execute a read-only SQL query."""
    normalized_sql = validate_readonly_sql(sql)
    bounded_limit = _bounded_limit(limit)
    rows, columns = _execute_sql(context.database_url, normalized_sql, limit=bounded_limit)
    rendered_rows = rows if unsafe_raw else [redact_value(row) for row in rows]
    return {
        "sql": normalized_sql,
        "limit": bounded_limit,
        "columns": columns,
        "row_count": len(rendered_rows),
        "rows": rendered_rows,
        "redacted": not unsafe_raw,
        "truncated": len(rendered_rows) >= bounded_limit,
    }


def db_explain(context: RemoteContext, *, sql: str) -> dict[str, Any]:
    """Run EXPLAIN QUERY PLAN for a read-only SQL query."""
    normalized_sql = validate_readonly_sql(sql)
    explain_sql = normalized_sql
    if not explain_sql.lower().startswith("explain query plan"):
        explain_sql = f"EXPLAIN QUERY PLAN {explain_sql}"
    rows, columns = _execute_sql(context.database_url, explain_sql, limit=MAX_ROW_LIMIT)
    return {
        "sql": explain_sql,
        "columns": columns,
        "rows": rows,
    }


def usage_summary(
    context: RemoteContext,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    group_by: str = "feature",
) -> dict[str, Any]:
    """Return grouped usage totals from persisted LLM usage rows."""
    _, usage_record_model, _, _ = _load_schema_models()
    engine = create_engine(context.database_url, pool_pre_ping=True)
    session_factory = sessionmaker(bind=engine)
    try:
        with session_factory() as session:
            rows = _apply_usage_window(
                session.query(usage_record_model),
                usage_record_model=usage_record_model,
                since=since,
                until=until,
            ).all()
            grouped: dict[str, dict[str, Any]] = defaultdict(lambda: _usage_totals())
            for row in rows:
                key = _usage_group_key(row, group_by=group_by)
                bucket = grouped[key]
                _accumulate_usage(bucket, row)
            return {
                "group_by": group_by,
                "totals": _summarize_usage_rows(rows),
                "groups": [
                    {"key": key, **grouped[key]}
                    for key in sorted(grouped.keys(), key=lambda item: (item == "unknown", item))
                ],
            }
    finally:
        engine.dispose()


def usage_by_user(
    context: RemoteContext,
    *,
    user_id: int,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 200,
    unsafe_raw: bool = False,
) -> dict[str, Any]:
    """Return detailed usage rows and totals for one user."""
    bounded_limit = _bounded_limit(limit)
    _, usage_record_model, _, _ = _load_schema_models()
    user_model = _load_user_model()
    engine = create_engine(context.database_url, pool_pre_ping=True)
    session_factory = sessionmaker(bind=engine)
    try:
        with session_factory() as session:
            user = session.query(user_model).filter(user_model.id == user_id).first()
            query = _apply_usage_window(
                session.query(usage_record_model).filter(usage_record_model.user_id == user_id),
                usage_record_model=usage_record_model,
                since=since,
                until=until,
            )
            rows = query.order_by(usage_record_model.created_at.desc()).limit(bounded_limit).all()
            serialized = [_serialize_usage_row(row, unsafe_raw=unsafe_raw) for row in rows]
            return {
                "user": {
                    "id": user_id,
                    "email": getattr(user, "email", None),
                },
                "limit": bounded_limit,
                "totals": _summarize_usage_rows(rows),
                "rows": serialized,
                "redacted": not unsafe_raw,
            }
    finally:
        engine.dispose()


def usage_by_content(
    context: RemoteContext,
    *,
    content_id: int,
    limit: int = 200,
    unsafe_raw: bool = False,
) -> dict[str, Any]:
    """Return detailed usage rows for one content item."""
    bounded_limit = _bounded_limit(limit)
    content_model, usage_record_model, _, _ = _load_schema_models()
    engine = create_engine(context.database_url, pool_pre_ping=True)
    session_factory = sessionmaker(bind=engine)
    try:
        with session_factory() as session:
            content = session.query(content_model).filter(content_model.id == content_id).first()
            rows = (
                session.query(usage_record_model)
                .filter(usage_record_model.content_id == content_id)
                .order_by(usage_record_model.created_at.desc())
                .limit(bounded_limit)
                .all()
            )
            return {
                "content": {
                    "id": content_id,
                    "url": getattr(content, "url", None),
                    "title": getattr(content, "title", None),
                },
                "limit": bounded_limit,
                "totals": _summarize_usage_rows(rows),
                "rows": [_serialize_usage_row(row, unsafe_raw=unsafe_raw) for row in rows],
                "redacted": not unsafe_raw,
            }
    finally:
        engine.dispose()


def events_list(
    context: RemoteContext,
    *,
    event_type: str | None = None,
    event_name: str | None = None,
    status: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 100,
    unsafe_raw: bool = False,
) -> dict[str, Any]:
    """Return recent event-log rows with optional filters."""
    bounded_limit = _bounded_limit(limit)
    _, _, _, event_log_model = _load_schema_models()
    if event_log_model is None:
        return {"limit": bounded_limit, "rows": [], "redacted": not unsafe_raw}

    engine = create_engine(context.database_url, pool_pre_ping=True)
    session_factory = sessionmaker(bind=engine)
    try:
        with session_factory() as session:
            query = session.query(event_log_model)
            if event_type:
                query = query.filter(event_log_model.event_type == event_type)
            if event_name:
                query = query.filter(event_log_model.event_name == event_name)
            if status:
                query = query.filter(event_log_model.status == status)
            if since is not None:
                query = query.filter(event_log_model.created_at >= _naive_utc(since))
            if until is not None:
                query = query.filter(event_log_model.created_at <= _naive_utc(until))
            rows = query.order_by(event_log_model.created_at.desc()).limit(bounded_limit).all()
            serialized = []
            for row in rows:
                data = row.data if unsafe_raw else redact_value(row.data)
                serialized.append(
                    {
                        "id": row.id,
                        "event_type": row.event_type,
                        "event_name": row.event_name,
                        "status": row.status,
                        "created_at": row.created_at,
                        "data": data,
                    }
                )
            return {"limit": bounded_limit, "rows": serialized, "redacted": not unsafe_raw}
    finally:
        engine.dispose()


def health_snapshot(context: RemoteContext) -> dict[str, Any]:
    """Return a coarse operational snapshot."""
    (
        content_model,
        usage_record_model,
        processing_task_model,
        event_log_model,
    ) = _load_schema_models()
    engine = create_engine(context.database_url, pool_pre_ping=True)
    session_factory = sessionmaker(bind=engine)
    try:
        with session_factory() as session:
            content_total = int(session.query(func.count(content_model.id)).scalar() or 0)
            task_total = int(session.query(func.count(processing_task_model.id)).scalar() or 0)
            event_total = (
                int(session.query(func.count(event_log_model.id)).scalar() or 0)
                if event_log_model is not None
                else 0
            )

            content_by_status = dict(
                session.query(content_model.status, func.count(content_model.id))
                .group_by(content_model.status)
                .all()
            )
            task_by_status = dict(
                session.query(processing_task_model.status, func.count(processing_task_model.id))
                .group_by(processing_task_model.status)
                .all()
            )
            latest_usage_at = session.query(func.max(usage_record_model.created_at)).scalar()

            return {
                "content": {
                    "total": content_total,
                    "by_status": {str(key): int(value) for key, value in content_by_status.items()},
                },
                "tasks": {
                    "total": task_total,
                    "by_status": {str(key): int(value) for key, value in task_by_status.items()},
                },
                "events": {"total": event_total},
                "usage": {"latest_record_at": latest_usage_at},
            }
    finally:
        engine.dispose()


def preview_reset_content(
    context: RemoteContext,
    *,
    cancel_only: bool,
    hours: float | None,
    content_type: str | None,
) -> dict[str, Any]:
    """Preview the effect of `scripts/reset_content_processing.py`."""
    content_model, _, processing_task_model, _ = _load_schema_models()
    engine = create_engine(context.database_url, pool_pre_ping=True)
    session_factory = sessionmaker(bind=engine)
    try:
        with session_factory() as session:
            content_query = session.query(content_model)
            if content_type:
                content_query = content_query.filter(content_model.content_type == content_type)
            if hours is not None:
                cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=hours)
                content_query = content_query.filter(
                    func.coalesce(
                        content_model.processed_at,
                        content_model.updated_at,
                        content_model.created_at,
                    )
                    >= cutoff
                )
            content_rows = content_query.all()
            content_ids = (
                [row.id for row in content_rows]
                if (hours is not None or content_type)
                else None
            )

            task_query = session.query(processing_task_model)
            if content_ids is not None:
                if content_ids:
                    task_query = task_query.filter(
                        processing_task_model.content_id.in_(content_ids)
                    )
                else:
                    task_query = task_query.filter(text("1 = 0"))

            deleted_tasks = int(task_query.count())
            if cancel_only:
                reset_contents = 0
            elif content_ids is not None:
                reset_contents = len(content_rows)
            else:
                reset_contents = int(session.query(func.count(content_model.id)).scalar() or 0)
            created_tasks = 0 if cancel_only else reset_contents
            return {
                "cancel_only": cancel_only,
                "hours": hours,
                "content_type": content_type,
                "matched_content_ids_sample": content_ids[:20] if content_ids else [],
                "deleted_tasks": deleted_tasks,
                "reset_contents": reset_contents,
                "created_tasks": created_tasks,
            }
    finally:
        engine.dispose()


def logs_list(context: RemoteContext) -> dict[str, Any]:
    """List available structured and service log files."""
    sources: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if context.logs_dir.exists():
        for directory_name in ("structured", "errors"):
            current_dir = context.logs_dir / directory_name
            if not current_dir.exists():
                continue
            for file_path in sorted(current_dir.glob("*.jsonl")):
                stat = file_path.stat()
                sources[directory_name].append(
                    {
                        "path": str(file_path),
                        "size_bytes": stat.st_size,
                        "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                    }
                )
    if context.service_log_dir.exists():
        for file_path in sorted(context.service_log_dir.glob("*.log")):
            stat = file_path.stat()
            sources[file_path.stem].append(
                {
                    "path": str(file_path),
                    "size_bytes": stat.st_size,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                }
            )
    return {"sources": dict(sources)}


def logs_tail(
    context: RemoteContext,
    *,
    source: str,
    limit: int = 50,
    unsafe_raw: bool = False,
) -> dict[str, Any]:
    """Return the most recent records for a source."""
    bounded_limit = _bounded_limit(limit)
    records = _collect_logs(context, source=source, limit=bounded_limit)
    ordered = sorted(
        records,
        key=lambda record: parse_record_timestamp(record) or datetime.min.replace(tzinfo=UTC),
    )
    tail_records = ordered[-bounded_limit:]
    return _render_logs(tail_records, unsafe_raw=unsafe_raw, limit=bounded_limit)


def logs_exceptions(
    context: RemoteContext,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    component: str | None = None,
    operation: str | None = None,
    limit: int = 20,
    unsafe_raw: bool = False,
) -> dict[str, Any]:
    """Return the most recent error records from the structured exception logs."""
    bounded_limit = _bounded_limit(limit)
    records = _collect_logs(context, source="errors", limit=None)
    filtered = _filter_log_records(records, since=since, until=until)
    ordered = sorted(
        filtered,
        key=lambda record: parse_record_timestamp(record) or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )

    matched: list[dict[str, Any]] = []
    for record in ordered:
        if component and str(record.get("component")) != str(component):
            continue
        if operation and str(record.get("operation")) != str(operation):
            continue
        matched.append(record)
        if len(matched) >= bounded_limit:
            break

    rendered = matched if unsafe_raw else [redact_value(record) for record in matched]
    return {
        "limit": bounded_limit,
        "available": len(ordered),
        "returned": len(rendered),
        "exceptions": rendered,
        "redacted": not unsafe_raw,
    }


def logs_range(
    context: RemoteContext,
    *,
    source: str,
    since: datetime | None,
    until: datetime | None,
    limit: int = 100,
    unsafe_raw: bool = False,
) -> dict[str, Any]:
    """Return records in a time range."""
    bounded_limit = _bounded_limit(limit)
    records = _collect_logs(context, source=source, limit=None)
    filtered = _filter_log_records(records, since=since, until=until)
    return _render_logs(filtered[:bounded_limit], unsafe_raw=unsafe_raw, limit=bounded_limit)


def logs_search(
    context: RemoteContext,
    *,
    source: str,
    query: str | None,
    filters: dict[str, Any],
    since: datetime | None,
    until: datetime | None,
    limit: int = 100,
    unsafe_raw: bool = False,
) -> dict[str, Any]:
    """Search logs by text and structured filters."""
    bounded_limit = _bounded_limit(limit)
    records = _collect_logs(context, source=source, limit=None)
    time_filtered = _filter_log_records(records, since=since, until=until)
    matched = []
    for record in time_filtered:
        if not record_matches_query(record, query):
            continue
        if not record_matches_filters(record, filters):
            continue
        matched.append(record)
        if len(matched) >= bounded_limit:
            break
    return _render_logs(matched, unsafe_raw=unsafe_raw, limit=bounded_limit)


def _execute_sql(
    database_url: str,
    sql: str,
    *,
    limit: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            if connection.engine.dialect.name == "sqlite":
                connection.exec_driver_sql("PRAGMA query_only = 1")
            else:
                connection.execute(text("SET TRANSACTION READ ONLY"))
            result = connection.execute(text(sql))
            rows = result.fetchmany(limit)
            columns = list(result.keys())
            serialized_rows = [dict(zip(columns, row, strict=False)) for row in rows]
            return serialized_rows, columns
    finally:
        engine.dispose()


def _collect_logs(
    context: RemoteContext,
    *,
    source: str,
    limit: int | None,
) -> list[dict[str, Any]]:
    if source in {"structured", "errors"}:
        directory = context.logs_dir / source
        records: list[dict[str, Any]] = []
        for file_path in sorted(directory.glob("*.jsonl")):
            with file_path.open(encoding="utf-8") as handle:
                for raw_line in handle:
                    parsed = parse_jsonl_record(raw_line, source=source, file_path=str(file_path))
                    if parsed is not None:
                        records.append(parsed)
        return records

    log_path = context.service_log_dir / f"{source}.log"
    if not log_path.exists():
        return []
    lines = deque(maxlen=limit or 10_000)
    with log_path.open(encoding="utf-8") as handle:
        for raw_line in handle:
            parsed = parse_service_log_line(raw_line, source=source, file_path=str(log_path))
            if parsed is not None:
                lines.append(parsed)
    return list(lines)


def _filter_log_records(
    records: list[dict[str, Any]],
    *,
    since: datetime | None,
    until: datetime | None,
) -> list[dict[str, Any]]:
    filtered = []
    for record in records:
        timestamp = parse_record_timestamp(record)
        if since is not None and timestamp is not None and timestamp < since:
            continue
        if until is not None and timestamp is not None and timestamp > until:
            continue
        filtered.append(record)
    return filtered


def _render_logs(records: list[dict[str, Any]], *, unsafe_raw: bool, limit: int) -> dict[str, Any]:
    rendered = records if unsafe_raw else [redact_value(record) for record in records]
    return {
        "limit": limit,
        "row_count": len(rendered),
        "records": rendered,
        "redacted": not unsafe_raw,
    }


def _apply_usage_window(
    query: Any,
    *,
    usage_record_model: Any,
    since: datetime | None,
    until: datetime | None,
) -> Any:
    if since is not None:
        query = query.filter(usage_record_model.created_at >= _naive_utc(since))
    if until is not None:
        query = query.filter(usage_record_model.created_at <= _naive_utc(until))
    return query


def _usage_group_key(row: Any, *, group_by: str) -> str:
    if group_by == "user":
        return str(row.user_id) if row.user_id is not None else "unknown"
    return str(getattr(row, group_by, None) or "unknown")


def _serialize_usage_row(row: Any, *, unsafe_raw: bool) -> dict[str, Any]:
    payload = {
        "id": row.id,
        "provider": row.provider,
        "model": row.model,
        "feature": row.feature,
        "operation": row.operation,
        "source": row.source,
        "request_id": row.request_id,
        "task_id": row.task_id,
        "content_id": row.content_id,
        "session_id": row.session_id,
        "message_id": row.message_id,
        "user_id": row.user_id,
        "input_tokens": row.input_tokens,
        "output_tokens": row.output_tokens,
        "total_tokens": row.total_tokens,
        "cost_usd": row.cost_usd,
        "currency": row.currency,
        "pricing_version": row.pricing_version,
        "metadata": row.metadata_json,
        "created_at": row.created_at,
    }
    return payload if unsafe_raw else redact_value(payload)


def _summarize_usage_rows(rows: list[Any]) -> dict[str, Any]:
    totals = _usage_totals()
    providers = Counter()
    models = Counter()
    for row in rows:
        _accumulate_usage(totals, row)
        providers[row.provider] += 1
        models[row.model] += 1
    totals["providers"] = dict(providers)
    totals["models"] = dict(models)
    return totals


def _usage_totals() -> dict[str, Any]:
    return {
        "call_count": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
    }


def _accumulate_usage(bucket: dict[str, Any], row: Any) -> None:
    bucket["call_count"] += 1
    bucket["input_tokens"] += int(row.input_tokens or 0)
    bucket["output_tokens"] += int(row.output_tokens or 0)
    bucket["total_tokens"] += int(row.total_tokens or 0)
    bucket["cost_usd"] += float(row.cost_usd or 0.0)
    bucket["cost_usd"] = round(bucket["cost_usd"], 8)


def _bounded_limit(limit: int) -> int:
    return max(1, min(int(limit), MAX_ROW_LIMIT))


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)
