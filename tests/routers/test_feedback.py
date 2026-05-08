"""Tests for authenticated feedback submission."""

from sqlalchemy import select

from app.models.schema import UserFeedback


def test_submit_feedback_persists_row(client, db_session, test_user) -> None:
    response = client.post(
        "/api/feedback",
        json={
            "message": "Please add a compact mode.",
            "source": "ios_settings",
            "app_version": "1.2.3",
            "build_number": "456",
            "platform": "ios",
            "os_version": "26.0",
            "device_model": "iPhone",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["feedback_id"] is not None

    stored = db_session.execute(select(UserFeedback)).scalar_one()
    assert stored.user_id == test_user.id
    assert stored.message == "Please add a compact mode."
    assert stored.source == "ios_settings"
    assert stored.app_version == "1.2.3"
    assert stored.build_number == "456"
    assert stored.platform == "ios"
    assert stored.os_version == "26.0"
    assert stored.device_model == "iPhone"


def test_submit_feedback_trims_message(client, db_session) -> None:
    response = client.post(
        "/api/feedback",
        json={
            "message": "  This needs better empty states.  ",
            "source": " ios_settings ",
        },
    )

    assert response.status_code == 201
    stored = db_session.execute(select(UserFeedback)).scalar_one()
    assert stored.message == "This needs better empty states."
    assert stored.source == "ios_settings"


def test_submit_feedback_rejects_blank_source(client) -> None:
    response = client.post(
        "/api/feedback",
        json={
            "message": "This should validate before persistence.",
            "source": "   ",
        },
    )

    assert response.status_code == 422


def test_submit_feedback_rejects_blank_message(client) -> None:
    response = client.post("/api/feedback", json={"message": "   "})

    assert response.status_code == 422


def test_submit_feedback_requires_authentication(client_factory) -> None:
    with client_factory(authenticate=False) as unauthenticated_client:
        response = unauthenticated_client.post(
            "/api/feedback",
            json={"message": "A signed-out user should not submit this."},
        )

    assert response.status_code in [401, 403]
