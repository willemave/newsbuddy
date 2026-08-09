"""Admin dashboard route and dashboard-specific readout helpers."""

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import and_, case, desc, func
from sqlalchemy.orm import Session

from app.admin_web.formatting import format_user_label
from app.admin_web.templates import templates
from app.core.db import get_readonly_db_session
from app.core.deps import require_admin
from app.models.db import (
    Content,
    OnboardingDiscoveryRun,
    ProcessingTask,
    VendorUsageRecord,
)
from app.models.db.users import User
from app.queries.queue_health import get_queue_health_snapshot

router = APIRouter(tags=["admin"])
TASK_STATUS_ORDER = ("pending", "processing", "failed", "completed")
DASHBOARD_STATS_RANGE_OPTIONS = (
    ("24h", "24h", timedelta(hours=24)),
    ("7d", "7d", timedelta(days=7)),
    ("30d", "30d", timedelta(days=30)),
    ("all", "All time", None),
)
COST_BUCKET_OPTIONS = (
    ("day", "Daily", timedelta(days=30), 30, "last 30 days"),
    ("week", "Weekly", timedelta(weeks=12), 12, "last 12 weeks"),
    ("month", "Monthly", timedelta(days=365), 12, "last 12 months"),
)
COST_DEFAULT_BUCKET = "day"
COST_AVERAGE_LABELS = {
    "day": "Day",
    "week": "Week",
    "month": "Month",
}
COST_AREA_LABELS = {
    "chat": "Chat",
    "summarization": "Summarization",
    "image_generation": "Image Generation",
    "learning_decks": "Learning Decks",
}
COST_FEATURE_GROUPS = {
    "chat": {"chat"},
    "summarization": {"news_processing", "summarization"},
    "image_generation": {"image_generation"},
    "learning_decks": {"learning_deck_generation"},
}
COST_EXTERNAL_FEATURES = (
    "x_api",
    "exa",
    "transcription",
    "narration_tts",
    "podcast_search",
    "html_extraction",
    "object_storage",
    "chat_sandbox",
    "learning_deck_sandbox",
    "llm_task_sandbox",
)
COST_TRACKED_FEATURES = tuple(
    sorted({feature for features in COST_FEATURE_GROUPS.values() for feature in features})
)
COST_AREA_ORDER = ("chat", "summarization", "image_generation", "learning_decks")
COST_FEATURE_TO_AREA = {
    feature: area for area, features in COST_FEATURE_GROUPS.items() for feature in features
}
COST_BUCKET_CONFIGS = {
    value: {
        "value": value,
        "label": label,
        "lookback": lookback,
        "bucket_count": bucket_count,
        "window_label": window_label,
    }
    for value, label, lookback, bucket_count, window_label in COST_BUCKET_OPTIONS
}
EXTERNAL_PROVIDER_LABELS = {
    "x": "X API",
    "exa": "Exa",
    "openai": "OpenAI",
    "listen_notes": "Listen Notes",
    "spotify": "Spotify",
    "podcast_index": "Podcast Index",
    "firecrawl": "Firecrawl",
    "s3_compatible": "Object Storage",
    "e2b": "E2B Sandbox",
}


def _normalize_dashboard_stats_range(stats_range: str | None) -> str:
    """Return a supported dashboard stats range value."""
    allowed_values = {value for value, _, _ in DASHBOARD_STATS_RANGE_OPTIONS}
    if stats_range in allowed_values:
        return str(stats_range)
    return "24h"


def _get_dashboard_stats_cutoff(stats_range: str, *, now: datetime) -> datetime | None:
    """Return the timestamp cutoff for the selected dashboard stats range."""
    for value, _label, delta in DASHBOARD_STATS_RANGE_OPTIONS:
        if value == stats_range:
            if delta is None:
                return None
            return now - delta
    return now - timedelta(hours=24)


def _build_dashboard_stats_range_links(
    request: Request, *, selected_stats_range: str
) -> list[dict[str, Any]]:
    """Build dashboard stats range tabs while preserving other query params."""
    base_params = dict(request.query_params)
    options: list[dict[str, Any]] = []

    for value, label, _delta in DASHBOARD_STATS_RANGE_OPTIONS:
        params = {**base_params, "stats_range": value}
        href = request.url.path
        if params:
            href = f"{href}?{urlencode(params)}"
        options.append(
            {
                "value": value,
                "label": label,
                "href": href,
                "active": value == selected_stats_range,
            }
        )

    return options


