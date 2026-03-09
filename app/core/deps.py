"""FastAPI dependencies for authentication and authorization."""

from typing import Annotated
from urllib.parse import quote

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.db import get_db_session as get_db
from app.core.security import verify_token
from app.infrastructure.security.api_keys import is_api_key_token
from app.models.schema import UserApiKey
from app.models.user import User
from app.repositories.api_key_repository import find_active_api_key_by_token, touch_last_used

# HTTP Bearer token scheme for JWT authentication
security = HTTPBearer(auto_error=False)
optional_security = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    """
    Get current authenticated user from JWT token.

    Args:
        credentials: HTTP Bearer credentials from Authorization header
        db: Database session

    Returns:
        Current authenticated user

    Raises:
        HTTPException: 401 if token is invalid or user not found
        HTTPException: 400 if user is inactive
    """
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authenticated")

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        token = credentials.credentials
        user: User | None = None

        if is_api_key_token(token):
            api_key: UserApiKey | None = find_active_api_key_by_token(db, raw_key=token)
            if api_key is None:
                raise credentials_exception
            user = db.query(User).filter(User.id == int(api_key.user_id)).first()
            if api_key.id is not None:
                touch_last_used(db, api_key_id=int(api_key.id))
        else:
            payload = verify_token(token)
            user_id: str = payload.get("sub")
            token_type: str = payload.get("type")

            if user_id is None or token_type != "access":
                raise credentials_exception
            user = db.query(User).filter(User.id == int(user_id)).first()

    except jwt.InvalidTokenError:
        raise credentials_exception from None

    if user is None:
        raise credentials_exception

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Inactive user")

    return user


def get_optional_user(
    db: Annotated[Session, Depends(get_db)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(optional_security)],
) -> User | None:
    """
    Get current user if authenticated, None otherwise.

    Args:
        db: Database session
        credentials: Optional HTTP Bearer credentials

    Returns:
        User if authenticated, None otherwise
    """
    if credentials is None:
        return None

    try:
        return get_current_user(credentials, db)
    except HTTPException:
        return None


ADMIN_SESSION_COOKIE = "admin_session"
ADMIN_EMAIL = "admin@system.local"


class AdminAuthRequired(Exception):
    """Exception raised when admin authentication is required."""

    def __init__(self, redirect_url: str):
        self.redirect_url = redirect_url


def get_or_create_admin_user(db: Session) -> User:
    """
    Get or create the system admin user for web UI operations.

    Args:
        db: Database session

    Returns:
        Admin user instance
    """
    admin = db.query(User).filter(User.email == ADMIN_EMAIL).first()
    if admin is None:
        admin = User(
            apple_id="system-admin",
            email=ADMIN_EMAIL,
            full_name="System Admin",
            is_admin=True,
            is_active=True,
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
    return admin


def require_admin(request: Request, db: Annotated[Session, Depends(get_db)]) -> User:
    """
    Require admin authentication via session cookie.

    Args:
        request: FastAPI request object
        db: Database session

    Returns:
        Admin user instance

    Raises:
        AdminAuthRequired: If not authenticated, redirects to login page
    """
    from app.routers.auth import admin_sessions

    admin_session = request.cookies.get(ADMIN_SESSION_COOKIE)

    if not admin_session or admin_session not in admin_sessions:
        # Build redirect URL with next parameter
        next_url = quote(str(request.url.path), safe="")
        raise AdminAuthRequired(redirect_url=f"/auth/admin/login?next={next_url}")

    return get_or_create_admin_user(db)
