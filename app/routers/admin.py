"""Admin router for administrative functionality."""

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import and_, desc, func
from sqlalchemy.orm import Session

from app.commands import create_api_key, revoke_api_key
from app.core.db import get_db_session, get_readonly_db_session
from app.core.deps import require_admin
from app.models.api.common import (
    OnboardingAudioDiscoverRequest,
    OnboardingAudioLanePreviewResponse,
)
from app.models.internal.admin_eval import (
    EVAL_MODEL_LABELS,
    EVAL_MODEL_SPECS,
    LONGFORM_TEMPLATE_LABELS,
    AdminEvalRunRequest,
)
from app.models.schema import Content, OnboardingDiscoveryRun, ProcessingTask
from app.models.user import User
from app.queries import list_api_keys
from app.services.admin_eval import get_default_pricing, run_admin_eval
from app.services.onboarding import preview_audio_lane_plan
from app.templates import templates

router = APIRouter(prefix="/admin", tags=["admin"])
TASK_STATUS_ORDER = ("pending", "processing", "failed", "completed")
DASHBOARD_STATS_RANGE_OPTIONS = (
    ("24h", "24h", timedelta(hours=24)),
    ("7d", "7d", timedelta(days=7)),
    ("30d", "30d", timedelta(days=30)),
    ("all", "All time", None),
)


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


def _build_scraper_health(db: Session, recent_cutoff: datetime) -> dict[str, Any]:
    """Return empty scraper event aggregates after EventLog removal."""
    return {
        "total_events_24h": 0,
        "error_events_24h": 0,
        "run_status_counts": {},
        "latest_stats_rows": [],
        "error_counts": [],
    }


def _build_queue_watchdog_health(db: Session, recent_cutoff: datetime) -> dict[str, Any]:
    """Return empty queue watchdog event aggregates after EventLog removal."""
    return {
        "total_runs_24h": 0,
        "total_touched_24h": 0,
        "runs_touching_tasks_24h": 0,
        "failed_runs_24h": 0,
        "latest_run_at": None,
        "action_stats": [],
        "alert_counts": {},
        "recent_actions": [],
    }


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


@router.get("/", response_class=HTMLResponse)
def admin_dashboard(
    request: Request,
    db: Annotated[Session, Depends(get_readonly_db_session)],
    _: None = Depends(require_admin),
    event_type: str | None = None,
    limit: int = 50,
    stats_range: str = "24h",
):
    """Admin dashboard with system statistics and event logs."""
    now = datetime.now(UTC)
    recent_cutoff = now - timedelta(hours=24)
    selected_stats_range = _normalize_dashboard_stats_range(stats_range)
    stats_cutoff = _get_dashboard_stats_cutoff(selected_stats_range, now=now)
    stats_range_links = _build_dashboard_stats_range_links(
        request,
        selected_stats_range=selected_stats_range,
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
    phase_status_rows = _build_phase_status_rows(db)
    recent_failure_rows, recent_failure_total = _build_recent_failure_rows(db, recent_cutoff)
    scraper_health = _build_scraper_health(db, recent_cutoff)
    watchdog_health = _build_queue_watchdog_health(db, recent_cutoff)
    user_stats, onboarding_latest_status_counts = _build_user_lifecycle(db, recent_cutoff)

    # EventLog has been removed; keep template context stable with empty values.
    event_logs: list[Any] = []
    event_types: list[str] = []

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
        "admin_dashboard.html",
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
            "queue_status_rows": queue_status_rows,
            "phase_status_rows": phase_status_rows,
            "recent_failure_rows": recent_failure_rows,
            "recent_failure_total": recent_failure_total,
            "scraper_total_events_24h": scraper_health["total_events_24h"],
            "scraper_error_events_24h": scraper_health["error_events_24h"],
            "scraper_run_status_counts": scraper_health["run_status_counts"],
            "scraper_latest_stats": scraper_health["latest_stats_rows"],
            "scraper_error_counts": scraper_health["error_counts"],
            "watchdog_total_runs_24h": watchdog_health["total_runs_24h"],
            "watchdog_total_touched_24h": watchdog_health["total_touched_24h"],
            "watchdog_runs_touching_tasks_24h": watchdog_health["runs_touching_tasks_24h"],
            "watchdog_failed_runs_24h": watchdog_health["failed_runs_24h"],
            "watchdog_latest_run_at": watchdog_health["latest_run_at"],
            "watchdog_action_stats": watchdog_health["action_stats"],
            "watchdog_alert_counts": watchdog_health["alert_counts"],
            "watchdog_recent_actions": watchdog_health["recent_actions"],
            "user_stats": user_stats,
            "onboarding_latest_status_counts": onboarding_latest_status_counts,
            "event_logs": event_logs,
            "event_types": event_types,
            "selected_event_type": event_type,
            "limit": limit,
            "content_without_summary": content_without_summary,
        },
    )


