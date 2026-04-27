"""Tests for admin authentication dependency."""

from datetime import timedelta
from unittest.mock import MagicMock, Mock

import pytest
from fastapi import Request

from app.core.deps import AdminAuthRequired, require_admin
from app.core.security import create_admin_session_token


def test_require_admin_valid_session():
    """Test require_admin with valid admin session."""
    # Create mock request with valid session cookie
    mock_request = Mock(spec=Request)
    mock_request.cookies = {"admin_session": create_admin_session_token()}

    # Create mock db session
    mock_db = MagicMock()
    mock_admin_user = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = mock_admin_user

    # Should not raise, should return admin user
    result = require_admin(mock_request, mock_db)
    assert result == mock_admin_user


def test_require_admin_no_cookie():
    """Test require_admin without session cookie."""
    mock_request = Mock(spec=Request)
    mock_request.cookies = {}
    mock_request.url.path = "/admin/dashboard"

    mock_db = MagicMock()

    with pytest.raises(AdminAuthRequired) as exc_info:
        require_admin(mock_request, mock_db)

    assert "/auth/admin/login" in exc_info.value.redirect_url


def test_require_admin_invalid_session():
    """Test require_admin with invalid session token."""
    mock_request = Mock(spec=Request)
    mock_request.cookies = {"admin_session": "invalid_token"}
    mock_request.url.path = "/admin/dashboard"

    mock_db = MagicMock()

    with pytest.raises(AdminAuthRequired) as exc_info:
        require_admin(mock_request, mock_db)

    assert "/auth/admin/login" in exc_info.value.redirect_url


def test_require_admin_expired_session():
    """Test require_admin rejects expired session tokens."""
    from app.core.security import create_token

    mock_request = Mock(spec=Request)
    mock_request.cookies = {
        "admin_session": create_token(0, "admin_session", timedelta(seconds=-1)),
    }
    mock_request.url.path = "/admin/dashboard"

    mock_db = MagicMock()

    with pytest.raises(AdminAuthRequired) as exc_info:
        require_admin(mock_request, mock_db)

    assert "/auth/admin/login" in exc_info.value.redirect_url