def _normalize_cost_bucket(cost_bucket: str | None) -> str:
    """Return a supported cost bucket value."""
    if cost_bucket in COST_BUCKET_CONFIGS:
        return str(cost_bucket)
    return COST_DEFAULT_BUCKET


def _build_cost_bucket_links(
    request: Request, *, selected_cost_bucket: str
) -> list[dict[str, Any]]:
    """Build cost bucket tabs while preserving other query params."""
    base_params = dict(request.query_params)
    options: list[dict[str, Any]] = []

    for value, label, _delta, _bucket_count, _window_label in COST_BUCKET_OPTIONS:
        params = {**base_params, "cost_bucket": value}
        href = request.url.path
        if params:
            href = f"{href}?{urlencode(params)}"
        options.append(
            {
                "value": value,
                "label": label,
                "href": href,
                "active": value == selected_cost_bucket,
            }
        )

    return options


def _normalize_task_error_type(error_message: str | None) -> str:
    """Map raw task error messages to coarse error buckets."""
    if not error_message:
        return "unknown"

    message = error_message.strip()
    lowered = message.lower()
    if "timeout" in lowered:
        return "timeout"
    if "rate limit" in lowered or "429" in lowered:
        return "rate_limit"
    if "connection" in lowered:
        return "connection"
    if "validation" in lowered:
        return "validation"
    if "json" in lowered:
        return "json_parse"
    if "http" in lowered or "status_code" in lowered:
        return "http_error"

    first_token = message.split(":", maxsplit=1)[0].strip()
    if first_token and first_token[0].isalpha():
        return first_token[:80]
    return "unknown"


def _build_queue_status_rows(db: Session) -> list[dict[str, Any]]:
    """Build queue partition status rows for dashboard display."""
    queue_status_counts = (
        db.query(
            ProcessingTask.queue_name,
            ProcessingTask.status,
            func.count(ProcessingTask.id).label("count"),
        )
        .group_by(ProcessingTask.queue_name, ProcessingTask.status)
        .all()
    )

    queue_status_map: dict[str, dict[str, int]] = defaultdict(dict)
    for queue_name, status, count in queue_status_counts:
        queue_label = str(queue_name or "unknown")
        queue_status_map[queue_label][str(status or "unknown")] = int(count or 0)

    rows: list[dict[str, Any]] = []
    for queue_name, status_counts in sorted(queue_status_map.items()):
        row: dict[str, Any] = {"queue_name": queue_name}
        total = 0
        for status in TASK_STATUS_ORDER:
            value = int(status_counts.get(status, 0))
            row[status] = value
            total += value
        row["total"] = total
        rows.append(row)
    return rows


def _build_queue_health_dashboard(db: Session) -> dict[str, Any]:
    """Build queue SLO data for dashboard display."""
    snapshot = get_queue_health_snapshot(db, window_hours=24, top_errors_limit=5)
    return {
        "generated_at": snapshot.generated_at,
        "window_hours": snapshot.window_hours,
        "processing_count": snapshot.processing_count,
        "expired_lease_count": snapshot.expired_lease_count,
        "recent_failed_count": snapshot.recent_failed_count,
        "pending": [
            {
                "queue_name": row.queue_name,
                "task_type": row.task_type,
                "pending_count": row.pending_count,
                "oldest_pending_age": _format_duration_seconds(row.oldest_pending_age_seconds),
            }
            for row in snapshot.pending[:10]
        ],
        "processing": [
            {
                "queue_name": row.queue_name,
                "task_type": row.task_type,
                "processing_count": row.processing_count,
                "oldest_processing_age": _format_duration_seconds(
                    row.oldest_processing_age_seconds
                ),
            }
            for row in snapshot.processing[:10]
        ],
        "activity": [
            {
                "queue_name": row.queue_name,
                "task_type": row.task_type,
                "enqueued_count": row.enqueued_count,
                "completed_count": row.completed_count,
                "failed_count": row.failed_count,
            }
            for row in snapshot.activity[:10]
        ],
        "latency": [
            {
                "queue_name": row.queue_name,
                "task_type": row.task_type,
                "sample_count": row.sample_count,
                "ready_wait_p50": _format_duration_seconds(row.ready_wait_p50_seconds),
                "ready_wait_p95": _format_duration_seconds(row.ready_wait_p95_seconds),
                "total_wait_p50": _format_duration_seconds(row.total_wait_p50_seconds),
                "total_wait_p95": _format_duration_seconds(row.total_wait_p95_seconds),
                "run_time_p50": _format_duration_seconds(row.run_time_p50_seconds),
                "run_time_p95": _format_duration_seconds(row.run_time_p95_seconds),
            }
            for row in snapshot.latency
        ],
        "retry_buckets": snapshot.retry_buckets,
        "top_failures": snapshot.top_failures,
    }


