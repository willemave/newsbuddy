"""Tests for bearer API key authentication."""

from __future__ import annotations

from app.models.schema import Content
from app.repositories.api_key_repository import create_api_key, revoke_api_key


def test_api_key_can_authenticate_existing_content_route(
    client_factory,
    db_session,
    test_user,
):
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

    with client_factory(authenticate=False) as client:
        response = client.get(
            "/api/content/",
            headers={"Authorization": f"Bearer {raw_key}"},
        )

    assert response.status_code == 200
    db_session.refresh(record)
    assert record.last_used_at is not None


def test_invalid_api_key_is_rejected(client_factory):
    """Invalid bearer API keys should be rejected."""
    with client_factory(authenticate=False) as client:
        response = client.get(
            "/api/content/",
            headers={"Authorization": "Bearer newsly_ak_deadbeef_invalid"},
        )

    assert response.status_code == 401


def test_revoked_api_key_is_rejected(client_factory, db_session, test_user):
    """Revoked API keys should no longer authenticate requests."""
    record, raw_key = create_api_key(
        db_session,
        user_id=test_user.id,
        created_by_admin_user_id=None,
    )
    revoke_api_key(db_session, api_key_id=record.id)

    with client_factory(authenticate=False) as client:
        response = client.get(
            "/api/content/",
            headers={"Authorization": f"Bearer {raw_key}"},
        )

    assert response.status_code == 401
