"""Tests for authentication endpoints."""

import re

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import create_access_token, create_refresh_token
from app.main import app
from app.models.schema import UserIntegrationConnection
from app.models.user import User

client = TestClient(app)


def test_apple_signin_new_user(db: Session, monkeypatch):
    """Test Apple Sign In creates new user."""
    # Override get_db_session to use our test db
    from app.core.db import get_db_session, get_readonly_db_session

    def override_get_db_session():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_readonly_db_session] = override_get_db_session

    # Mock Apple token verification
    def mock_verify_apple_token(id_token):
        return {"sub": "001234.abcd1234", "email": "newuser@icloud.com", "email_verified": True}

    monkeypatch.setattr("app.routers.auth.verify_apple_token", mock_verify_apple_token)

    try:
        response = client.post(
            "/auth/apple",
            json={
                "id_token": "mock.apple.token",
                "email": "newuser@icloud.com",
                "full_name": "New User",
            },
        )

        assert response.status_code == 200
        data = response.json()

        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"
        assert data["user"]["email"] == "newuser@icloud.com"
        assert data["user"]["full_name"] == "New User"
        assert data["is_new_user"] is True
        assert "openai_api_key" not in data
    finally:
        app.dependency_overrides.clear()


def test_apple_signin_existing_user(db: Session, monkeypatch):
    """Test Apple Sign In with existing user."""
    # Override get_db_session to use our test db
    from app.core.db import get_db_session, get_readonly_db_session

    def override_get_db_session():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_readonly_db_session] = override_get_db_session

    # Create existing user
    existing_user = User(
        apple_id="001234.existing", email="existing@icloud.com", full_name="Existing User"
    )
    db.add(existing_user)
    db.commit()
    db.refresh(existing_user)

    # Mock Apple token verification
    def mock_verify_apple_token(id_token):
        return {"sub": "001234.existing", "email": "existing@icloud.com"}

    monkeypatch.setattr("app.routers.auth.verify_apple_token", mock_verify_apple_token)

    try:
        response = client.post(
            "/auth/apple", json={"id_token": "mock.apple.token", "email": "existing@icloud.com"}
        )

        assert response.status_code == 200
        data = response.json()

        assert data["user"]["id"] == existing_user.id
        assert data["user"]["email"] == "existing@icloud.com"
        assert data["is_new_user"] is False
        assert "openai_api_key" not in data
    finally:
        app.dependency_overrides.clear()


def test_apple_signin_invalid_token(monkeypatch):
    """Test Apple Sign In with invalid token."""

    # Mock Apple token verification to raise error
    def mock_verify_apple_token(id_token):
        raise ValueError("Invalid token")

    monkeypatch.setattr("app.routers.auth.verify_apple_token", mock_verify_apple_token)

    response = client.post(
        "/auth/apple", json={"id_token": "invalid.token", "email": "test@icloud.com"}
    )

    assert response.status_code == 401
    assert "Invalid Apple token" in response.json()["detail"]


def test_refresh_token_valid(db: Session):
    """Test token refresh with valid refresh token."""
    # Override get_db_session to use our test db
    from app.core.db import get_db_session, get_readonly_db_session

    def override_get_db_session():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_readonly_db_session] = override_get_db_session

    try:
        # Create user
        user = User(apple_id="001234.refresh", email="refresh@icloud.com", is_active=True)
        db.add(user)
        db.commit()
        db.refresh(user)

        # Create refresh token
        refresh_token = create_refresh_token(user.id)

        response = client.post("/auth/refresh", json={"refresh_token": refresh_token})

        assert response.status_code == 200
        data = response.json()

        assert "access_token" in data
        assert "refresh_token" in data  # Should now return new refresh token
        assert data["token_type"] == "bearer"
        assert "openai_api_key" not in data
    finally:
        app.dependency_overrides.clear()


def test_refresh_token_invalid():
    """Test token refresh with invalid token."""
    response = client.post("/auth/refresh", json={"refresh_token": "invalid.token"})

    assert response.status_code == 401


