"""Authentication endpoints."""

import secrets
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.constants import ALLOWED_NEWS_DIGEST_INTERVAL_HOURS
from app.core.db import get_db_session
from app.core.deps import ADMIN_SESSION_COOKIE, get_current_user
from app.core.logging import get_logger
from app.core.observability import build_log_extra
from app.core.security import (
    create_access_token,
    create_refresh_token,
    verify_admin_password,
    verify_apple_token,
    verify_token,
)
from app.core.settings import get_settings
from app.models.user import (
    AccessTokenResponse,
    AdminLoginRequest,
    AdminLoginResponse,
    AppleSignInRequest,
    RefreshTokenRequest,
    TokenResponse,
    UpdateUserProfileRequest,
    User,
    UserResponse,
)
from app.services.news_digest_preferences import (
    normalize_news_digest_preference_prompt,
    resolve_user_news_digest_preference_prompt,
)
from app.services.x_integration import has_active_x_connection, normalize_twitter_username
from app.templates import templates

logger = get_logger(__name__)
settings = get_settings()

router = APIRouter()

# PRODUCTION WARNING - IN-MEMORY SESSION STORAGE:
# This in-memory set stores admin session tokens. This has critical limitations:
#
# PROBLEMS WITH CURRENT IMPLEMENTATION:
# 1. Sessions lost on application restart - all admins logged out
# 2. Does not work with multiple server instances - sessions only valid on one server
# 3. No session expiry mechanism - sessions live forever until server restart
# 4. No ability to revoke sessions or view active sessions
# 5. Memory leak potential if sessions accumulate
#
# BEFORE PRODUCTION DEPLOYMENT - MUST FIX:
# Option 1: Redis (recommended for distributed systems)
#   - Use Redis with TTL for automatic expiry
#   - Works across multiple server instances
#   - Fast session validation
#
# Option 2: Database sessions
#   - Store sessions in database with expiry timestamp
#   - Works across server instances
#   - Can track login history and revoke sessions
#
# This implementation is suitable ONLY for single-instance development/MVP.
admin_sessions = set()


def _build_user_response(db: Session, user: User) -> UserResponse:
    has_sync = has_active_x_connection(db, user.id)
    response = UserResponse.model_validate(user)
    return response.model_copy(
        update={
            "has_x_bookmark_sync": has_sync,
            "news_digest_preference_prompt": resolve_user_news_digest_preference_prompt(user),
        }
    )


def _normalize_news_digest_timezone(timezone_name: str | None) -> str | None:
    """Normalize and validate an IANA timezone string.

    Args:
        timezone_name: Raw timezone string from client payload.

    Returns:
        Normalized timezone string, or ``None`` when not provided.

    Raises:
        ValueError: If timezone is invalid.
    """
    if timezone_name is None:
        return None

    candidate = timezone_name.strip()
    if not candidate:
        return "UTC"

    try:
        ZoneInfo(candidate)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Invalid timezone: {candidate}") from exc

    return candidate


def _normalize_news_digest_interval_hours(interval_hours: int | None) -> int | None:
    """Validate digest interval checkpoint hours."""
    if interval_hours is None:
        return None
    if interval_hours not in ALLOWED_NEWS_DIGEST_INTERVAL_HOURS:
        allowed_values = ", ".join(str(option) for option in ALLOWED_NEWS_DIGEST_INTERVAL_HOURS)
        raise ValueError(
            f"Invalid digest interval hours: {interval_hours}. Allowed: {allowed_values}"
        )
    return interval_hours


