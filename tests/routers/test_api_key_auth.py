"""Tests for bearer API key authentication."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.db import get_db_session, get_readonly_db_session
from app.main import app
from app.models.schema import Content
from app.repositories.api_key_repository import create_api_key, revoke_api_key


def _make_db_override(db_session):
    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    return _override_get_db


def _make_auth_client(db_session) -> TestClient:
    override_get_db = _make_db_override(db_session)
    app.dependency_overrides[get_db_session] = override_get_db
    app.dependency_overrides[get_readonly_db_session] = override_get_db
    return TestClient(app)


def test_api_key_can_authenticate_existing_content_route(db_session, test_user):
    """Bearer API keys should authenticate the same protected routes as JWT."""
    db_session.add(
        Content(
            content_type="article",
            url="https://example.com/articles/1",
            title="One",
            status="completed",
            content_metadata={},
        )
    )
    db_session.commit()

    record, raw_key = create_api_key(
        db_session,
        user_id=test_user.id,
        created_by_admin_user_id=None,
    )

    with _make_auth_client(db_session) as client:
        response = client.get(
            "/api/content/",
            headers={"Authorization": f"Bearer {raw_key}"},
        )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    db_session.refresh(record)
    assert record.last_used_at is not None


def test_invalid_api_key_is_rejected(db_session):
    """Invalid bearer API keys should be rejected."""
    with _make_auth_client(db_session) as client:
        response = client.get(
            "/api/content/",
            headers={"Authorization": "Bearer newsly_ak_deadbeef_invalid"},
        )

    app.dependency_overrides.clear()

    assert response.status_code == 401


def test_revoked_api_key_is_rejected(db_session, test_user):
    """Revoked API keys should no longer authenticate requests."""
    record, raw_key = create_api_key(
        db_session,
        user_id=test_user.id,
        created_by_admin_user_id=None,
    )
    revoke_api_key(db_session, api_key_id=record.id)

    with _make_auth_client(db_session) as client:
        response = client.get(
            "/api/content/",
            headers={"Authorization": f"Bearer {raw_key}"},
        )

    app.dependency_overrides.clear()

    assert response.status_code == 401
