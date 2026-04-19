"""Tests for admin router dashboard pages."""

import re
from datetime import UTC, datetime, timedelta

from app.core.deps import require_admin
from app.main import app
from app.models.schema import Content, ProcessingTask, UserApiKey


def _override_admin_dependency(test_user):
    def _override_require_admin():
        return test_user

    return _override_require_admin


def _has_tag_text(body: str, text: str) -> bool:
    """Return whether the rendered HTML contains the given text node content."""
    return re.search(rf">\s*{re.escape(text)}\s*<", body) is not None


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
            ProcessingTask(
                task_type="generate_image",
                content_id=1,
                status="pending",
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


def test_admin_dashboard_defaults_summary_stats_to_24h(client, db_session, test_user):
    """Dashboard summary stats should default to the last 24 hours."""
    app.dependency_overrides[require_admin] = _override_admin_dependency(test_user)
    try:
        now = datetime.now(UTC)
        older = now - timedelta(days=3)
        db_session.add_all(
            [
                Content(
                    content_type="article",
                    url="https://example.com/recent-article",
                    title="Recent Article",
                    source="test",
                    status="completed",
                    created_at=now,
                ),
                Content(
                    content_type="news",
                    url="https://example.com/old-news",
                    title="Old News",
                    source="test",
                    status="completed",
                    created_at=older,
                ),
                ProcessingTask(
                    task_type="summarize",
                    content_id=1,
                    status="processing",
                    created_at=now,
                ),
                ProcessingTask(
                    task_type="generate_image",
                    content_id=1,
                    status="failed",
                    created_at=older,
                    completed_at=older,
                    error_message="old failure",
                ),
            ]
        )
        db_session.commit()

        response = client.get("/admin/")
        assert response.status_code == 200
        body = response.text

        assert "Content 24h" in body
        assert "Tasks 24h" in body
        assert "Failures 24h" in body
        assert _has_tag_text(body, "article")
        assert not _has_tag_text(body, "news")
    finally:
        app.dependency_overrides.pop(require_admin, None)


def test_admin_dashboard_all_time_summary_stats_include_older_records(
    client, db_session, test_user
):
    """Dashboard summary stats should include older records when all time is selected."""
    app.dependency_overrides[require_admin] = _override_admin_dependency(test_user)
    try:
        now = datetime.now(UTC)
        older = now - timedelta(days=3)
        db_session.add_all(
            [
                Content(
                    content_type="article",
                    url="https://example.com/recent-article-all",
                    title="Recent Article",
                    source="test",
                    status="completed",
                    created_at=now,
                ),
                Content(
                    content_type="news",
                    url="https://example.com/old-news-all",
                    title="Old News",
                    source="test",
                    status="completed",
                    created_at=older,
                ),
                ProcessingTask(
                    task_type="summarize",
                    content_id=1,
                    status="processing",
                    created_at=now,
                ),
                ProcessingTask(
                    task_type="generate_image",
                    content_id=1,
                    status="failed",
                    created_at=older,
                    completed_at=older,
                    error_message="old failure",
                ),
            ]
        )
        db_session.commit()

        response = client.get("/admin/?stats_range=all")
        assert response.status_code == 200
        body = response.text

        assert "Content All time" in body
        assert "Tasks All time" in body
        assert "Failures All time" in body
        assert _has_tag_text(body, "article")
        assert _has_tag_text(body, "news")
        assert _has_tag_text(body, "failed")
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
