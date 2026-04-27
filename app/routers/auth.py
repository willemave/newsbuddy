"""Authentication endpoints."""

import secrets
from collections.abc import Mapping, Sequence
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.db import get_db_session
from app.core.deps import ADMIN_SESSION_COOKIE, get_current_user, require_user_id
from app.core.logging import get_logger
from app.core.observability import build_log_extra
from app.core.security import (
    create_access_token,
    create_admin_session_token,
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
    CouncilPersonaConfig,
    DebugUserSessionRequest,
    RefreshTokenRequest,
    TokenResponse,
    UpdateUserProfileRequest,
    User,
    UserResponse,
    resolve_user_council_personas,
)
from app.services.news_list_preferences import (
    normalize_news_list_preference_prompt,
    resolve_user_news_list_preference_prompt,
)
from app.services.x_integration import has_active_x_connection, normalize_twitter_username
from app.templates import templates

logger = get_logger(__name__)
settings = get_settings()

router = APIRouter()


def _build_user_response(db: Session, user: User) -> UserResponse:
    has_sync = has_active_x_connection(db, require_user_id(user))
    response = UserResponse.model_validate(user)
    return response.model_copy(
        update={
            "has_x_bookmark_sync": has_sync,
            "news_list_preference_prompt": resolve_user_news_list_preference_prompt(user),
            "council_personas": resolve_user_council_personas(user),
        }
    )


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
    user_id = require_user_id(user)
    access_token = create_access_token(user_id)
    refresh_token = create_refresh_token(user_id)
    logger.info(
        "Apple sign-in completed",
        extra=build_log_extra(
            component="auth",
            operation="apple_signin",
            event_name="auth.apple_signin",
            status="completed",
            user_id=user_id,
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
    request: DebugUserSessionRequest | None = None,
) -> TokenResponse:
    """Create a debug user session (debug mode only)."""
    is_development_env = settings.environment.lower() == "development"
    if not (settings.debug or is_development_env):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    payload = request or DebugUserSessionRequest()

    if payload.user_id is not None:
        user = db.query(User).filter(User.id == payload.user_id).first()
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Debug user not found",
            )
        is_new_user = False
    else:
        apple_id = f"debug_{secrets.token_urlsafe(16)}"
        email = f"debug+{secrets.token_urlsafe(8)}@example.com"
        user = User(
            apple_id=apple_id,
            email=email,
            full_name="Debug User",
            is_active=True,
        )
        db.add(user)
        db.flush()
        is_new_user = True

    if payload.has_completed_onboarding is not None:
        user.has_completed_onboarding = payload.has_completed_onboarding
    if payload.has_completed_new_user_tutorial is not None:
        user.has_completed_new_user_tutorial = payload.has_completed_new_user_tutorial

    db.commit()
    db.refresh(user)

    user_id = require_user_id(user)
    access_token = create_access_token(user_id)
    refresh_token = create_refresh_token(user_id)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=_build_user_response(db, user),
        is_new_user=is_new_user,
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
        user_id = payload.get("sub")
        token_type = payload.get("type")

        if not isinstance(user_id, str) or not isinstance(token_type, str):
            raise credentials_exception
        if token_type != "refresh":
            raise credentials_exception

    except jwt.InvalidTokenError:
        raise credentials_exception from None

    # Verify user exists and is active
    user = db.query(User).filter(User.id == int(user_id)).first()

    if user is None or not user.is_active:
        raise credentials_exception

    # Generate new access token AND new refresh token (token rotation)
    current_user_id = require_user_id(user)
    access_token = create_access_token(current_user_id)
    new_refresh_token = create_refresh_token(current_user_id)

    logger.info(
        "Token refresh completed",
        extra=build_log_extra(
            component="auth",
            operation="refresh_token",
            event_name="auth.refresh_token",
            status="completed",
            user_id=current_user_id,
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

    if payload.news_list_preference_prompt is not None:
        current_user.news_list_preference_prompt = normalize_news_list_preference_prompt(
            payload.news_list_preference_prompt
        )

    if payload.council_personas is not None:
        current_user.council_personas = [
            CouncilPersonaConfig.model_validate(persona).model_dump(mode="json")
            for persona in payload.council_personas
        ]

    db.commit()
    db.refresh(current_user)
    return _build_user_response(db, current_user)


def _is_email_validation_error(exc: ValidationError) -> bool:
    return any(error.get("loc") == ("email",) for error in exc.errors())


def _repair_invalid_email(
    db: Session,
    current_user: User,
    errors: Sequence[Mapping[str, object]],
) -> User:
    user_id = require_user_id(current_user)
    local_part = f"user{user_id}"
    email = (current_user.email or "").strip()
    original_email = email
    if email:
        local = email.split("@", 1)[0].strip()
        if local:
            local_part = f"{local}+{user_id}"

    current_user.email = f"{local_part}@example.com"
    db.commit()
    db.refresh(current_user)

    logger.error(
        "Repaired invalid user email",
        extra={
            "component": "auth",
            "operation": "repair_email",
            "item_id": str(user_id),
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

    session_token = create_admin_session_token()
    is_production = settings.environment.lower() == "production"

    # Set httpOnly cookie
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