def _format_duration_seconds(seconds: float | None) -> str:
    """Format a queue-age duration for compact admin display."""
    if seconds is None:
        return "unknown"
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = seconds / 60
    if minutes < 60:
        return f"{minutes:.0f}m"
    hours = minutes / 60
    if hours < 48:
        return f"{hours:.1f}h"
    return f"{hours / 24:.1f}d"


def _build_phase_status_rows(db: Session) -> list[dict[str, Any]]:
    """Build task-phase status rows for dashboard display."""
    phase_status_counts = (
        db.query(
            ProcessingTask.task_type,
            ProcessingTask.status,
            func.count(ProcessingTask.id).label("count"),
        )
        .group_by(ProcessingTask.task_type, ProcessingTask.status)
        .all()
    )

    phase_status_map: dict[str, dict[str, int]] = defaultdict(dict)
    for task_type, status, count in phase_status_counts:
        task_label = str(task_type or "unknown")
        phase_status_map[task_label][str(status or "unknown")] = int(count or 0)

    rows: list[dict[str, Any]] = []
    for task_type, status_counts in sorted(phase_status_map.items()):
        row: dict[str, Any] = {"task_type": task_type}
        total = 0
        for status in TASK_STATUS_ORDER:
            value = int(status_counts.get(status, 0))
            row[status] = value
            total += value
        row["total"] = total
        rows.append(row)
    return rows


def _build_recent_failure_rows(
    db: Session, recent_cutoff: datetime
) -> tuple[list[dict[str, Any]], int]:
    """Build recent task failure rollups by phase and normalized error type."""
    recent_failed_tasks = (
        db.query(ProcessingTask.task_type, ProcessingTask.error_message)
        .filter(ProcessingTask.status == "failed")
        .filter(ProcessingTask.completed_at >= recent_cutoff)
        .all()
    )

    failure_buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for task_type, error_message in recent_failed_tasks:
        task_label = str(task_type or "unknown")
        error_label = _normalize_task_error_type(error_message)
        bucket_key = (task_label, error_label)

        bucket = failure_buckets.get(bucket_key)
        if bucket is None:
            bucket = {
                "task_type": task_label,
                "error_type": error_label,
                "count": 0,
                "sample_error": (error_message or "unknown")[:240],
            }
            failure_buckets[bucket_key] = bucket
        bucket["count"] += 1

    rows = sorted(
        failure_buckets.values(),
        key=lambda item: int(item["count"]),
        reverse=True,
    )[:15]
    total = sum(int(item["count"]) for item in rows)
    return rows, total


def _count_failed_tasks(db: Session, *, cutoff: datetime | None) -> int:
    """Count failed tasks for the selected summary range."""
    query = db.query(func.count(ProcessingTask.id)).filter(ProcessingTask.status == "failed")
    if cutoff is not None:
        query = query.filter(ProcessingTask.completed_at >= cutoff)
    return int(query.scalar() or 0)