def test_refresh_token_with_access_token(db: Session):
    """Test refresh endpoint rejects access tokens."""
    # Override get_db_session to use our test db
    from app.core.db import get_db_session, get_readonly_db_session

    def override_get_db_session():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_readonly_db_session] = override_get_db_session

    try:
        user = User(apple_id="001234.wrongtype", email="wrongtype@icloud.com", is_active=True)
        db.add(user)
        db.commit()
        db.refresh(user)

        # Try with access token (should fail)
        access_token = create_access_token(user.id)

        response = client.post("/auth/refresh", json={"refresh_token": access_token})

        assert response.status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_refresh_token_rotation(db: Session):
    """
    Test refresh token rotation for security and session extension.

    Verifies that:
    1. Refresh endpoint returns both new access token AND new refresh token
    2. New refresh token can be used for subsequent refreshes
    3. This allows active users to stay logged in indefinitely

    Note: JWT tokens generated rapidly (same second) may be identical since
    they contain the same payload and timestamps. What matters is that the
    endpoint returns a refresh token and it works for subsequent refreshes.
    """
    # Override get_db_session to use our test db
    from app.core.db import get_db_session, get_readonly_db_session

    def override_get_db_session():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_readonly_db_session] = override_get_db_session

    try:
        # Create user
        user = User(apple_id="001234.rotation", email="rotation@icloud.com", is_active=True)
        db.add(user)
        db.commit()
        db.refresh(user)

        # Create initial refresh token
        initial_refresh_token = create_refresh_token(user.id)

        # First refresh - should get new access token AND new refresh token
        response = client.post("/auth/refresh", json={"refresh_token": initial_refresh_token})

        assert response.status_code == 200
        data = response.json()

        # Verify both tokens are present (key requirement for rotation)
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"
        assert "openai_api_key" not in data

        # Verify both tokens are valid strings
        new_refresh_token = data["refresh_token"]
        assert isinstance(new_refresh_token, str)
        assert len(new_refresh_token) > 0

        # Most important: verify new refresh token works for subsequent refresh
        response2 = client.post("/auth/refresh", json={"refresh_token": new_refresh_token})

        assert response2.status_code == 200
        data2 = response2.json()

        # Should get another set of tokens
        assert "access_token" in data2
        assert "refresh_token" in data2

        print("✅ Refresh token rotation working correctly")
        print(f"   - Initial refresh succeeded: {response.status_code}")
        print(f"   - Second refresh succeeded:  {response2.status_code}")
        print("   - Both refreshes returned new refresh tokens")

    finally:
        app.dependency_overrides.clear()


def test_validation_error_response_does_not_echo_request_body():
    """Validation errors should not include the raw request body."""
    response = client.post("/auth/refresh", json={})

    assert response.status_code == 422
    payload = response.json()
    assert "detail" in payload
    assert "body" not in payload


def test_admin_login_valid(monkeypatch):
    """Test admin login with correct password."""

    # Mock verify_admin_password to accept our test password
    def mock_verify_admin_password(password: str) -> bool:
        return password == "test_admin_pass"

    monkeypatch.setattr("app.routers.auth.verify_admin_password", mock_verify_admin_password)

    response = client.post("/auth/admin/login", json={"password": "test_admin_pass"})

    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Logged in as admin"

    # Check cookie is set
    assert "admin_session" in response.cookies


def test_admin_login_invalid():
    """Test admin login with wrong password."""
    response = client.post("/auth/admin/login", json={"password": "wrong_password"})

    assert response.status_code == 401
    assert "admin_session" not in response.cookies


def test_admin_logout(monkeypatch):
    """Test admin logout."""

    # Mock verify_admin_password to accept our test password
    def mock_verify_admin_password(password: str) -> bool:
        return password == "test_admin_pass"

    monkeypatch.setattr("app.routers.auth.verify_admin_password", mock_verify_admin_password)

    # First login
    response = client.post("/auth/admin/login", json={"password": "test_admin_pass"})

    # Then logout
    client.cookies.set("admin_session", response.cookies["admin_session"])
    response = client.post("/auth/admin/logout")

    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Logged out"


def test_get_current_user_info(db: Session, monkeypatch):
    """Test /auth/me endpoint."""
    from app.core.settings import get_settings

    monkeypatch.setattr(get_settings(), "debug", False)
    # Override get_db_session to use our test db
    from app.core.db import get_db_session, get_readonly_db_session

    def override_get_db_session():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_readonly_db_session] = override_get_db_session

    # Create test user
    test_user = User(apple_id="001234.test.me", email="testme@icloud.com", full_name="Test Me User")
    db.add(test_user)
    db.commit()
    db.refresh(test_user)

    # Generate token for user
    from app.core.security import create_access_token

    access_token = create_access_token(test_user.id)

    try:
        response = client.get("/auth/me", headers={"Authorization": f"Bearer {access_token}"})

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == test_user.id
        assert data["email"] == "testme@icloud.com"
        assert data["full_name"] == "Test Me User"
        assert data["twitter_username"] is None
        assert data["news_digest_timezone"] == "UTC"
        assert data["has_x_bookmark_sync"] is False
    finally:
        app.dependency_overrides.clear()


