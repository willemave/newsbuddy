"""Admin API key management views."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.admin_web.templates import templates
from app.commands import create_api_key, revoke_api_key
from app.core.db import get_db_session
from app.core.deps import require_admin
from app.models.db.users import User
from app.queries import list_api_keys

router = APIRouter(tags=["admin"])


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
        "api_keys.html",
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
        "api_keys.html",
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