def _build_user_lifecycle(
    db: Session, recent_cutoff: datetime
) -> tuple[dict[str, int], dict[str, int]]:
    """Build user lifecycle and latest onboarding status aggregates."""
    total_users = int(db.query(func.count(User.id)).scalar() or 0)
    active_users = int(db.query(func.count(User.id)).filter(User.is_active.is_(True)).scalar() or 0)
    tutorial_completed_users = int(
        db.query(func.count(User.id))
        .filter(User.has_completed_new_user_tutorial.is_(True))
        .scalar()
        or 0
    )
    new_users_24h = int(
        db.query(func.count(User.id)).filter(User.created_at >= recent_cutoff).scalar() or 0
    )
    admin_users = int(db.query(func.count(User.id)).filter(User.is_admin.is_(True)).scalar() or 0)
    users_with_onboarding = int(
        db.query(func.count(func.distinct(OnboardingDiscoveryRun.user_id))).scalar() or 0
    )

    latest_onboarding_subquery = (
        db.query(
            OnboardingDiscoveryRun.user_id.label("user_id"),
            func.max(OnboardingDiscoveryRun.created_at).label("latest_created_at"),
        )
        .group_by(OnboardingDiscoveryRun.user_id)
        .subquery()
    )
    latest_onboarding_rows = (
        db.query(
            OnboardingDiscoveryRun.status,
            func.count(OnboardingDiscoveryRun.id).label("count"),
        )
        .join(
            latest_onboarding_subquery,
            and_(
                OnboardingDiscoveryRun.user_id == latest_onboarding_subquery.c.user_id,
                OnboardingDiscoveryRun.created_at == latest_onboarding_subquery.c.latest_created_at,
            ),
        )
        .group_by(OnboardingDiscoveryRun.status)
        .all()
    )
    latest_onboarding_status_counts = {
        str(status or "unknown"): int(count or 0) for status, count in latest_onboarding_rows
    }

    lifecycle = {
        "total_users": total_users,
        "active_users": active_users,
        "inactive_users": max(total_users - active_users, 0),
        "tutorial_completed_users": tutorial_completed_users,
        "tutorial_pending_users": max(total_users - tutorial_completed_users, 0),
        "new_users_24h": new_users_24h,
        "admin_users": admin_users,
        "users_with_onboarding": users_with_onboarding,
        "users_without_onboarding": max(total_users - users_with_onboarding, 0),
    }
    return lifecycle, latest_onboarding_status_counts


def _cost_bucket_config(cost_bucket: str) -> dict[str, Any]:
    """Return dashboard cost window config for the requested bucket."""
    return COST_BUCKET_CONFIGS.get(cost_bucket, COST_BUCKET_CONFIGS[COST_DEFAULT_BUCKET])


def _build_cost_bucket_label_expr(db: Session, cost_bucket: str) -> Any:
    """Return a dialect-aware SQL expression for cost bucket labels."""
    dialect_name = ""
    if db.bind is not None and db.bind.dialect is not None:
        dialect_name = db.bind.dialect.name

    if cost_bucket == "day":
        return func.date(VendorUsageRecord.created_at)

    if cost_bucket == "week":
        if dialect_name == "sqlite":
            return func.printf(
                "%s-W%s",
                func.strftime("%Y", VendorUsageRecord.created_at),
                func.strftime("%W", VendorUsageRecord.created_at),
            )
        return func.to_char(
            func.date_trunc("week", VendorUsageRecord.created_at),
            'IYYY-"W"IW',
        )

    if dialect_name == "sqlite":
        return func.strftime("%Y-%m", VendorUsageRecord.created_at)
    return func.to_char(
        func.date_trunc("month", VendorUsageRecord.created_at),
        "YYYY-MM",
    )


