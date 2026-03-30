"""Admin router for administrative functionality."""

import asyncio
import base64
import contextlib
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import and_, desc, func
from sqlalchemy.orm import Session

from app.application.commands import create_api_key, revoke_api_key
from app.application.queries import list_api_keys
from app.core.db import get_db_session, get_readonly_db_session
from app.core.deps import ADMIN_SESSION_COOKIE, require_admin
from app.core.settings import get_settings
from app.models.schema import Content, OnboardingDiscoveryRun, ProcessingTask
from app.models.user import User
from app.routers.admin_conversational_models import AdminConversationalHealthResponse
from app.routers.api.models import (
    OnboardingAudioDiscoverRequest,
    OnboardingAudioLanePreviewResponse,
)
from app.services.admin_conversational_agent import (
    AgentConversationRuntime,
    build_available_knowledge_context,
    build_health_flags,
    close_agent_session,
    create_or_get_session_state,
    search_knowledge,
    search_web,
    serialize_knowledge_hits,
    serialize_web_hits,
    start_agent_session,
    stream_agent_turn,
)
from app.services.admin_eval import (
    EVAL_MODEL_LABELS,
    EVAL_MODEL_SPECS,
    LONGFORM_TEMPLATE_LABELS,
    AdminEvalRunRequest,
    get_default_pricing,
    run_admin_eval,
)
from app.services.onboarding import preview_audio_lane_plan
from app.templates import templates

router = APIRouter(prefix="/admin", tags=["admin"])
TASK_STATUS_ORDER = ("pending", "processing", "failed", "completed")


def _has_valid_admin_session(websocket: WebSocket) -> bool:
    """Validate admin auth cookie for websocket endpoints."""
    from app.routers.auth import admin_sessions

    session_token = websocket.cookies.get(ADMIN_SESSION_COOKIE)
    return bool(session_token and session_token in admin_sessions)


async def _send_ws_event(websocket: WebSocket, payload: dict[str, Any]) -> bool:
    """Send websocket event and indicate whether the connection is still open."""
    try:
        await websocket.send_json(payload)
        return True
    except (RuntimeError, WebSocketDisconnect):
        return False


class _TurnEventEmitter:
    """Thread-safe event bridge from worker thread to async websocket queue."""

    def __init__(self, event_loop: asyncio.AbstractEventLoop, queue: asyncio.Queue[dict[str, Any]]):
        self._event_loop = event_loop
        self._queue = queue

    def __call__(self, event: dict[str, Any]) -> None:
        def enqueue() -> None:
            if self._queue.full() and event.get("type") in {"assistant_delta", "audio_chunk_raw"}:
                return
            if self._queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    self._queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                self._queue.put_nowait(event)

        self._event_loop.call_soon_threadsafe(enqueue)


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
    active_users = int(
        db.query(func.count(User.id)).filter(User.is_active.is_(True)).scalar() or 0
    )
    tutorial_completed_users = int(
        db.query(func.count(User.id))
        .filter(User.has_completed_new_user_tutorial.is_(True))
        .scalar()
        or 0
    )
    new_users_24h = int(
        db.query(func.count(User.id)).filter(User.created_at >= recent_cutoff).scalar() or 0
    )
    admin_users = int(
        db.query(func.count(User.id)).filter(User.is_admin.is_(True)).scalar() or 0
    )
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
                OnboardingDiscoveryRun.created_at
                == latest_onboarding_subquery.c.latest_created_at,
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
):
    """Admin dashboard with system statistics and event logs."""
    recent_cutoff = datetime.now(UTC) - timedelta(hours=24)

    # Content statistics
    content_stats_result = (
        db.query(Content.content_type, func.count(Content.id).label("count"))
        .group_by(Content.content_type)
        .all()
    )
    content_stats = {row.content_type: row.count for row in content_stats_result}
    total_content = db.query(func.count(Content.id)).scalar() or 0

    # Task statistics
    task_stats_result = (
        db.query(ProcessingTask.status, func.count(ProcessingTask.id).label("count"))
        .group_by(ProcessingTask.status)
        .all()
    )
    task_stats = {row.status: row.count for row in task_stats_result}
    total_tasks = db.query(func.count(ProcessingTask.id)).scalar() or 0
    recent_tasks = (
        db.query(func.count(ProcessingTask.id))
        .filter(ProcessingTask.created_at >= recent_cutoff)
        .scalar()
        or 0
    )

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
            | (Content.content_metadata["summary"] == "null")
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
            "recent_tasks": recent_tasks,
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


