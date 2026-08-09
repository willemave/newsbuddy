"""Tests for authentication endpoints."""

import re
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import create_access_token, create_refresh_token
from app.models.api.auth import RefreshTokenRequest
from app.models.contracts import TaskType
from app.models.db import ProcessingTask, User, UserIntegrationConnection
from app.routers import auth as auth_router
from app.services.refresh_token_rotation import consume_refresh_token


@pytest.fixture
def auth_client(client_factory) -> Iterator[TestClient]:
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


def test_debug_create_user_can_reset_existing_user_onboarding_flags(
    auth_client: TestClient,
    db_session: Session,
    test_user,
) -> None:
    """Debug auth can persistently reset onboarding flags for an existing user."""
    test_user.has_completed_onboarding = True
    test_user.has_completed_new_user_tutorial = True
    db_session.commit()

    response = auth_client.post(
        "/auth/debug/new-user",
        json={
            "user_id": test_user.id,
            "has_completed_onboarding": False,
            "has_completed_new_user_tutorial": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["user"]["id"] == test_user.id
    assert payload["user"]["has_completed_onboarding"] is False
    assert payload["user"]["has_completed_new_user_tutorial"] is False
    assert payload["is_new_user"] is False

    db_session.refresh(test_user)
    assert test_user.has_completed_onboarding is False
    assert test_user.has_completed_new_user_tutorial is False


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

    replay = auth_client.post(
        "/auth/refresh",
        json={"refresh_token": initial_refresh_token},
    )
    assert replay.status_code == 401


def test_refresh_token_can_only_be_consumed_once_concurrently(
    client_factory,
    user_factory,
) -> None:
    user = user_factory(
        apple_id="001234.concurrent-rotation",
        email="concurrent-rotation@icloud.com",
        is_active=True,
    )
    refresh_token = create_refresh_token(user.id)
    barrier = Barrier(2)

    def exchange(client: TestClient) -> int:
        barrier.wait(timeout=5)
        return client.post(
            "/auth/refresh",
            json={"refresh_token": refresh_token},
        ).status_code

    with (
        client_factory(authenticate=False) as first_client,
        client_factory(authenticate=False) as second_client,
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        futures = [
            executor.submit(exchange, first_client),
            executor.submit(exchange, second_client),
        ]
        statuses = sorted(future.result(timeout=10) for future in futures)

    assert statuses == [200, 401]


def test_refresh_rotation_holds_user_lock_until_token_is_consumed(
    db_session_factory,
    user_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = user_factory(
        apple_id="001234.refresh-delete-race",
        email="refresh-delete-race@icloud.com",
        is_active=True,
    )
    user_id = user.id
    assert user_id is not None
    raw_refresh_token = create_refresh_token(user_id)
    refresh_locked_user = Event()
    deletion_attempted = Event()
    release_refresh = Event()
    deletion_locked_user = Event()

    def paused_consume(db, **kwargs):  # noqa: ANN001
        refresh_locked_user.set()
        assert deletion_attempted.wait(timeout=5)
        assert release_refresh.wait(timeout=5)
        return consume_refresh_token(db, **kwargs)

    monkeypatch.setattr(auth_router, "consume_refresh_token", paused_consume)

    def rotate_token() -> str:
        with db_session_factory() as refresh_db:
            response = auth_router.refresh_token(
                RefreshTokenRequest(refresh_token=raw_refresh_token),
                refresh_db,
            )
            refresh_db.commit()
            return response.refresh_token

    def delete_user() -> None:
        with db_session_factory() as deletion_db:
            deletion_attempted.set()
            locked_user = deletion_db.query(User).filter(User.id == user_id).with_for_update().one()
            deletion_locked_user.set()
            deletion_db.delete(locked_user)
            deletion_db.commit()

    with ThreadPoolExecutor(max_workers=2) as executor:
        refresh_future = executor.submit(rotate_token)
        assert refresh_locked_user.wait(timeout=5)
        deletion_future = executor.submit(delete_user)
        assert deletion_attempted.wait(timeout=5)
        assert not deletion_locked_user.wait(timeout=0.1)

        release_refresh.set()
        assert refresh_future.result(timeout=5)
        deletion_future.result(timeout=5)

    with db_session_factory() as verification_db:
        assert verification_db.get(User, user_id) is None


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

    monkeypatch.setattr("app.admin_web.auth.verify_admin_password", mock_verify_admin_password)

    response = auth_client.post("/auth/admin/login", json={"password": "test_admin_pass"})
    assert response.status_code == 200
    assert response.json()["message"] == "Logged in as admin"
    assert "admin_session" in response.cookies


def test_admin_login_page_is_private_and_clearly_branded(auth_client: TestClient) -> None:
    response = auth_client.get("/auth/admin/login")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-robots-tag"] == "noindex, nofollow, noarchive"
    assert "Newsly Operator Login" in response.text
    assert "Authorized access only" in response.text
    assert "Newsbuddy" not in response.text


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

    monkeypatch.setattr("app.admin_web.auth.verify_admin_password", mock_verify_admin_password)

    response = auth_client.post("/auth/admin/login", json={"password": "test_admin_pass"})
    auth_client.cookies.set("admin_session", response.cookies["admin_session"])

    logout_response = auth_client.post("/auth/admin/logout")
    assert logout_response.status_code == 200
    assert logout_response.json()["message"] == "Logged out"


@pytest.mark.usefixtures("production_settings")
def test_get_current_user_info(
    auth_client: TestClient,
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
    assert data["council_personas"] == []
    assert data["has_x_bookmark_sync"] is False


@pytest.mark.usefixtures("production_settings")
def test_get_current_user_info_reports_x_connection(
    auth_client: TestClient,
    db_session: Session,
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


@pytest.mark.usefixtures("production_settings")
def test_update_current_user_info(
    auth_client: TestClient,
    db_session: Session,
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
            "reading_experience": "briefing",
            "council_personas": [
                {
                    "id": "einstein",
                    "display_name": "Albert Einstein",
                    "sort_order": 0,
                },
                {
                    "id": "turing",
                    "display_name": "Alan Turing",
                    "sort_order": 1,
                },
                {
                    "id": "hopper",
                    "display_name": "Grace Hopper",
                    "sort_order": 2,
                },
            ],
        },
    )
    assert response.status_code == 200

    data = response.json()
    assert data["full_name"] == "Updated Name"
    assert data["twitter_username"] == "willem_aw"
    assert data["reading_experience"] == "briefing"
    assert [persona["display_name"] for persona in data["council_personas"]] == [
        "Albert Einstein",
        "Alan Turing",
        "Grace Hopper",
    ]

    db_session.refresh(test_user)
    assert test_user.full_name == "Updated Name"
    assert test_user.twitter_username == "willem_aw"
    assert test_user.reading_experience == "briefing"
    assert test_user.council_personas[0]["id"] == "einstein"


@pytest.mark.usefixtures("production_settings")
def test_update_current_user_info_rejects_invalid_council_persona_count(
    auth_client: TestClient,
    user_factory,
    auth_headers_factory,
) -> None:
    """PATCH /auth/me should require 2-3 council experts."""
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
                    "sort_order": 0,
                },
            ]
        },
    )

    assert response.status_code == 422
    assert "at least 2 item" in str(response.json()).lower()


@pytest.mark.usefixtures("production_settings")
def test_update_current_user_info_rejects_invalid_username(
    auth_client: TestClient,
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


@pytest.mark.usefixtures("production_settings")
def test_get_current_user_info_invalid_token(
    auth_client: TestClient,
) -> None:
    """Test /auth/me with invalid token."""
    response = auth_client.get("/auth/me", headers={"Authorization": "Bearer invalid.token.here"})
    assert response.status_code == 401


@pytest.mark.usefixtures("production_settings")
def test_get_current_user_info_no_token(
    auth_client: TestClient,
) -> None:
    """Test /auth/me without token."""
    response = auth_client.get("/auth/me")
    assert response.status_code == 403


@pytest.mark.usefixtures("production_settings")
def test_datetime_serialization_has_timezone(
    auth_client: TestClient,
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


@pytest.mark.usefixtures("production_settings")
def test_auth_me_repairs_invalid_email(
    auth_client: TestClient,
    db_session: Session,
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


def test_delete_account_reauthenticates_deactivates_and_enqueues(
    client: TestClient,
    db_session: Session,
    test_user,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.routers.auth.verify_apple_token",
        lambda _token: {"sub": test_user.apple_id},
    )
    revoked: list[str] = []
    monkeypatch.setattr(
        "app.routers.auth.exchange_and_revoke_apple_authorization",
        lambda code: revoked.append(code),
    )

    response = client.request(
        "DELETE",
        "/auth/me",
        json={"id_token": "fresh-token", "authorization_code": "fresh-code"},
    )

    assert response.status_code == 202
    assert response.json() == {"status": "deletion_scheduled"}
    db_session.refresh(test_user)
    assert test_user.is_active is False
    assert revoked == ["fresh-code"]
    task = db_session.query(ProcessingTask).one()
    assert task.task_type == TaskType.DELETE_USER_ACCOUNT.value
    assert task.payload == {"user_id": test_user.id}
    assert task.dedupe_key == f"delete-user-account:{test_user.id}"
    assert task.owner_user_id is None


def test_delete_account_stays_active_when_deletion_cannot_be_queued(
    client,
    db_session,
    test_user,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.routers.auth.verify_apple_token",
        lambda _token: {"sub": test_user.apple_id},
    )
    monkeypatch.setattr(
        "app.routers.auth.exchange_and_revoke_apple_authorization",
        lambda _code: None,
    )

    class FailingGateway:
        def enqueue_many_in_session(self, db, requests) -> list[int]:
            raise RuntimeError("queue unavailable")

    monkeypatch.setattr("app.routers.auth.get_task_queue_gateway", lambda: FailingGateway())

    response = client.request(
        "DELETE",
        "/auth/me",
        json={"id_token": "fresh-token", "authorization_code": "fresh-code"},
    )

    assert response.status_code == 503
    db_session.refresh(test_user)
    assert test_user.is_active is True