def _build_cost_analysis(
    db: Session,
    *,
    cost_bucket: str,
    now: datetime,
) -> dict[str, Any]:
    """Build admin dashboard cost analysis for tracked LLM areas."""
    config = _cost_bucket_config(cost_bucket)
    start_dt = now - config["lookback"]
    bucket_expr = _build_cost_bucket_label_expr(db, cost_bucket)
    cost_expr = func.coalesce(VendorUsageRecord.cost_usd, 0.0)
    filters = (
        VendorUsageRecord.feature.in_(COST_TRACKED_FEATURES),
        VendorUsageRecord.created_at >= start_dt,
    )

    (
        total_cost_usd,
        total_rows,
        attributed_cost_usd,
        unattributed_cost_usd,
        attributed_user_count,
    ) = (
        db.query(
            func.coalesce(func.sum(cost_expr), 0.0),
            func.count(VendorUsageRecord.id),
            func.coalesce(
                func.sum(
                    case(
                        (VendorUsageRecord.user_id.is_not(None), cost_expr),
                        else_=0.0,
                    )
                ),
                0.0,
            ),
            func.coalesce(
                func.sum(
                    case(
                        (VendorUsageRecord.user_id.is_(None), cost_expr),
                        else_=0.0,
                    )
                ),
                0.0,
            ),
            func.count(func.distinct(VendorUsageRecord.user_id)),
        )
        .filter(*filters)
        .one()
    )

    area_rollups: dict[str, dict[str, Any]] = {
        area: {
            "area": area,
            "label": COST_AREA_LABELS[area],
            "row_count": 0,
            "cost_usd": 0.0,
            "attributed_cost_usd": 0.0,
            "share_of_total": 0.0,
        }
        for area in COST_AREA_ORDER
    }
    area_rows = (
        db.query(
            VendorUsageRecord.feature.label("feature"),
            func.count(VendorUsageRecord.id).label("row_count"),
            func.coalesce(func.sum(cost_expr), 0.0).label("cost_usd"),
            func.coalesce(
                func.sum(
                    case(
                        (VendorUsageRecord.user_id.is_not(None), cost_expr),
                        else_=0.0,
                    )
                ),
                0.0,
            ).label("attributed_cost_usd"),
        )
        .filter(*filters)
        .group_by(VendorUsageRecord.feature)
        .all()
    )
    for row in area_rows:
        area = COST_FEATURE_TO_AREA.get(row.feature)
        if area is None:
            continue
        area_rollup = area_rollups[area]
        area_rollup["row_count"] += int(row.row_count or 0)
        area_rollup["cost_usd"] += float(row.cost_usd or 0.0)
        area_rollup["attributed_cost_usd"] += float(row.attributed_cost_usd or 0.0)

    total_cost = float(total_cost_usd or 0.0)
    for area in COST_AREA_ORDER:
        area_rollup = area_rollups[area]
        area_rollup["cost_usd"] = round(area_rollup["cost_usd"], 8)
        area_rollup["attributed_cost_usd"] = round(area_rollup["attributed_cost_usd"], 8)
        area_rollup["average_cost_usd"] = round(
            area_rollup["cost_usd"] / config["bucket_count"],
            8,
        )
        area_rollup["share_of_total"] = round(
            (area_rollup["cost_usd"] / total_cost * 100.0) if total_cost > 0 else 0.0,
            2,
        )

    bucket_rows = (
        db.query(
            bucket_expr.label("bucket_label"),
            VendorUsageRecord.feature.label("feature"),
            func.coalesce(func.sum(cost_expr), 0.0).label("cost_usd"),
            func.coalesce(
                func.sum(
                    case(
                        (VendorUsageRecord.user_id.is_not(None), cost_expr),
                        else_=0.0,
                    )
                ),
                0.0,
            ).label("attributed_cost_usd"),
        )
        .filter(*filters)
        .group_by(bucket_expr, VendorUsageRecord.feature)
        .order_by(bucket_expr.desc())
        .all()
    )
    bucket_rollups: dict[str, dict[str, Any]] = {}
    for row in bucket_rows:
        bucket_label = str(row.bucket_label)
        bucket_rollup = bucket_rollups.setdefault(
            bucket_label,
            {
                "bucket_label": bucket_label,
                "total_cost_usd": 0.0,
                "attributed_cost_usd": 0.0,
                "chat_cost_usd": 0.0,
                "summarization_cost_usd": 0.0,
                "image_generation_cost_usd": 0.0,
                "learning_decks_cost_usd": 0.0,
            },
        )
        area = COST_FEATURE_TO_AREA.get(row.feature)
        if area is None:
            continue
        cost_value = float(row.cost_usd or 0.0)
        attributed_value = float(row.attributed_cost_usd or 0.0)
        bucket_rollup["total_cost_usd"] += cost_value
        bucket_rollup["attributed_cost_usd"] += attributed_value
        bucket_rollup[f"{area}_cost_usd"] += cost_value

    period_rows = sorted(
        (
            {
                **row,
                "total_cost_usd": round(float(row["total_cost_usd"]), 8),
                "attributed_cost_usd": round(float(row["attributed_cost_usd"]), 8),
                "chat_cost_usd": round(float(row["chat_cost_usd"]), 8),
                "summarization_cost_usd": round(float(row["summarization_cost_usd"]), 8),
                "image_generation_cost_usd": round(float(row["image_generation_cost_usd"]), 8),
                "learning_decks_cost_usd": round(float(row["learning_decks_cost_usd"]), 8),
            }
            for row in bucket_rollups.values()
        ),
        key=lambda row: str(row["bucket_label"]),
        reverse=True,
    )[: int(config["bucket_count"])]

    user_rows = (
        db.query(
            VendorUsageRecord.user_id.label("user_id"),
            User.email.label("email"),
            User.full_name.label("full_name"),
            VendorUsageRecord.feature.label("feature"),
            func.coalesce(func.sum(cost_expr), 0.0).label("cost_usd"),
            func.count(VendorUsageRecord.id).label("row_count"),
        )
        .outerjoin(User, User.id == VendorUsageRecord.user_id)
        .filter(*filters)
        .filter(VendorUsageRecord.user_id.is_not(None))
        .group_by(
            VendorUsageRecord.user_id,
            User.email,
            User.full_name,
            VendorUsageRecord.feature,
        )
        .all()
    )
    user_rollups: dict[int, dict[str, Any]] = {}
    for row in user_rows:
        user_id = int(row.user_id)
        user_rollup = user_rollups.setdefault(
            user_id,
            {
                "user_id": user_id,
                "user_label": format_user_label(user_id, row.email, row.full_name),
                "total_cost_usd": 0.0,
                "row_count": 0,
                "chat_cost_usd": 0.0,
                "summarization_cost_usd": 0.0,
                "image_generation_cost_usd": 0.0,
                "learning_decks_cost_usd": 0.0,
            },
        )
        area = COST_FEATURE_TO_AREA.get(row.feature)
        if area is None:
            continue
        cost_value = float(row.cost_usd or 0.0)
        user_rollup["total_cost_usd"] += cost_value
        user_rollup["row_count"] += int(row.row_count or 0)
        user_rollup[f"{area}_cost_usd"] += cost_value

    user_rollup_rows = sorted(
        (
            {
                **row,
                "total_cost_usd": round(float(row["total_cost_usd"]), 8),
                "chat_cost_usd": round(float(row["chat_cost_usd"]), 8),
                "summarization_cost_usd": round(float(row["summarization_cost_usd"]), 8),
                "image_generation_cost_usd": round(float(row["image_generation_cost_usd"]), 8),
                "learning_decks_cost_usd": round(float(row["learning_decks_cost_usd"]), 8),
                "average_cost_usd": round(
                    float(row["total_cost_usd"]) / config["bucket_count"],
                    8,
                ),
            }
            for row in user_rollups.values()
        ),
        key=lambda row: float(row["total_cost_usd"]),
        reverse=True,
    )[:15]

    return {
        "selected_cost_bucket_label": config["label"],
        "average_label": COST_AVERAGE_LABELS[config["value"]],
        "cost_window_label": config["window_label"],
        "total_cost_usd": round(total_cost, 8),
        "total_rows": int(total_rows or 0),
        "attributed_cost_usd": round(float(attributed_cost_usd or 0.0), 8),
        "unattributed_cost_usd": round(float(unattributed_cost_usd or 0.0), 8),
        "attributed_user_count": int(attributed_user_count or 0),
        "average_cost_usd": round(total_cost / config["bucket_count"], 8),
        "area_rows": [area_rollups[area] for area in COST_AREA_ORDER],
        "period_rows": period_rows,
        "user_rows": user_rollup_rows,
    }