@router.get("/conversational", response_class=HTMLResponse)
def admin_conversational_page(
    request: Request,
    _: None = Depends(require_admin),
) -> HTMLResponse:
    """Render admin conversational prototype UI."""
    return templates.TemplateResponse(
        request,
        "admin_conversational.html",
        {
            "request": request,
        },
    )


@router.get("/conversational/health", response_model=AdminConversationalHealthResponse)
def admin_conversational_health(
    _: None = Depends(require_admin),
) -> AdminConversationalHealthResponse:
    """Report readiness for admin conversational features."""
    return AdminConversationalHealthResponse(**build_health_flags())


@router.websocket("/conversational/ws")
async def admin_conversational_ws(
    websocket: WebSocket,
    db: Annotated[Session, Depends(get_readonly_db_session)],
) -> None:
    """Websocket endpoint for streaming admin conversational turns."""
    if not _has_valid_admin_session(websocket):
        await websocket.close(code=4401)
        return

    await websocket.accept()
    settings = get_settings()
    max_queue_size = max(10, settings.admin_conversational_ws_max_queue)
    selected_user_id: int | None = None
    session_id: str | None = None
    runtime: AgentConversationRuntime | None = None

    try:
        while True:
            try:
                message = await websocket.receive_json()
            except WebSocketDisconnect:
                return
            except Exception:
                is_open = await _send_ws_event(
                    websocket,
                    {
                        "type": "error",
                        "code": "invalid_payload",
                        "message": "Expected JSON message payload.",
                    },
                )
                if not is_open:
                    return
                continue

            message_type = str(message.get("type", "")).strip().lower()
            if message_type == "ping":
                is_open = await _send_ws_event(websocket, {"type": "pong"})
                if not is_open:
                    return
                continue

            if message_type == "init":
                raw_user_id = message.get("user_id")
                raw_session_id = message.get("session_id")
                requested_session_id = raw_session_id if isinstance(raw_session_id, str) else None
                try:
                    user_id = int(raw_user_id)
                except (TypeError, ValueError):
                    is_open = await _send_ws_event(
                        websocket,
                        {
                            "type": "error",
                            "code": "invalid_user_id",
                            "message": "user_id must be a positive integer.",
                        },
                    )
                    if not is_open:
                        return
                    continue

                if user_id <= 0:
                    is_open = await _send_ws_event(
                        websocket,
                        {
                            "type": "error",
                            "code": "invalid_user_id",
                            "message": "user_id must be a positive integer.",
                        },
                    )
                    if not is_open:
                        return
                    continue

                user_exists = db.query(User.id).filter(User.id == user_id).first()
                if user_exists is None:
                    is_open = await _send_ws_event(
                        websocket,
                        {
                            "type": "error",
                            "code": "user_not_found",
                            "message": "Selected user_id does not exist.",
                        },
                    )
                    if not is_open:
                        return
                    continue

                try:
                    state = create_or_get_session_state(requested_session_id, user_id)
                except ValueError as exc:
                    is_open = await _send_ws_event(
                        websocket,
                        {
                            "type": "error",
                            "code": "invalid_session",
                            "message": str(exc),
                        },
                    )
                    if not is_open:
                        return
                    continue

                if runtime is not None:
                    with contextlib.suppress(Exception):
                        await asyncio.to_thread(close_agent_session, runtime)
                    runtime = None

                bootstrap_context = build_available_knowledge_context(
                    db=db,
                    user_id=user_id,
                    limit=100,
                )

                try:
                    runtime = await asyncio.to_thread(
                        start_agent_session,
                        state.session_id,
                        user_id,
                        bootstrap_context,
                    )
                except Exception as exc:  # noqa: BLE001
                    selected_user_id = None
                    session_id = None
                    is_open = await _send_ws_event(
                        websocket,
                        {
                            "type": "error",
                            "code": "agent_session_error",
                            "message": str(exc),
                        },
                    )
                    if not is_open:
                        return
                    continue

                selected_user_id = user_id
                session_id = state.session_id
                is_open = await _send_ws_event(
                    websocket,
                    {
                        "type": "ready",
                        "session_id": session_id,
                    },
                )
                if not is_open:
                    return
                continue

            if message_type != "user_message":
                is_open = await _send_ws_event(
                    websocket,
                    {
                        "type": "error",
                        "code": "unknown_event",
                        "message": f"Unsupported event type: {message_type or 'empty'}",
                    },
                )
                if not is_open:
                    return
                continue

            if selected_user_id is None or session_id is None or runtime is None:
                is_open = await _send_ws_event(
                    websocket,
                    {
                        "type": "error",
                        "code": "session_not_initialized",
                        "message": "Send init event before user_message.",
                    },
                )
                if not is_open:
                    return
                continue

            text = str(message.get("text", "")).strip()
            if not text:
                is_open = await _send_ws_event(
                    websocket,
                    {
                        "type": "error",
                        "code": "empty_message",
                        "message": "text is required.",
                    },
                )
                if not is_open:
                    return
                continue

            turn_id = str(message.get("turn_id") or f"turn_{uuid4().hex}")
            is_open = await _send_ws_event(websocket, {"type": "turn_started", "turn_id": turn_id})
            if not is_open:
                return

            knowledge_hits = search_knowledge(db=db, user_id=selected_user_id, query=text, limit=5)
            web_hits = await asyncio.to_thread(search_web, text, 5)
            is_open = await _send_ws_event(
                websocket,
                {
                    "type": "sources",
                    "turn_id": turn_id,
                    "knowledge_hits": serialize_knowledge_hits(knowledge_hits),
                    "web_hits": serialize_web_hits(web_hits),
                },
            )
            if not is_open:
                return

            queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=max_queue_size)
            event_loop = asyncio.get_running_loop()
            emit_event = _TurnEventEmitter(event_loop, queue)
            current_runtime = runtime

            def run_turn(
                local_runtime: AgentConversationRuntime = current_runtime,
                local_text: str = text,
                local_turn_id: str = turn_id,
                local_knowledge_hits=knowledge_hits,
                local_web_hits=web_hits,
                local_emit_event: _TurnEventEmitter = emit_event,
            ) -> None:
                try:
                    stream_agent_turn(
                        runtime=local_runtime,
                        user_text=local_text,
                        turn_id=local_turn_id,
                        emit_event=local_emit_event,
                        knowledge_hits=local_knowledge_hits,
                        web_hits=local_web_hits,
                    )
                    local_emit_event({"type": "_internal_done"})
                except TimeoutError as exc:
                    local_emit_event(
                        {
                            "type": "_internal_error",
                            "code": "turn_timeout",
                            "message": str(exc),
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    local_emit_event(
                        {
                            "type": "_internal_error",
                            "code": "agent_error",
                            "message": str(exc),
                        }
                    )

            worker_task = asyncio.create_task(asyncio.to_thread(run_turn))

            while True:
                event = await queue.get()
                event_type = event.get("type")

                if event_type == "_internal_done":
                    is_open = await _send_ws_event(
                        websocket,
                        {"type": "turn_complete", "turn_id": turn_id},
                    )
                    break

                if event_type == "_internal_error":
                    is_open = await _send_ws_event(
                        websocket,
                        {
                            "type": "error",
                            "turn_id": turn_id,
                            "code": str(event.get("code", "agent_error")),
                            "message": str(event.get("message", "Turn failed.")),
                        },
                    )
                    break

                if event_type == "audio_chunk_raw":
                    audio_bytes = event.get("audio_bytes")
                    if isinstance(audio_bytes, (bytes, bytearray)):
                        payload = {
                            "type": "audio_chunk",
                            "turn_id": turn_id,
                            "seq": int(event.get("seq", 0)),
                            "mime_type": str(event.get("mime_type", "application/octet-stream")),
                            "chunk_b64": base64.b64encode(bytes(audio_bytes)).decode("ascii"),
                        }
                        is_open = await _send_ws_event(websocket, payload)
                        if not is_open:
                            break
                    continue

                is_open = await _send_ws_event(websocket, event)
                if not is_open:
                    break

            with contextlib.suppress(Exception):
                await worker_task

            if not is_open:
                return
    finally:
        if runtime is not None:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(close_agent_session, runtime)