@router.post("/apple", response_model=TokenResponse)
def apple_signin(
    request: AppleSignInRequest, db: Annotated[Session, Depends(get_db_session)]
) -> TokenResponse:
    """
    Authenticate with Apple Sign In.

    Creates new user if first time, otherwise logs in existing user.

    Args:
        request: Apple Sign In request with id_token, email, and optional full_name
        db: Database session

    Returns:
        Access token, refresh token, and user data

    Raises:
        HTTPException: 401 if Apple token is invalid
    """
    # Verify Apple identity token
    try:
        apple_claims = verify_apple_token(request.id_token)

        apple_id = apple_claims.get("sub")

        if not apple_id:
            logger.error(
                "Apple sign-in token missing subject",
                extra=build_log_extra(
                    component="auth",
                    operation="apple_signin",
                    event_name="auth.apple_signin",
                    status="failed",
                    context_data={"failure_class": "missing_subject"},
                ),
            )
            raise ValueError("Missing subject in token")

    except (ValueError, Exception) as e:
        logger.exception(
            "Apple sign-in token verification failed",
            extra=build_log_extra(
                component="auth",
                operation="apple_signin",
                event_name="auth.apple_signin",
                status="failed",
                context_data={"failure_class": type(e).__name__},
            ),
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid Apple token: {str(e)}"
        ) from e

    # Extract email from token if not provided or empty
    email = request.email
    if not email or email.strip() == "":
        email = apple_claims.get("email")

    if not email:
        logger.error(
            "Apple sign-in missing email",
            extra=build_log_extra(
                component="auth",
                operation="apple_signin",
                event_name="auth.apple_signin",
                status="failed",
                context_data={"failure_class": "missing_email"},
            ),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email is required but not found in request or Apple token",
        )

    # Extract full_name from token if not provided or empty
    full_name = request.full_name
    if not full_name or full_name.strip() == "":
        # Apple sometimes provides name in the token
        token_name = apple_claims.get("name")
        if token_name:
            # Token name might be a dict like {"firstName": "John", "lastName": "Doe"}
            if isinstance(token_name, dict):
                first = token_name.get("firstName", "")
                last = token_name.get("lastName", "")
                full_name = f"{first} {last}".strip()
            else:
                full_name = token_name
        else:
            full_name = None

    # Check if user already exists
    user = db.query(User).filter(User.apple_id == apple_id).first()

    is_new_user = False
    if user is None:
        # Create new user
        user = User(
            apple_id=apple_id,
            email=email,
            full_name=full_name if full_name else None,
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        is_new_user = True

    # Generate tokens
    access_token = create_access_token(user.id)
    refresh_token = create_refresh_token(user.id)
    logger.info(
        "Apple sign-in completed",
        extra=build_log_extra(
            component="auth",
            operation="apple_signin",
            event_name="auth.apple_signin",
            status="completed",
            user_id=user.id,
            context_data={
                "auth_method": "apple",
                "is_new_user": is_new_user,
            },
        ),
    )

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=_build_user_response(db, user),
        is_new_user=is_new_user,
    )


@router.post("/debug/new-user", response_model=TokenResponse)
def debug_create_user(
    db: Annotated[Session, Depends(get_db_session)],
) -> TokenResponse:
    """Create a debug user session (debug mode only)."""
    is_development_env = settings.environment.lower() == "development"
    if not (settings.debug or is_development_env):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    apple_id = f"debug_{secrets.token_urlsafe(16)}"
    email = f"debug+{secrets.token_urlsafe(8)}@example.com"
    user = User(
        apple_id=apple_id,
        email=email,
        full_name="Debug User",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    access_token = create_access_token(user.id)
    refresh_token = create_refresh_token(user.id)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=_build_user_response(db, user),
        is_new_user=True,
    )


