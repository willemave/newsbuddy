"""Tests for admin usage dashboard routes."""

from app.core.deps import require_admin
from app.main import app


def _override_admin_dependency(test_user):
    def _override_require_admin():
        return test_user

    return _override_require_admin


def test_admin_vendor_usage_page_renders(client, test_user):
    app.dependency_overrides[require_admin] = _override_admin_dependency(test_user)
    try:
        response = client.get("/admin/vendor-usage")

        assert response.status_code == 200
        assert "Vendor Usage" in response.text
    finally:
        app.dependency_overrides.pop(require_admin, None)


def test_admin_llm_usage_redirects_to_vendor_usage(client, test_user):
    app.dependency_overrides[require_admin] = _override_admin_dependency(test_user)
    try:
        response = client.get(
            "/admin/llm-usage?provider=openai",
            follow_redirects=False,
        )

        assert response.status_code == 307
        assert response.headers["location"] == "/admin/vendor-usage?provider=openai"
    finally:
        app.dependency_overrides.pop(require_admin, None)