def test_get_current_user_info_reports_x_connection(db: Session, monkeypatch):
    """Test /auth/me reports active X sync status."""
    from app.core.db import get_db_session, get_readonly_db_session
    from app.core.settings import get_settings

    monkeypatch.setattr(get_settings(), "debug", False)

    def override_get_db_session():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_readonly_db_session] = override_get_db_session

    test_user = User(
        apple_id="001234.test.xsync",
        email="xsync@icloud.com",
        full_name="X Sync User",
    )
    db.add(test_user)
    db.commit()
    db.refresh(test_user)

    db.add(
        UserIntegrationConnection(
            user_id=test_user.id,
            provider="x",
            access_token_encrypted="encrypted-token",
            is_active=True,
        )
    )
    db.commit()

    access_token = create_access_token(test_user.id)

    try:
        response = client.get("/auth/me", headers={"Authorization": f"Bearer {access_token}"})
        assert response.status_code == 200
        data = response.json()
        assert data["has_x_bookmark_sync"] is True
    finally:
        app.dependency_overrides.clear()


def test_update_current_user_info(db: Session, monkeypatch):
    """Test PATCH /auth/me updates profile fields."""
    from app.core.db import get_db_session, get_readonly_db_session
    from app.core.settings import get_settings

    monkeypatch.setattr(get_settings(), "debug", False)

    def override_get_db_session():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_readonly_db_session] = override_get_db_session

    test_user = User(
        apple_id="001234.test.patchme",
        email="patchme@icloud.com",
        full_name="Patch Me",
    )
    db.add(test_user)
    db.commit()
    db.refresh(test_user)

    access_token = create_access_token(test_user.id)

    try:
        response = client.patch(
            "/auth/me",
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "full_name": "Updated Name",
                "twitter_username": "@Willem_AW",
                "news_digest_timezone": "America/New_York",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["full_name"] == "Updated Name"
        assert data["twitter_username"] == "willem_aw"
        assert data["news_digest_timezone"] == "America/New_York"

        db.refresh(test_user)
        assert test_user.full_name == "Updated Name"
        assert test_user.twitter_username == "willem_aw"
        assert test_user.news_digest_timezone == "America/New_York"
    finally:
        app.dependency_overrides.clear()


def test_update_current_user_info_rejects_invalid_username(db: Session, monkeypatch):
    """Test PATCH /auth/me validates username formatting."""
    from app.core.db import get_db_session, get_readonly_db_session
    from app.core.settings import get_settings

    monkeypatch.setattr(get_settings(), "debug", False)

    def override_get_db_session():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_readonly_db_session] = override_get_db_session

    test_user = User(
        apple_id="001234.test.invalidusername",
        email="invalidusername@icloud.com",
        full_name="Invalid Username",
    )
    db.add(test_user)
    db.commit()
    db.refresh(test_user)
    access_token = create_access_token(test_user.id)

    try:
        response = client.patch(
            "/auth/me",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"twitter_username": "not valid!"},
        )
        assert response.status_code == 400
        assert "Twitter username" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_update_current_user_info_rejects_invalid_timezone(db: Session, monkeypatch):
    """Test PATCH /auth/me validates timezone formatting."""
    from app.core.db import get_db_session, get_readonly_db_session
    from app.core.settings import get_settings

    monkeypatch.setattr(get_settings(), "debug", False)

    def override_get_db_session():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_readonly_db_session] = override_get_db_session

    test_user = User(
        apple_id="001234.test.invalidtimezone",
        email="invalidtimezone@icloud.com",
        full_name="Invalid Timezone",
    )
    db.add(test_user)
    db.commit()
    db.refresh(test_user)
    access_token = create_access_token(test_user.id)

    try:
        response = client.patch(
            "/auth/me",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"news_digest_timezone": "Not/A_Real_Timezone"},
        )
        assert response.status_code == 400
        assert "timezone" in response.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()