def _build_external_api_analysis(
    db: Session,
    *,
    cost_bucket: str,
    now: datetime,
) -> dict[str, Any]:
    """Build provider-level external API rollups for non-LLM services."""
    config = _cost_bucket_config(cost_bucket)
    start_dt = now - config["lookback"]
    cost_expr = func.coalesce(VendorUsageRecord.cost_usd, 0.0)
    provider_rows = (
        db.query(
            VendorUsageRecord.provider.label("provider"),
            func.count(VendorUsageRecord.id).label("row_count"),
            func.coalesce(func.sum(cost_expr), 0.0).label("cost_usd"),
            func.coalesce(func.sum(VendorUsageRecord.request_count), 0).label("request_count"),
            func.coalesce(func.sum(VendorUsageRecord.resource_count), 0).label("resource_count"),
        )
        .filter(VendorUsageRecord.feature.in_(COST_EXTERNAL_FEATURES))
        .filter(VendorUsageRecord.created_at >= start_dt)
        .group_by(VendorUsageRecord.provider)
        .order_by(
            func.coalesce(func.sum(cost_expr), 0.0).desc(),
            func.count(VendorUsageRecord.id).desc(),
        )
        .all()
    )
    rows = [
        {
            "provider": row.provider,
            "label": EXTERNAL_PROVIDER_LABELS.get(str(row.provider), str(row.provider)),
            "row_count": int(row.row_count or 0),
            "cost_usd": round(float(row.cost_usd or 0.0), 8),
            "request_count": int(row.request_count or 0),
            "resource_count": int(row.resource_count or 0),
            "average_cost_usd": round(float(row.cost_usd or 0.0) / config["bucket_count"], 8),
        }
        for row in provider_rows
    ]
    return {
        "rows": rows[:12],
        "window_label": config["window_label"],
        "average_label": COST_AVERAGE_LABELS[config["value"]],
    }


