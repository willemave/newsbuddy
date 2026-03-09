"""Tests for admin router dashboard pages."""

from datetime import UTC, datetime

from app.core.deps import require_admin
from app.main import app
from app.models.schema import Content, EventLog, ProcessingTask, UserApiKey


def _override_admin_dependency(test_user):
    def _override_require_admin():
        return test_user

    return _override_require_admin


def test_admin_dashboard_renders_sections(client, db_session, test_user):
    """Dashboard route should render successfully with seeded data."""
    app.dependency_overrides[require_admin] = _override_admin_dependency(test_user)
    try:
        now = datetime.now(UTC)
        db_session.add(
            Content(
                content_type="article",
                url="https://example.com/test",
                title="Test Article",
                source="test",
                status="completed",
            )
        )
        db_session.add(
            ProcessingTask(
                task_type="summarize",
                content_id=1,
                status="completed",
                created_at=now,
                completed_at=now,
            )
        )
        db_session.add(
            EventLog(
                event_type="scraper_stats",
                event_name="scrape_complete",
                status="success",
                data={"saved": 1},
                created_at=now,
            )
        )
        db_session.commit()

        response = client.get("/admin/")
        assert response.status_code == 200
        assert "Queue Status" in response.text
        assert "Task Phases" in response.text
        assert "Scraper Health (24h)" in response.text
    finally:
        app.dependency_overrides.pop(require_admin, None)


def test_admin_dashboard_filters_event_logs_by_type(client, db_session, test_user):
    """`event_type` query param should scope dashboard event rows."""
    app.dependency_overrides[require_admin] = _override_admin_dependency(test_user)
    try:
        now = datetime.now(UTC)
        db_session.add_all(
            [
                EventLog(
                    event_type="scraper",
                    event_name="event_scraper_a",
                    status="success",
                    data={"index": 0},
                    created_at=now,
                ),
                EventLog(
                    event_type="scraper",
                    event_name="event_scraper_b",
                    status="success",
                    data={"index": 1},
                    created_at=now,
                ),
                EventLog(
                    event_type="processor",
                    event_name="event_processor",
                    status="success",
                    data={"processed": True},
                    created_at=now,
                ),
            ]
        )
        db_session.commit()

        response = client.get("/admin/?event_type=scraper")
        assert response.status_code == 200
        assert "event_scraper_a" in response.text
        assert "event_scraper_b" in response.text
        assert "event_processor" not in response.text
    finally:
        app.dependency_overrides.pop(require_admin, None)


def test_admin_api_keys_page_renders(client, db_session, test_user):
    app.dependency_overrides[require_admin] = _override_admin_dependency(test_user)
    try:
        response = client.get("/admin/api-keys")
        assert response.status_code == 200
        assert "API Keys" in response.text
        assert str(test_user.email) in response.text
    finally:
        app.dependency_overrides.pop(require_admin, None)


def test_admin_can_create_and_revoke_api_key(client, db_session, test_user):
    app.dependency_overrides[require_admin] = _override_admin_dependency(test_user)
    try:
        create_response = client.post("/admin/api-keys/create", data={"user_id": str(test_user.id)})
        assert create_response.status_code == 200
        assert "Copy this key now" in create_response.text
        assert "newsly_ak_" in create_response.text

        record = db_session.query(UserApiKey).filter(UserApiKey.user_id == test_user.id).first()
        assert record is not None
        assert record.revoked_at is None

        revoke_response = client.post(f"/admin/api-keys/{record.id}/revoke", follow_redirects=False)
        assert revoke_response.status_code == 303

        db_session.refresh(record)
        assert record.revoked_at is not None
    finally:
        app.dependency_overrides.pop(require_admin, None)