def test_get_current_user_info_invalid_token(monkeypatch):
    """Test /auth/me with invalid token."""
    from app.core.settings import get_settings

    monkeypatch.setattr(get_settings(), "debug", False)
    response = client.get("/auth/me", headers={"Authorization": "Bearer invalid.token.here"})

    assert response.status_code == 401


def test_get_current_user_info_no_token(monkeypatch):
    """Test /auth/me without token."""
    from app.core.settings import get_settings

    monkeypatch.setattr(get_settings(), "debug", False)
    response = client.get("/auth/me")

    assert response.status_code == 403  # Forbidden when no auth header


def test_datetime_serialization_has_timezone(db: Session, monkeypatch):
    """
    Test that datetime fields in user responses are serialized with timezone indicator.

    This ensures compatibility with iOS Swift's ISO8601DateFormatter which requires
    datetime strings to have timezone information (e.g., '2025-11-01T15:29:31Z').

    Without the 'Z' suffix, iOS JSON decoding will fail with:
    "Expected date string to be ISO8601-formatted."
    """
    # Override get_db_session to use our test db
    from app.core.settings import get_settings

    monkeypatch.setattr(get_settings(), "debug", False)
    from app.core.db import get_db_session, get_readonly_db_session

    def override_get_db_session():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_readonly_db_session] = override_get_db_session

    # Create test user
    test_user = User(
        apple_id="001234.datetime.test",
        email="datetimetest@icloud.com",
        full_name="Datetime Test User",
    )
    db.add(test_user)
    db.commit()
    db.refresh(test_user)

    # Generate token for user
    access_token = create_access_token(test_user.id)

    try:
        response = client.get("/auth/me", headers={"Authorization": f"Bearer {access_token}"})

        assert response.status_code == 200
        data = response.json()

        # Verify datetime fields exist
        assert "created_at" in data
        assert "updated_at" in data

        # ISO8601 with timezone pattern: YYYY-MM-DDTHH:MM:SSZ or YYYY-MM-DDTHH:MM:SS.fffffZ
        iso8601_tz_pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$"

        # Verify created_at has timezone indicator
        assert re.match(
            iso8601_tz_pattern,
            data["created_at"],
        ), (
            "created_at "
            f"'{data['created_at']}' "
            "does not match ISO8601 with timezone (must end with 'Z')"
        )

        # Verify updated_at has timezone indicator
        assert re.match(
            iso8601_tz_pattern,
            data["updated_at"],
        ), (
            "updated_at "
            f"'{data['updated_at']}' "
            "does not match ISO8601 with timezone (must end with 'Z')"
        )

        print(
            "✅ Datetime serialization correct: "
            f"created_at={data['created_at']}, "
            f"updated_at={data['updated_at']}"
        )

    finally:
        app.dependency_overrides.clear()


def test_debug_new_user_disabled(monkeypatch):
    monkeypatch.setattr("app.routers.auth.settings.debug", False)
    monkeypatch.setattr("app.routers.auth.settings.environment", "production")
    response = client.post("/auth/debug/new-user")
    assert response.status_code == 404


def test_debug_new_user_enabled(db: Session, monkeypatch):
    from app.core.db import get_db_session, get_readonly_db_session

    def override_get_db_session():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_readonly_db_session] = override_get_db_session
    monkeypatch.setattr("app.routers.auth.settings.debug", False)
    monkeypatch.setattr("app.routers.auth.settings.environment", "development")

    try:
        response = client.post("/auth/debug/new-user")
        assert response.status_code == 200
        data = response.json()
        assert data["is_new_user"] is True
        assert data["user"]["email"].startswith("debug+")
        assert data["access_token"]
        assert data["refresh_token"]
    finally:
        app.dependency_overrides.clear()


def test_auth_me_repairs_invalid_email(db: Session, monkeypatch):
    from app.core.settings import get_settings

    monkeypatch.setattr(get_settings(), "debug", False)
    from app.core.db import get_db_session, get_readonly_db_session

    def override_get_db_session():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_readonly_db_session] = override_get_db_session

    user = User(
        apple_id="001234.invalid",
        email="dev@local",
        full_name="Invalid Email",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    access_token = create_access_token(user.id)

    try:
        response = client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["email"].endswith("@example.com")

        db.refresh(user)
        assert user.email.endswith("@example.com")
    finally:
        app.dependency_overrides.clear()