@router.get("/", response_class=HTMLResponse)
def admin_dashboard(
    request: Request,
    db: Annotated[Session, Depends(get_readonly_db_session)],
    _: None = Depends(require_admin),
    stats_range: str = "24h",
    cost_bucket: str = "day",
):
    """Admin dashboard with system statistics and operational readouts."""
    now = datetime.now(UTC)
    recent_cutoff = now - timedelta(hours=24)
    selected_stats_range = _normalize_dashboard_stats_range(stats_range)
    stats_cutoff = _get_dashboard_stats_cutoff(selected_stats_range, now=now)
    stats_range_links = _build_dashboard_stats_range_links(
        request,
        selected_stats_range=selected_stats_range,
    )
    selected_cost_bucket = _normalize_cost_bucket(cost_bucket)
    cost_bucket_links = _build_cost_bucket_links(
        request,
        selected_cost_bucket=selected_cost_bucket,
    )
    stats_range_label = next(
        label
        for value, label, _delta in DASHBOARD_STATS_RANGE_OPTIONS
        if value == selected_stats_range
    )

    # Content statistics
    content_stats_query = db.query(Content.content_type, func.count(Content.id).label("count"))
    total_content_query = db.query(func.count(Content.id))
    if stats_cutoff is not None:
        content_stats_query = content_stats_query.filter(Content.created_at >= stats_cutoff)
        total_content_query = total_content_query.filter(Content.created_at >= stats_cutoff)

    content_stats_result = content_stats_query.group_by(Content.content_type).all()
    content_stats = {row.content_type: row.count for row in content_stats_result}
    total_content = int(total_content_query.scalar() or 0)

    # Task statistics
    task_stats_query = db.query(ProcessingTask.status, func.count(ProcessingTask.id).label("count"))
    if stats_cutoff is not None:
        task_stats_query = task_stats_query.filter(ProcessingTask.created_at >= stats_cutoff)

    task_stats_result = task_stats_query.group_by(ProcessingTask.status).all()
    task_stats = {row.status: row.count for row in task_stats_result}
    total_tasks = int(
        (
            db.query(func.count(ProcessingTask.id))
            .filter(ProcessingTask.created_at >= stats_cutoff)
            .scalar()
            if stats_cutoff is not None
            else db.query(func.count(ProcessingTask.id)).scalar()
        )
        or 0
    )
    summary_failure_total = _count_failed_tasks(db, cutoff=stats_cutoff)

    # Dashboard readouts
    queue_status_rows = _build_queue_status_rows(db)
    queue_health = _build_queue_health_dashboard(db)
    phase_status_rows = _build_phase_status_rows(db)
    recent_failure_rows, recent_failure_total = _build_recent_failure_rows(db, recent_cutoff)
    user_stats, onboarding_latest_status_counts = _build_user_lifecycle(db, recent_cutoff)
    cost_analysis = _build_cost_analysis(
        db,
        cost_bucket=selected_cost_bucket,
        now=now,
    )
    external_api_analysis = _build_external_api_analysis(
        db,
        cost_bucket=selected_cost_bucket,
        now=now,
    )

    # Content with missing summary and explicit errors
    content_without_summary = (
        db.query(Content)
        .filter(
            (Content.content_metadata["summary"].is_(None))
            | (Content.content_metadata["summary"].as_string() == "null")
        )
        .filter(Content.error_message.is_not(None))
        .order_by(desc(Content.created_at))
        .limit(20)
        .all()
    )

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "request": request,
            "content_stats": content_stats,
            "total_content": total_content,
            "task_stats": task_stats,
            "total_tasks": total_tasks,
            "summary_failure_total": summary_failure_total,
            "selected_stats_range": selected_stats_range,
            "selected_stats_range_label": stats_range_label,
            "stats_range_links": stats_range_links,
            "cost_bucket_links": cost_bucket_links,
            "cost_analysis": cost_analysis,
            "external_api_analysis": external_api_analysis,
            "queue_status_rows": queue_status_rows,
            "queue_health": queue_health,
            "phase_status_rows": phase_status_rows,
            "recent_failure_rows": recent_failure_rows,
            "recent_failure_total": recent_failure_total,
            "user_stats": user_stats,
            "onboarding_latest_status_counts": onboarding_latest_status_counts,
            "content_without_summary": content_without_summary,
        },
    )
