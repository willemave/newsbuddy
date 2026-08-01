"""Admin web authentication routes."""

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse

from app.admin_web.templates import templates
from app.core.deps import ADMIN_SESSION_COOKIE
from app.core.security import create_admin_session_token, verify_admin_password
from app.core.settings import get_settings
from app.models.api.auth import AdminLoginRequest, AdminLoginResponse

settings = get_settings()
router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request) -> HTMLResponse:
    """Render admin login page."""
    response = templates.TemplateResponse(request, "login.html", {"request": request})
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return response


@router.post("/admin/login", response_model=AdminLoginResponse)
def admin_login(request: AdminLoginRequest, response: Response) -> AdminLoginResponse:
    """Admin login with password."""
    if not verify_admin_password(request.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin password"
        )

    session_token = create_admin_session_token()
    is_production = settings.environment.lower() == "production"
    response.set_cookie(
        key=ADMIN_SESSION_COOKIE,
        value=session_token,
        httponly=True,
        max_age=settings.admin_session_expire_minutes * 60,
        samesite="lax",
        secure=is_production,
    )
    return AdminLoginResponse(message="Logged in as admin")


@router.post("/admin/logout")
def admin_logout(response: Response) -> dict[str, str]:
    """Admin logout."""
    response.delete_cookie(key=ADMIN_SESSION_COOKIE)
    return {"message": "Logged out"}
