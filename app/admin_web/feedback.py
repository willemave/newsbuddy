"""Admin user feedback view."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.admin_web.formatting import format_user_label
from app.admin_web.templates import templates
from app.core.db import get_readonly_db_session
from app.core.deps import require_admin
from app.models.db import UserFeedback
from app.models.db.users import User

router = APIRouter(tags=["admin"])


def _build_feedback_rows(db: Session) -> list[dict[str, Any]]:
    """Return recent user feedback rows for admin review."""
    rows = (
        db.query(
            UserFeedback,
            User.email.label("email"),
            User.full_name.label("full_name"),
        )
        .outerjoin(User, User.id == UserFeedback.user_id)
        .order_by(UserFeedback.created_at.desc())
        .limit(200)
        .all()
    )
    return [
        {
            "user_id": feedback.user_id,
            "user_label": format_user_label(feedback.user_id, email, full_name),
            "message": feedback.message,
            "source": feedback.source,
            "app_version": feedback.app_version,
            "build_number": feedback.build_number,
            "platform": feedback.platform,
            "os_version": feedback.os_version,
            "device_model": feedback.device_model,
            "created_at": feedback.created_at,
        }
        for feedback, email, full_name in rows
    ]


@router.get("/feedback", response_class=HTMLResponse)
def admin_feedback_page(
    request: Request,
    db: Annotated[Session, Depends(get_readonly_db_session)],
    _: None = Depends(require_admin),
) -> HTMLResponse:
    """Render recent user feedback."""
    return templates.TemplateResponse(
        request,
        "feedback.html",
        {
            "request": request,
            "feedback_rows": _build_feedback_rows(db),
        },
    )