@router.get("/onboarding/lane-preview", response_class=HTMLResponse)
def onboarding_lane_preview_page(
    request: Request,
    _: None = Depends(require_admin),
) -> HTMLResponse:
    """Render admin tool for onboarding lane preview."""
    return templates.TemplateResponse(
        request,
        "admin_onboarding_lane_preview.html",
        {
            "request": request,
        },
    )


@router.post(
    "/onboarding/lane-preview",
    response_model=OnboardingAudioLanePreviewResponse,
)
async def onboarding_lane_preview(
    payload: OnboardingAudioDiscoverRequest,
    _: None = Depends(require_admin),
) -> OnboardingAudioLanePreviewResponse:
    """Preview generated onboarding lanes from transcript input."""
    try:
        return await preview_audio_lane_plan(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/evals/summaries", response_class=HTMLResponse)
def admin_eval_summaries_page(
    request: Request,
    _: None = Depends(require_admin),
) -> HTMLResponse:
    """Render admin summary eval UI."""
    return templates.TemplateResponse(
        request,
        "admin_eval_summaries.html",
        {
            "request": request,
            "model_specs": EVAL_MODEL_SPECS,
            "model_labels": EVAL_MODEL_LABELS,
            "template_labels": LONGFORM_TEMPLATE_LABELS,
            "default_pricing": get_default_pricing(),
        },
    )


@router.post("/evals/summaries/run")
def admin_eval_summaries_run(
    payload: AdminEvalRunRequest,
    db: Annotated[Session, Depends(get_readonly_db_session)],
    _: None = Depends(require_admin),
) -> dict[str, Any]:
    """Run summary/title eval against selected models and content samples."""
    return run_admin_eval(db, payload)


@router.get("/api-keys", response_class=HTMLResponse)
def admin_api_keys_page(
    request: Request,
    db: Annotated[Session, Depends(get_db_session)],
    admin_user: Annotated[User, Depends(require_admin)],
) -> HTMLResponse:
    """Render admin API key management UI."""
    users = db.query(User).order_by(User.email.asc()).all()
    api_keys = list_api_keys.execute(db)
    return templates.TemplateResponse(
        request,
        "admin_api_keys.html",
        {
            "request": request,
            "admin_user": admin_user,
            "users": users,
            "api_keys": api_keys,
            "created_key": None,
        },
    )


@router.post("/api-keys/create", response_class=HTMLResponse)
def admin_api_keys_create(
    request: Request,
    user_id: Annotated[int, Form(...)],
    db: Annotated[Session, Depends(get_db_session)],
    admin_user: Annotated[User, Depends(require_admin)],
) -> HTMLResponse:
    """Create an API key for a target user and reveal it once."""
    users = db.query(User).order_by(User.email.asc()).all()
    created = create_api_key.execute(
        db,
        user_id=user_id,
        created_by_admin_user_id=admin_user.id,
    )
    api_keys = list_api_keys.execute(db)
    return templates.TemplateResponse(
        request,
        "admin_api_keys.html",
        {
            "request": request,
            "admin_user": admin_user,
            "users": users,
            "api_keys": api_keys,
            "created_key": created,
        },
    )


@router.post("/api-keys/{api_key_id}/revoke")
def admin_api_keys_revoke(
    api_key_id: int,
    db: Annotated[Session, Depends(get_db_session)],
    _: None = Depends(require_admin),
) -> RedirectResponse:
    """Revoke an API key and return to the admin list."""
    revoke_api_key.execute(db, api_key_id=api_key_id)
    return RedirectResponse(url="/admin/api-keys", status_code=303)
