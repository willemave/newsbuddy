"""Tests for FastAPI dependencies."""

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, require_user_id
from app.core.security import create_access_token, create_refresh_token
from app.models.user import User


def test_require_user_id_returns_persisted_user_id(db: Session):
    user = User(apple_id="test.apple.require-user-id", email="id@example.com", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)

    assert require_user_id(user) == user.id


def test_require_user_id_rejects_transient_user() -> None:
    with pytest.raises(ValueError, match="Authenticated user is missing an id"):
        require_user_id(User(apple_id="transient", email="transient@example.com"))


def test_get_current_user_valid_token(db: Session):
    """Test get_current_user with valid token."""
    # Create test user
    user = User(
        apple_id="test.apple.001", email="test@example.com", full_name="Test User", is_active=True
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    assert user.id is not None

    # Create valid access token
    token = create_access_token(user.id)
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    # Get user from token
    result = get_current_user(credentials=credentials, db=db)

    assert result.id == user.id
    assert result.email == user.email


def test_get_current_user_invalid_token(db: Session):
    """Test get_current_user with invalid token."""
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="invalid.token.here")

    with pytest.raises(HTTPException) as exc_info:
        get_current_user(credentials=credentials, db=db)

    assert exc_info.value.status_code == 401
    assert "Could not validate credentials" in str(exc_info.value.detail)


def test_get_current_user_nonexistent_user(db: Session):
    """Test get_current_user with token for non-existent user."""
    # Create token for user ID that doesn't exist
    token = create_access_token(999999)
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    with pytest.raises(HTTPException) as exc_info:
        get_current_user(credentials=credentials, db=db)

    assert exc_info.value.status_code == 401


def test_get_current_user_inactive_user(db: Session):
    """Test get_current_user with inactive user."""
    # Create inactive user
    user = User(
        apple_id="test.apple.002",
        email="inactive@example.com",
        full_name="Inactive User",
        is_active=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    assert user.id is not None

    token = create_access_token(user.id)
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    with pytest.raises(HTTPException) as exc_info:
        get_current_user(credentials=credentials, db=db)

    assert exc_info.value.status_code == 400
    assert "Inactive user" in str(exc_info.value.detail)


def test_get_current_user_refresh_token(db: Session):
    """Test get_current_user rejects refresh token."""
    # Create user
    user = User(apple_id="test.apple.003", email="test3@example.com", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    assert user.id is not None

    # Try with refresh token (should fail)
    refresh_token = create_refresh_token(user.id)
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=refresh_token)

    with pytest.raises(HTTPException) as exc_info:
        get_current_user(credentials=credentials, db=db)

    assert exc_info.value.status_code == 401
