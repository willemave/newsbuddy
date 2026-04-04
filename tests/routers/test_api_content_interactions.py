"""Tests for content interaction analytics API endpoint."""

from uuid import uuid4

from sqlalchemy import select

from app.models.schema import AnalyticsInteraction


def test_record_content_interaction_success(client, content_factory, db_session) -> None:
    """It should persist a new interaction and return its ID."""
    content = content_factory(
        url="https://example.com/api-interaction-content",
        title="Analytics API Content",
    )
    interaction_id = str(uuid4())

    response = client.post(
        "/api/analytics",
        json={
            "interaction_id": interaction_id,
            "content_id": content.id,
            "interaction_type": "opened",
            "surface": "ios_content_detail",
            "context_data": {
                "content_type": "article",
                "was_read_when_loaded": False,
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["recorded"] is True
    assert payload["interaction_id"] == interaction_id
    assert payload["analytics_interaction_id"] is not None

    stored = db_session.execute(select(AnalyticsInteraction)).scalars().all()
    assert len(stored) == 1
    assert stored[0].content_id == content.id
    assert stored[0].interaction_type == "opened"
    assert stored[0].surface == "ios_content_detail"


def test_record_content_interaction_idempotent(client, content_factory, db_session) -> None:
    """It should return recorded=false for duplicate interaction IDs."""
    content = content_factory(
        url="https://example.com/api-interaction-content",
        title="Analytics API Content",
    )
    interaction_id = str(uuid4())
    payload = {
        "interaction_id": interaction_id,
        "content_id": content.id,
        "interaction_type": "opened",
        "surface": "ios_content_detail",
        "context_data": {},
    }

    first = client.post("/api/analytics", json=payload)
    second = client.post("/api/analytics", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["recorded"] is True
    assert second.json()["recorded"] is False
    assert first.json()["analytics_interaction_id"] == second.json()["analytics_interaction_id"]

    stored = db_session.execute(select(AnalyticsInteraction)).scalars().all()
    assert len(stored) == 1


def test_record_content_interaction_missing_content(client) -> None:
    """It should return 404 when content does not exist."""
    response = client.post(
        "/api/analytics",
        json={
            "interaction_id": str(uuid4()),
            "content_id": 9_999_999,
            "interaction_type": "opened",
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Content not found"


def test_record_content_interaction_requires_authentication(client_factory) -> None:
    """It should reject unauthenticated requests."""
    with client_factory(authenticate=False) as unauthenticated_client:
        response = unauthenticated_client.post(
            "/api/analytics",
            json={
                "interaction_id": str(uuid4()),
                "content_id": 1,
                "interaction_type": "opened",
            },
        )

    assert response.status_code in [401, 403]
