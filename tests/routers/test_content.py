"""Tests for admin-only web interface routes."""


def test_root_redirects_to_admin(client):
    """Root should redirect to admin dashboard entry point."""
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin"


def test_removed_web_content_routes_return_404(client):
    """Legacy web content browsing routes should not exist."""
    assert client.get("/articles/").status_code == 404
    assert client.get("/articles/detail/1").status_code == 404
    assert client.get("/favorites").status_code == 404
    assert client.get("/content/1").status_code == 404
    assert client.get("/content/1/json").status_code == 404


def test_admin_dashboard_requires_admin_session(client):
    """Admin dashboard should still enforce admin session auth."""
    response = client.get("/admin/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/auth/admin/login?next=")
