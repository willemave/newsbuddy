"""Tests for admin summary eval routes."""

from app.core.deps import require_admin
from app.main import app


def test_admin_eval_page_requires_admin_session(client):
    """Eval page should redirect to admin login without admin session."""
    response = client.get("/admin/evals/summaries", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/auth/admin/login?next=")


def test_admin_eval_run_requires_admin_session(client):
    """Eval run API should redirect to admin login without admin session."""
    response = client.post(
        "/admin/evals/summaries/run",
        json={
            "content_types": ["article"],
            "models": ["cheap"],
            "sample_size": 1,
            "recent_pool_size": 20,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/auth/admin/login?next=")


def test_admin_eval_run_returns_payload(client, test_user, monkeypatch):
    """Eval API should return run payload for authenticated admin."""

    def override_require_admin():
        return test_user

    def fake_run_admin_eval(_db, _payload):
        return {
            "run_started_at": "2026-02-07T00:00:00+00:00",
            "available_models": [
                {"alias": "cheap", "model_spec": "google:gemini-3.1-flash-lite-preview"}
            ],
            "skipped_models": [],
            "samples_by_type": {"article": []},
            "results": [],
            "aggregate": {
                "items_total": 0,
                "cells_total": 0,
                "cells_successful": 0,
                "cells_failed": 0,
            },
        }

    app.dependency_overrides[require_admin] = override_require_admin
    monkeypatch.setattr("app.admin_web.evals.run_admin_eval", fake_run_admin_eval)

    response = client.post(
        "/admin/evals/summaries/run",
        json={
            "content_types": ["article"],
            "models": ["cheap"],
            "sample_size": 1,
            "recent_pool_size": 20,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["available_models"][0]["alias"] == "cheap"


def test_admin_eval_run_rejects_unknown_model(client, test_user):
    """Eval API should validate model aliases."""

    def override_require_admin():
        return test_user

    app.dependency_overrides[require_admin] = override_require_admin

    response = client.post(
        "/admin/evals/summaries/run",
        json={
            "content_types": ["article"],
            "models": ["unknown_model"],
            "sample_size": 1,
            "recent_pool_size": 20,
        },
    )

    assert response.status_code == 422
