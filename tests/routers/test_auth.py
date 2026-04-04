"""Tests for authentication endpoints."""

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import create_access_token, create_refresh_token
from app.models.schema import UserIntegrationConnection
from app.models.user import build_default_council_personas
from app.services.news_list_preferences import DEFAULT_NEWS_LIST_PREFERENCE_PROMPT


@pytest.fixture
def auth_client(client_factory) -> TestClient:
    """Create a client for auth flows without overriding current_user."""
    with client_factory(authenticate=False) as test_client:
        yield test_client


@pytest.fixture
def production_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable debug mode for auth/me style endpoint tests."""
    from app.core.settings import get_settings

    monkeypatch.setattr(get_settings(), "debug", False)


def test_apple_signin_new_user(auth_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test Apple Sign In creates new user."""

    def mock_verify_apple_token(_id_token: str) -> dict[str, object]:
        return {"sub": "001234.abcd1234", "email": "newuser@icloud.com", "email_verified": True}

    monkeypatch.setattr("app.routers.auth.verify_apple_token", mock_verify_apple_token)

    response = auth_client.post(
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


def test_apple_signin_existing_user(
    auth_client: TestClient,
    db_session: Session,
    user_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test Apple Sign In with existing user."""
    existing_user = user_factory(
        apple_id="001234.existing",
        email="existing@icloud.com",
        full_name="Existing User",
    )

    def mock_verify_apple_token(_id_token: str) -> dict[str, str]:
        return {"sub": "001234.existing", "email": "existing@icloud.com"}

    monkeypatch.setattr("app.routers.auth.verify_apple_token", mock_verify_apple_token)

    response = auth_client.post(
        "/auth/apple",
        json={"id_token": "mock.apple.token", "email": "existing@icloud.com"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["user"]["id"] == existing_user.id
    assert data["user"]["email"] == "existing@icloud.com"
    assert data["is_new_user"] is False
    assert "openai_api_key" not in data


def test_apple_signin_invalid_token(
    auth_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test Apple Sign In with invalid token."""

    def mock_verify_apple_token(_id_token: str) -> None:
        raise ValueError("Invalid token")

    monkeypatch.setattr("app.routers.auth.verify_apple_token", mock_verify_apple_token)

    response = auth_client.post(
        "/auth/apple",
        json={"id_token": "invalid.token", "email": "test@icloud.com"},
    )

    assert response.status_code == 401
    assert "Invalid Apple token" in response.json()["detail"]


def test_debug_create_user_reuses_existing_user_and_updates_flags(
    auth_client: TestClient,
    test_user,
) -> None:
    """Debug auth can issue a session for a seeded test user."""
    response = auth_client.post(
        "/auth/debug/new-user",
        json={
            "user_id": test_user.id,
            "has_completed_onboarding": True,
            "has_completed_new_user_tutorial": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["user"]["id"] == test_user.id
    assert payload["user"]["has_completed_onboarding"] is True
    assert payload["user"]["has_completed_new_user_tutorial"] is True
    assert payload["is_new_user"] is False


def test_refresh_token_valid(
    auth_client: TestClient,
    user_factory,
) -> None:
    """Test token refresh with valid refresh token."""
    user = user_factory(
        apple_id="001234.refresh",
        email="refresh@icloud.com",
        is_active=True,
    )
    refresh_token = create_refresh_token(user.id)

    response = auth_client.post("/auth/refresh", json={"refresh_token": refresh_token})

    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"
    assert "openai_api_key" not in data


def test_refresh_token_invalid(auth_client: TestClient) -> None:
    """Test token refresh with invalid token."""
    response = auth_client.post("/auth/refresh", json={"refresh_token": "invalid.token"})
    assert response.status_code == 401


def test_refresh_token_with_access_token(
    auth_client: TestClient,
    user_factory,
) -> None:
    """Test refresh endpoint rejects access tokens."""
    user = user_factory(
        apple_id="001234.wrongtype",
        email="wrongtype@icloud.com",
        is_active=True,
    )
    access_token = create_access_token(user.id)

    response = auth_client.post("/auth/refresh", json={"refresh_token": access_token})
    assert response.status_code == 401


def test_refresh_token_rotation(
    auth_client: TestClient,
    user_factory,
) -> None:
    """Test refresh token rotation for security and session extension."""
    user = user_factory(
        apple_id="001234.rotation",
        email="rotation@icloud.com",
        is_active=True,
    )
    initial_refresh_token = create_refresh_token(user.id)

    response = auth_client.post("/auth/refresh", json={"refresh_token": initial_refresh_token})
    assert response.status_code == 200

    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"
    assert "openai_api_key" not in data

    response2 = auth_client.post("/auth/refresh", json={"refresh_token": data["refresh_token"]})
    assert response2.status_code == 200
    data2 = response2.json()
    assert "access_token" in data2
    assert "refresh_token" in data2


def test_validation_error_response_does_not_echo_request_body(
    auth_client: TestClient,
) -> None:
    """Validation errors should not include the raw request body."""
    response = auth_client.post("/auth/refresh", json={})

    assert response.status_code == 422
    payload = response.json()
    assert "detail" in payload
    assert "body" not in payload


def test_admin_login_valid(
    auth_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test admin login with correct password."""

    def mock_verify_admin_password(password: str) -> bool:
        return password == "test_admin_pass"

    monkeypatch.setattr("app.routers.auth.verify_admin_password", mock_verify_admin_password)

    response = auth_client.post("/auth/admin/login", json={"password": "test_admin_pass"})
    assert response.status_code == 200
    assert response.json()["message"] == "Logged in as admin"
    assert "admin_session" in response.cookies


def test_admin_login_invalid(auth_client: TestClient) -> None:
    """Test admin login with wrong password."""
    response = auth_client.post("/auth/admin/login", json={"password": "wrong_password"})
    assert response.status_code == 401
    assert "admin_session" not in response.cookies


def test_admin_logout(
    auth_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test admin logout."""

    def mock_verify_admin_password(password: str) -> bool:
        return password == "test_admin_pass"

    monkeypatch.setattr("app.routers.auth.verify_admin_password", mock_verify_admin_password)

    response = auth_client.post("/auth/admin/login", json={"password": "test_admin_pass"})
    auth_client.cookies.set("admin_session", response.cookies["admin_session"])

    logout_response = auth_client.post("/auth/admin/logout")
    assert logout_response.status_code == 200
    assert logout_response.json()["message"] == "Logged out"


def test_get_current_user_info(
    auth_client: TestClient,
    production_settings,
    user_factory,
    auth_headers_factory,
) -> None:
    """Test /auth/me endpoint."""
    test_user = user_factory(
        apple_id="001234.test.me",
        email="testme@icloud.com",
        full_name="Test Me User",
    )

    response = auth_client.get("/auth/me", headers=auth_headers_factory(test_user))

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == test_user.id
    assert data["email"] == "testme@icloud.com"
    assert data["full_name"] == "Test Me User"
    assert data["twitter_username"] is None
    assert data["news_list_preference_prompt"] == DEFAULT_NEWS_LIST_PREFERENCE_PROMPT
    assert data["council_personas"] == build_default_council_personas()
    assert data["has_x_bookmark_sync"] is False


def test_get_current_user_info_reports_x_connection(
    auth_client: TestClient,
    db_session: Session,
    production_settings,
    user_factory,
    auth_headers_factory,
) -> None:
    """Test /auth/me reports active X sync status."""
    test_user = user_factory(
        apple_id="001234.test.xsync",
        email="xsync@icloud.com",
        full_name="X Sync User",
    )
    db_session.add(
        UserIntegrationConnection(
            user_id=test_user.id,
            provider="x",
            access_token_encrypted="encrypted-token",
            is_active=True,
        )
    )
    db_session.commit()

    response = auth_client.get("/auth/me", headers=auth_headers_factory(test_user))
    assert response.status_code == 200
    assert response.json()["has_x_bookmark_sync"] is True


def test_update_current_user_info(
    auth_client: TestClient,
    db_session: Session,
    production_settings,
    user_factory,
    auth_headers_factory,
) -> None:
    """Test PATCH /auth/me updates profile fields."""
    test_user = user_factory(
        apple_id="001234.test.patchme",
        email="patchme@icloud.com",
        full_name="Patch Me",
    )

    response = auth_client.patch(
        "/auth/me",
        headers=auth_headers_factory(test_user),
        json={
            "full_name": "Updated Name",
            "twitter_username": "@Willem_AW",
            "council_personas": [
                {
                    "id": "einstein",
                    "display_name": "Albert Einstein",
                    "instruction_prompt": "Reduce the topic to first principles.",
                    "sort_order": 0,
                },
                {
                    "id": "turing",
                    "display_name": "Alan Turing",
                    "instruction_prompt": "Focus on computation, systems, and limits.",
                    "sort_order": 1,
                },
                {
                    "id": "hopper",
                    "display_name": "Grace Hopper",
                    "instruction_prompt": "Prefer practical engineering tradeoffs and clarity.",
                    "sort_order": 2,
                },
                {
                    "id": "munger",
                    "display_name": "Charlie Munger",
                    "instruction_prompt": "Stress incentives and second-order effects.",
                    "sort_order": 3,
                },
            ],
            "news_list_preference_prompt": "Prefer semiconductors, infrastructure, and AI models.",
        },
    )
    assert response.status_code == 200

    data = response.json()
    assert data["full_name"] == "Updated Name"
    assert data["twitter_username"] == "willem_aw"
    assert data["news_list_preference_prompt"].startswith("Prefer semiconductors")
    assert [persona["display_name"] for persona in data["council_personas"]] == [
        "Albert Einstein",
        "Alan Turing",
        "Grace Hopper",
        "Charlie Munger",
    ]

    db_session.refresh(test_user)
    assert test_user.full_name == "Updated Name"
    assert test_user.twitter_username == "willem_aw"
    assert test_user.news_list_preference_prompt.startswith("Prefer semiconductors")
    assert test_user.council_personas[0]["id"] == "einstein"


def test_update_current_user_info_rejects_invalid_council_persona_count(
    auth_client: TestClient,
    production_settings,
    user_factory,
    auth_headers_factory,
) -> None:
    """PATCH /auth/me should require exactly four council personas."""
    test_user = user_factory(
        apple_id="001234.test.invalidcouncil",
        email="invalidcouncil@icloud.com",
        full_name="Invalid Council",
    )

    response = auth_client.patch(
        "/auth/me",
        headers=auth_headers_factory(test_user),
        json={
            "council_personas": [
                {
                    "id": "one",
                    "display_name": "One",
                    "instruction_prompt": "First",
                    "sort_order": 0,
                },
                {
                    "id": "two",
                    "display_name": "Two",
                    "instruction_prompt": "Second",
                    "sort_order": 1,
                },
                {
                    "id": "three",
                    "display_name": "Three",
                    "instruction_prompt": "Third",
                    "sort_order": 2,
                },
            ]
        },
    )

    assert response.status_code == 422
    assert "at least 4 items" in str(response.json()).lower()


def test_update_current_user_info_empty_prompt_falls_back_to_default(
    auth_client: TestClient,
    db_session: Session,
    production_settings,
    user_factory,
    auth_headers_factory,
) -> None:
    """Blank prompt updates should clear stored value and return the default prompt."""
    test_user = user_factory(
        apple_id="001234.test.blankprompt",
        email="blankprompt@icloud.com",
        full_name="Blank Prompt",
        news_list_preference_prompt="Keep macro and chips only.",
    )

    response = auth_client.patch(
        "/auth/me",
        headers=auth_headers_factory(test_user),
        json={"news_list_preference_prompt": "   "},
    )

    assert response.status_code == 200
    assert response.json()["news_list_preference_prompt"] == DEFAULT_NEWS_LIST_PREFERENCE_PROMPT

    db_session.refresh(test_user)
    assert test_user.news_list_preference_prompt is None


def test_update_current_user_info_rejects_invalid_username(
    auth_client: TestClient,
    production_settings,
    user_factory,
    auth_headers_factory,
) -> None:
    """Test PATCH /auth/me validates username formatting."""
    test_user = user_factory(
        apple_id="001234.test.invalidusername",
        email="invalidusername@icloud.com",
        full_name="Invalid Username",
    )

    response = auth_client.patch(
        "/auth/me",
        headers=auth_headers_factory(test_user),
        json={"twitter_username": "not valid!"},
    )

    assert response.status_code == 400
    assert "Twitter username" in response.json()["detail"]


def test_get_current_user_info_invalid_token(
    auth_client: TestClient,
    production_settings,
) -> None:
    """Test /auth/me with invalid token."""
    response = auth_client.get("/auth/me", headers={"Authorization": "Bearer invalid.token.here"})
    assert response.status_code == 401


def test_get_current_user_info_no_token(
    auth_client: TestClient,
    production_settings,
) -> None:
    """Test /auth/me without token."""
    response = auth_client.get("/auth/me")
    assert response.status_code == 403


def test_datetime_serialization_has_timezone(
    auth_client: TestClient,
    production_settings,
    user_factory,
    auth_headers_factory,
) -> None:
    """Datetime fields in user responses should be ISO8601 with a timezone."""
    test_user = user_factory(
        apple_id="001234.datetime.test",
        email="datetimetest@icloud.com",
        full_name="Datetime Test User",
    )

    response = auth_client.get("/auth/me", headers=auth_headers_factory(test_user))
    assert response.status_code == 200

    data = response.json()
    assert "created_at" in data
    assert "updated_at" in data

    iso8601_tz_pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$"
    assert re.match(iso8601_tz_pattern, data["created_at"])
    assert re.match(iso8601_tz_pattern, data["updated_at"])


def test_debug_new_user_disabled(
    auth_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.routers.auth.settings.debug", False)
    monkeypatch.setattr("app.routers.auth.settings.environment", "production")
    response = auth_client.post("/auth/debug/new-user")
    assert response.status_code == 404


def test_debug_new_user_enabled(
    auth_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.routers.auth.settings.debug", False)
    monkeypatch.setattr("app.routers.auth.settings.environment", "development")

    response = auth_client.post("/auth/debug/new-user")
    assert response.status_code == 200
    data = response.json()
    assert data["is_new_user"] is True
    assert data["user"]["email"].startswith("debug+")
    assert data["access_token"]
    assert data["refresh_token"]


def test_auth_me_repairs_invalid_email(
    auth_client: TestClient,
    db_session: Session,
    production_settings,
    user_factory,
    auth_headers_factory,
) -> None:
    user = user_factory(
        apple_id="001234.invalid",
        email="dev@local",
        full_name="Invalid Email",
        is_active=True,
    )

    response = auth_client.get("/auth/me", headers=auth_headers_factory(user))
    assert response.status_code == 200
    assert response.json()["email"].endswith("@example.com")

    db_session.refresh(user)
    assert user.email.endswith("@example.com")