@router.post("/refresh", response_model=AccessTokenResponse)
def refresh_token(
    request: RefreshTokenRequest, db: Annotated[Session, Depends(get_db_session)]
) -> AccessTokenResponse:
    """
    Refresh access token using refresh token.

    Implements refresh token rotation for enhanced security:
    - Issues new access token (30 min expiry)
    - Issues new refresh token (90 day expiry)
    - Old refresh token is invalidated (client should discard)

    This ensures active users stay logged in indefinitely while
    maintaining security through token rotation.

    Args:
        request: Refresh token request
        db: Database session

    Returns:
        New access token and new refresh token

    Raises:
        HTTPException: 401 if refresh token is invalid
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
    )

    try:
        payload = verify_token(request.refresh_token)
        user_id: str = payload.get("sub")
        token_type: str = payload.get("type")

        if user_id is None or token_type != "refresh":
            raise credentials_exception

    except jwt.InvalidTokenError:
        raise credentials_exception from None

    # Verify user exists and is active
    user = db.query(User).filter(User.id == int(user_id)).first()

    if user is None or not user.is_active:
        raise credentials_exception

    # Generate new access token AND new refresh token (token rotation)
    access_token = create_access_token(user.id)
    new_refresh_token = create_refresh_token(user.id)

    logger.info(
        "Token refresh completed",
        extra=build_log_extra(
            component="auth",
            operation="refresh_token",
            event_name="auth.refresh_token",
            status="completed",
            user_id=user.id,
            context_data={"auth_method": "refresh_token"},
        ),
    )

    return AccessTokenResponse(
        access_token=access_token,
        refresh_token=new_refresh_token,
    )


@router.get("/me", response_model=UserResponse)
def get_current_user_info(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db_session)],
) -> UserResponse:
    """
    Get current authenticated user information.

    Args:
        current_user: Current authenticated user from JWT token

    Returns:
        Current user information

    Raises:
        HTTPException: 401 if token is invalid
    """
    try:
        return _build_user_response(db, current_user)
    except ValidationError as exc:
        if not _is_email_validation_error(exc):
            raise
        user = _repair_invalid_email(db, current_user, exc.errors())
        return _build_user_response(db, user)


@router.patch("/me", response_model=UserResponse)
def update_current_user_info(
    payload: UpdateUserProfileRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db_session)],
) -> UserResponse:
    """Update authenticated user's profile fields."""
    if payload.full_name is not None:
        cleaned_full_name = payload.full_name.strip()
        current_user.full_name = cleaned_full_name or None

    if payload.twitter_username is not None:
        try:
            current_user.twitter_username = normalize_twitter_username(payload.twitter_username)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if payload.news_digest_timezone is not None:
        try:
            current_user.news_digest_timezone = _normalize_news_digest_timezone(
                payload.news_digest_timezone
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if payload.news_digest_interval_hours is not None:
        try:
            current_user.news_digest_interval_hours = _normalize_news_digest_interval_hours(
                payload.news_digest_interval_hours
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if payload.news_digest_preference_prompt is not None:
        current_user.news_digest_preference_prompt = normalize_news_digest_preference_prompt(
            payload.news_digest_preference_prompt
        )

    db.commit()
    db.refresh(current_user)
    return _build_user_response(db, current_user)


def _is_email_validation_error(exc: ValidationError) -> bool:
    return any(error.get("loc") == ("email",) for error in exc.errors())


def _repair_invalid_email(
    db: Session,
    current_user: User,
    errors: list[dict[str, object]],
) -> User:
    local_part = f"user{current_user.id}"
    email = (current_user.email or "").strip()
    original_email = email
    if email:
        local = email.split("@", 1)[0].strip()
        if local:
            local_part = f"{local}+{current_user.id}"

    current_user.email = f"{local_part}@example.com"
    db.commit()
    db.refresh(current_user)

    logger.error(
        "Repaired invalid user email",
        extra={
            "component": "auth",
            "operation": "repair_email",
            "item_id": str(current_user.id),
            "context_data": {
                "errors": errors,
                "email": current_user.email,
                "original_email": original_email,
            },
        },
    )
    return current_user


@router.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request) -> HTMLResponse:
    """
    Render admin login page.

    Args:
        request: FastAPI request object

    Returns:
        HTML login page
    """
    return templates.TemplateResponse(request, "admin_login.html", {"request": request})


@router.post("/admin/login", response_model=AdminLoginResponse)
def admin_login(request: AdminLoginRequest, response: Response) -> AdminLoginResponse:
    """
    Admin login with password.

    Args:
        request: Admin login request with password
        response: FastAPI response to set cookie

    Returns:
        Success message

    Raises:
        HTTPException: 401 if password is incorrect
    """
    if not verify_admin_password(request.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin password"
        )

    # Generate session token
    session_token = secrets.token_urlsafe(32)
    admin_sessions.add(session_token)

    # Set httpOnly cookie
    response.set_cookie(
        key=ADMIN_SESSION_COOKIE,
        value=session_token,
        httponly=True,
        max_age=7 * 24 * 60 * 60,  # 7 days
        samesite="lax",
    )

    return AdminLoginResponse(message="Logged in as admin")


@router.post("/admin/logout")
def admin_logout(response: Response) -> dict:
    """
    Admin logout.

    Args:
        response: FastAPI response to delete cookie

    Returns:
        Success message
    """
    # Delete cookie
    response.delete_cookie(key=ADMIN_SESSION_COOKIE)

    return {"message": "Logged out"}
