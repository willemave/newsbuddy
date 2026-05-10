"""Admin insight report controls."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.admin_web.templates import templates
from app.core.db import get_readonly_db_session
from app.core.deps import require_admin
from app.models.contracts import TaskStatus, TaskType
from app.models.db import ProcessingTask
from app.models.db.users import User
from app.services.insight_report import (
    DEFAULT_MIN_SAVES_FOR_TRIGGER,
    SYNTHESIS_EFFORT,
    SYNTHESIS_MODEL,
    count_knowledge_saves_since,
    last_insight_report_for_user,
)
from app.services.queue import get_queue_service

router = APIRouter(tags=["admin"])


def _build_insight_report_rows(db: Session) -> list[dict[str, Any]]:
    """Assemble per-user insight-report stats for the admin panel."""
    rows: list[dict[str, Any]] = []
    users = db.query(User).order_by(User.email.asc()).all()
    for user in users:
        if user.id is None:
            continue
        user_id = int(user.id)
        last_report = last_insight_report_for_user(db, user_id=user_id)
        last_report_content_id = last_report[0] if last_report else None
        last_at = last_report[1] if last_report else None
        new_saves = count_knowledge_saves_since(db, user_id=user_id, since=last_at)
        total_saves = count_knowledge_saves_since(db, user_id=user_id, since=None)
        pending_task = (
            db.query(ProcessingTask.id)
            .filter(ProcessingTask.task_type == TaskType.GENERATE_INSIGHT_REPORT.value)
            .filter(
                ProcessingTask.status.in_((TaskStatus.PENDING.value, TaskStatus.PROCESSING.value))
            )
            .filter(ProcessingTask.payload["user_id"].as_integer() == user_id)
            .order_by(ProcessingTask.id.desc())
            .first()
        )
        rows.append(
            {
                "user_id": user_id,
                "email": user.email,
                "is_active": user.is_active,
                "total_saves": total_saves,
                "new_saves": new_saves,
                "last_report_at": last_at,
                "last_report_content_id": last_report_content_id,
                "pending_task_id": pending_task[0] if pending_task else None,
                "eligible": (user.is_active and new_saves >= DEFAULT_MIN_SAVES_FOR_TRIGGER),
            }
        )
    return rows


@router.get("/insight-reports", response_class=HTMLResponse)
def admin_insight_reports_page(
    request: Request,
    db: Annotated[Session, Depends(get_readonly_db_session)],
    admin_user: Annotated[User, Depends(require_admin)],
) -> HTMLResponse:
    """Admin panel: per-user insight report eligibility + manual trigger."""
    rows = _build_insight_report_rows(db)
    return templates.TemplateResponse(
        request,
        "insight_reports.html",
        {
            "request": request,
            "admin_user": admin_user,
            "rows": rows,
            "min_saves_threshold": DEFAULT_MIN_SAVES_FOR_TRIGGER,
            "synthesis_model": SYNTHESIS_MODEL,
            "effort": SYNTHESIS_EFFORT,
        },
    )


@router.post("/insight-reports/trigger")
def admin_insight_reports_trigger(
    user_id: Annotated[int, Form(...)],
    _: None = Depends(require_admin),
    synthesis_model: Annotated[str, Form()] = SYNTHESIS_MODEL,
    effort: Annotated[str, Form()] = SYNTHESIS_EFFORT,
) -> RedirectResponse:
    """Manually enqueue a ``generate_insight_report`` task for a single user."""
    queue_service = get_queue_service()
    queue_service.enqueue(
        TaskType.GENERATE_INSIGHT_REPORT,
        payload={
            "user_id": user_id,
            "synthesis_model": synthesis_model,
            "effort": effort,
            "triggered_by": "admin",
        },
        dedupe=True,
        dedupe_key=f"insight_report|user:{user_id}|manual",
    )
    return RedirectResponse(url="/admin/insight-reports", status_code=303)
