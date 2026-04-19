"""Pure remote operations for the operator CLI."""

from __future__ import annotations

import json
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
from app.models.content_mapper import content_to_domain
from app.models.schema import Content, ContentStatusEntry, ProcessingTask, VendorUsageRecord
from app.models.user import User
from app.services.content_metadata_merge import refresh_merge_content_metadata
from app.services.image_generation import ImageGenerationService, get_image_generation_service
from app.services.long_form_images import (
    has_active_generate_image_task,
    has_generated_long_form_image,
    is_visible_long_form_image_candidate,
)
from app.utils.image_urls import build_content_image_url, build_thumbnail_url

DEFAULT_ROW_LIMIT = 200
MAX_ROW_LIMIT = 1000
ESCAPED_NUL_JSON_PATTERN = r"%\\u0000%"
_ALLOWED_CONTROL_CHARACTERS = {"\n", "\r", "\t"}
ADMIN_IMAGE_REPAIR_PAYLOAD = {"source": "admin.fix.regenerate-images", "manual": True}


@dataclass(frozen=True)
class RemoteContext:
    """Resolved context for remote read-only operations."""

    database_url: str
    logs_dir: Path
    service_log_dir: Path


@lru_cache(maxsize=1)
def _load_schema_models() -> tuple[Any, Any, Any, Any]:
    """Load schema models lazily for DB-backed remote commands."""
    from app.models.schema import Content, ProcessingTask, VendorUsageRecord

    return Content, VendorUsageRecord, ProcessingTask, None


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
    """Return grouped usage totals from persisted vendor usage rows."""
    engine = create_engine(context.database_url, pool_pre_ping=True)
    session_factory = sessionmaker(bind=engine)
    try:
        with session_factory() as session:
            rows = _apply_usage_window(
                session.query(VendorUsageRecord),
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
    engine = create_engine(context.database_url, pool_pre_ping=True)
    session_factory = sessionmaker(bind=engine)
    try:
        with session_factory() as session:
            user = session.query(User).filter(User.id == user_id).first()
            query = _apply_usage_window(
                session.query(VendorUsageRecord).filter(VendorUsageRecord.user_id == user_id),
                since=since,
                until=until,
            )
            rows = query.order_by(VendorUsageRecord.created_at.desc()).limit(bounded_limit).all()
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
    engine = create_engine(context.database_url, pool_pre_ping=True)
    session_factory = sessionmaker(bind=engine)
    try:
        with session_factory() as session:
            content = session.query(Content).filter(Content.id == content_id).first()
            rows = (
                session.query(VendorUsageRecord)
                .filter(VendorUsageRecord.content_id == content_id)
                .order_by(VendorUsageRecord.created_at.desc())
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
    """Return event rows.

    EventLog has been removed from the app schema, so this remains a stable empty
    response for older admin CLI surfaces.
    """
    del context, event_type, event_name, status, since, until, unsafe_raw
    return {"limit": _bounded_limit(limit), "rows": [], "redacted": True}


def health_snapshot(context: RemoteContext) -> dict[str, Any]:
    """Return a coarse operational snapshot."""
    engine = create_engine(context.database_url, pool_pre_ping=True)
    session_factory = sessionmaker(bind=engine)
    try:
        with session_factory() as session:
            content_total = int(session.query(func.count(Content.id)).scalar() or 0)
            task_total = int(session.query(func.count(ProcessingTask.id)).scalar() or 0)
            content_rows = (
                session.query(Content.status, func.count(Content.id)).group_by(Content.status).all()
            )
            content_by_status: dict[str | None, int] = {
                status: int(count) for status, count in content_rows
            }
            task_rows = (
                session.query(ProcessingTask.status, func.count(ProcessingTask.id))
                .group_by(ProcessingTask.status)
                .all()
            )
            task_by_status: dict[str | None, int] = {
                status: int(count) for status, count in task_rows
            }
            latest_usage_at = session.query(func.max(VendorUsageRecord.created_at)).scalar()

            return {
                "content": {
                    "total": content_total,
                    "by_status": {str(key): int(value) for key, value in content_by_status.items()},
                },
                "tasks": {
                    "total": task_total,
                    "by_status": {str(key): int(value) for key, value in task_by_status.items()},
                },
                "events": {"total": 0},
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
    engine = create_engine(context.database_url, pool_pre_ping=True)
    session_factory = sessionmaker(bind=engine)
    try:
        with session_factory() as session:
            content_query = session.query(Content)
            if content_type:
                content_query = content_query.filter(Content.content_type == content_type)
            if hours is not None:
                cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=hours)
                content_query = content_query.filter(
                    func.coalesce(Content.processed_at, Content.updated_at, Content.created_at)
                    >= cutoff
                )
            content_rows = content_query.all()
            content_ids = (
                [row.id for row in content_rows] if (hours is not None or content_type) else None
            )

            task_query = session.query(ProcessingTask)
            if content_ids is not None:
                if content_ids:
                    task_query = task_query.filter(ProcessingTask.content_id.in_(content_ids))
                else:
                    task_query = task_query.filter(text("1 = 0"))

            deleted_tasks = int(task_query.count())
            if cancel_only:
                reset_contents = 0
            elif content_ids is not None:
                reset_contents = len(content_rows)
            else:
                reset_contents = int(session.query(func.count(Content.id)).scalar() or 0)
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


def preview_sanitize_content_metadata(
    context: RemoteContext,
    *,
    content_id: int | None,
    limit: int = 100,
) -> dict[str, Any]:
    """Preview malformed content-metadata rows that can be sanitized safely."""
    return _sanitize_content_metadata_rows(
        context,
        content_id=content_id,
        limit=limit,
        apply=False,
    )


def sanitize_content_metadata(
    context: RemoteContext,
    *,
    content_id: int | None,
    limit: int = 100,
) -> dict[str, Any]:
    """Apply metadata sanitization for malformed content rows."""
    return _sanitize_content_metadata_rows(
        context,
        content_id=content_id,
        limit=limit,
        apply=True,
    )


def preview_regenerate_images(
    context: RemoteContext,
    *,
    content_ids: list[int] | None,
    limit: int = 20,
) -> dict[str, Any]:
    """Preview long-form image regeneration candidates."""
    return _regenerate_images(context, content_ids=content_ids, limit=limit, apply=False)


def regenerate_images(
    context: RemoteContext,
    *,
    content_ids: list[int] | None,
    limit: int = 20,
) -> dict[str, Any]:
    """Regenerate long-form images directly against the live runtime DB."""
    return _regenerate_images(context, content_ids=content_ids, limit=limit, apply=True)


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
            connection.execute(text("SET TRANSACTION READ ONLY"))
            result = connection.execute(text(sql))
            rows = result.fetchmany(limit)
            columns = list(result.keys())
            serialized_rows = [dict(zip(columns, row, strict=False)) for row in rows]
            return serialized_rows, columns
    finally:
        engine.dispose()


def _sanitize_content_metadata_rows(
    context: RemoteContext,
    *,
    content_id: int | None,
    limit: int,
    apply: bool,
) -> dict[str, Any]:
    bounded_limit = _bounded_limit(limit)
    engine = create_engine(context.database_url, pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            selected = _select_malformed_content_rows(
                connection,
                content_id=content_id,
                limit=bounded_limit,
            )
            matched_total = _count_malformed_content_rows(connection, content_id=content_id)

            updated_rows: list[dict[str, Any]] = []
            for row in selected:
                sanitized_json = _sanitize_json_text(str(row["metadata_text"]))
                updated_rows.append(
                    {
                        "id": int(row["id"]),
                        "content_type": row["content_type"],
                        "status": row["status"],
                        "title": row["title"],
                        "created_at": row["created_at"],
                        "processed_at": row["processed_at"],
                        "changed": sanitized_json != str(row["metadata_text"]),
                    }
                )
                if not apply or sanitized_json == str(row["metadata_text"]):
                    continue
                connection.execute(
                    text(
                        """
                        UPDATE contents
                        SET content_metadata = CAST(:content_metadata AS json),
                            updated_at = :updated_at
                        WHERE id = :content_id
                        """
                    ),
                    {
                        "content_metadata": sanitized_json,
                        "updated_at": datetime.now(UTC).replace(tzinfo=None),
                        "content_id": int(row["id"]),
                    },
                )

            return {
                "applied": apply,
                "content_id": content_id,
                "limit": bounded_limit,
                "matched_total": matched_total,
                "selected_count": len(updated_rows),
                "updated_count": sum(1 for row in updated_rows if row["changed"]) if apply else 0,
                "rows": updated_rows,
            }
    finally:
        engine.dispose()


def _regenerate_images(
    context: RemoteContext,
    *,
    content_ids: list[int] | None,
    limit: int,
    apply: bool,
) -> dict[str, Any]:
    bounded_limit = _bounded_limit(limit)
    engine = create_engine(context.database_url, pool_pre_ping=True)
    session_factory = sessionmaker(bind=engine)
    try:
        with session_factory() as session:
            selected_content_ids = _resolve_image_regeneration_content_ids(
                session,
                content_ids=content_ids,
                limit=bounded_limit,
            )
            rows = [
                _build_image_regeneration_row(session, content_id)
                for content_id in selected_content_ids
            ]
            candidates = [
                row
                for row in rows
                if row["eligible"] and not row["has_generated_image"] and not row["has_active_task"]
            ]
            if not apply:
                return {
                    "applied": False,
                    "requested_content_ids": content_ids or [],
                    "limit": bounded_limit,
                    "matched_total": len(rows),
                    "selected_count": len(candidates),
                    "rows": rows,
                }

            results: list[dict[str, Any]] = []
            service = get_image_generation_service()
            for candidate in candidates:
                results.append(
                    _regenerate_one_content_image(
                        session,
                        service=service,
                        content_id=int(candidate["content_id"]),
                    )
                )

            session.commit()
            return {
                "applied": True,
                "requested_content_ids": content_ids or [],
                "limit": bounded_limit,
                "matched_total": len(rows),
                "selected_count": len(candidates),
                "updated_count": sum(1 for row in results if row["status"] == "completed"),
                "rows": rows,
                "results": results,
            }
    finally:
        engine.dispose()


def _resolve_image_regeneration_content_ids(
    session,
    *,
    content_ids: list[int] | None,
    limit: int,
) -> list[int]:
    if content_ids:
        unique_ids = list(dict.fromkeys(int(content_id) for content_id in content_ids))
        rows = session.query(Content.id).filter(Content.id.in_(unique_ids)).all()
        existing_ids = {int(row.id) for row in rows}
        return [content_id for content_id in unique_ids if content_id in existing_ids]

    failed_rows = (
        session.query(
            ProcessingTask.content_id,
            func.max(ProcessingTask.id).label("latest_task_id"),
        )
        .filter(ProcessingTask.task_type == "generate_image")
        .filter(ProcessingTask.status == "failed")
        .filter(ProcessingTask.content_id.is_not(None))
        .group_by(ProcessingTask.content_id)
        .order_by(func.max(ProcessingTask.id).desc())
        .limit(limit)
        .all()
    )
    return [int(row.content_id) for row in failed_rows if row.content_id is not None]


def _build_image_regeneration_row(session, content_id: int) -> dict[str, Any]:
    content = session.query(Content).filter(Content.id == content_id).first()
    if content is None:
        return {
            "content_id": content_id,
            "exists": False,
            "eligible": False,
            "has_generated_image": False,
        }

    latest_task = (
        session.query(ProcessingTask)
        .filter(ProcessingTask.content_id == content_id)
        .filter(ProcessingTask.task_type == "generate_image")
        .order_by(ProcessingTask.id.desc())
        .first()
    )
    inbox_count = int(
        session.query(func.count(ContentStatusEntry.id))
        .filter(ContentStatusEntry.content_id == content_id)
        .filter(ContentStatusEntry.status == "inbox")
        .scalar()
        or 0
    )
    return {
        "content_id": content_id,
        "exists": True,
        "content_type": content.content_type,
        "content_status": content.status,
        "classification": content.classification,
        "inbox_rows": inbox_count,
        "eligible": is_visible_long_form_image_candidate(session, content),
        "has_generated_image": has_generated_long_form_image(content),
        "has_active_task": has_active_generate_image_task(session, content_id),
        "latest_task_id": latest_task.id if latest_task is not None else None,
        "latest_task_status": latest_task.status if latest_task is not None else None,
        "latest_task_error": latest_task.error_message if latest_task is not None else None,
    }


def _regenerate_one_content_image(
    session,
    *,
    service: ImageGenerationService,
    content_id: int,
) -> dict[str, Any]:
    content = session.query(Content).filter(Content.id == content_id).first()
    if content is None:
        return {"content_id": content_id, "status": "missing"}

    task = ProcessingTask(
        task_type="generate_image",
        content_id=content_id,
        payload=dict(ADMIN_IMAGE_REPAIR_PAYLOAD),
        status="processing",
        queue_name="image",
        started_at=datetime.now(UTC).replace(tzinfo=None),
        locked_at=datetime.now(UTC).replace(tzinfo=None),
        locked_by="admin-fix",
        retry_count=0,
    )
    session.add(task)
    session.flush()

    try:
        result = service.generate_image(content_to_domain(content))
        if not result.success:
            task.status = "failed"
            task.completed_at = datetime.now(UTC).replace(tzinfo=None)
            task.error_message = result.error_message
            task.locked_at = None
            task.locked_by = None
            task.lease_expires_at = None
            session.flush()
            return {
                "content_id": content_id,
                "task_id": task.id,
                "status": "failed",
                "error_message": result.error_message,
            }

        base_metadata = dict(content.content_metadata or {})
        metadata = dict(base_metadata)
        metadata["image_generated_at"] = datetime.now(UTC).isoformat()
        metadata["image_url"] = build_content_image_url(content_id)
        if result.thumbnail_path:
            metadata["thumbnail_url"] = build_thumbnail_url(content_id)
        content.content_metadata = refresh_merge_content_metadata(
            session,
            content_id=content.id,
            base_metadata=base_metadata,
            updated_metadata=metadata,
        )

        task.status = "completed"
        task.completed_at = datetime.now(UTC).replace(tzinfo=None)
        task.error_message = None
        task.locked_at = None
        task.locked_by = None
        task.lease_expires_at = None
        session.flush()
        return {
            "content_id": content_id,
            "task_id": task.id,
            "status": "completed",
            "image_path": str(result.image_path),
            "thumbnail_path": str(result.thumbnail_path) if result.thumbnail_path else None,
        }
    except Exception as exc:  # noqa: BLE001
        task.status = "failed"
        task.completed_at = datetime.now(UTC).replace(tzinfo=None)
        task.error_message = str(exc)
        task.locked_at = None
        task.locked_by = None
        task.lease_expires_at = None
        session.flush()
        return {
            "content_id": content_id,
            "task_id": task.id,
            "status": "error",
            "error_message": str(exc),
        }


def _count_malformed_content_rows(connection, *, content_id: int | None) -> int:
    filters = ["CAST(content_metadata AS text) LIKE :pattern"]
    params: dict[str, Any] = {"pattern": ESCAPED_NUL_JSON_PATTERN}
    if content_id is not None:
        filters.append("id = :content_id")
        params["content_id"] = int(content_id)
    count_sql = f"SELECT count(*) FROM contents WHERE {' AND '.join(filters)}"
    return int(connection.execute(text(count_sql), params).scalar() or 0)


def _select_malformed_content_rows(
    connection,
    *,
    content_id: int | None,
    limit: int,
) -> list[dict[str, Any]]:
    filters = ["CAST(content_metadata AS text) LIKE :pattern"]
    params: dict[str, Any] = {
        "pattern": ESCAPED_NUL_JSON_PATTERN,
        "limit": int(limit),
    }
    if content_id is not None:
        filters.append("id = :content_id")
        params["content_id"] = int(content_id)
    select_sql = f"""
        SELECT
            id,
            content_type,
            status,
            title,
            created_at,
            processed_at,
            CAST(content_metadata AS text) AS metadata_text
        FROM contents
        WHERE {" AND ".join(filters)}
        ORDER BY id ASC
        LIMIT :limit
    """
    result = connection.execute(text(select_sql), params)
    return [dict(row._mapping) for row in result.fetchall()]


def _sanitize_json_text(raw_json: str) -> str:
    parsed = json.loads(raw_json)
    sanitized = _sanitize_json_value(parsed)
    return json.dumps(sanitized, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _sanitize_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            _strip_disallowed_control_characters(str(key)): _sanitize_json_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_json_value(item) for item in value]
    if isinstance(value, str):
        return _strip_disallowed_control_characters(value)
    return value


def _strip_disallowed_control_characters(value: str) -> str:
    return "".join(
        character
        for character in value
        if ord(character) >= 32 or character in _ALLOWED_CONTROL_CHARACTERS
    )


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
    lines: deque[dict[str, Any]] = deque(maxlen=limit or 10_000)
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


def _apply_usage_window(query: Any, *, since: datetime | None, until: datetime | None) -> Any:
    if since is not None:
        query = query.filter(VendorUsageRecord.created_at >= _naive_utc(since))
    if until is not None:
        query = query.filter(VendorUsageRecord.created_at <= _naive_utc(until))
    return query


def _usage_group_key(row: VendorUsageRecord, *, group_by: str) -> str:
    if group_by == "user":
        return str(row.user_id) if row.user_id is not None else "unknown"
    if group_by == "vendor":
        return str(row.provider or "unknown")
    return str(getattr(row, group_by, None) or "unknown")


def _serialize_usage_row(row: VendorUsageRecord, *, unsafe_raw: bool) -> dict[str, Any]:
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
        "request_count": row.request_count,
        "resource_count": row.resource_count,
        "cost_usd": row.cost_usd,
        "currency": row.currency,
        "pricing_version": row.pricing_version,
        "metadata": row.metadata_json,
        "created_at": row.created_at,
    }
    return payload if unsafe_raw else redact_value(payload)


def _summarize_usage_rows(rows: list[VendorUsageRecord]) -> dict[str, Any]:
    totals = _usage_totals()
    providers: Counter[str] = Counter()
    models: Counter[str] = Counter()
    for row in rows:
        _accumulate_usage(totals, row)
        providers[row.provider or "unknown"] += 1
        models[row.model or "unknown"] += 1
    totals["providers"] = dict(providers)
    totals["models"] = dict(models)
    return totals


def _usage_totals() -> dict[str, Any]:
    return {
        "call_count": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "request_count": 0,
        "resource_count": 0,
        "cost_usd": 0.0,
    }


def _accumulate_usage(bucket: dict[str, Any], row: VendorUsageRecord) -> None:
    bucket["call_count"] += 1
    bucket["input_tokens"] += int(row.input_tokens or 0)
    bucket["output_tokens"] += int(row.output_tokens or 0)
    bucket["total_tokens"] += int(row.total_tokens or 0)
    bucket["request_count"] += int(row.request_count or 0)
    bucket["resource_count"] += int(row.resource_count or 0)
    bucket["cost_usd"] += float(row.cost_usd or 0.0)
    bucket["cost_usd"] = round(bucket["cost_usd"], 8)


def _bounded_limit(limit: int) -> int:
    return max(1, min(int(limit), MAX_ROW_LIMIT))


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)
