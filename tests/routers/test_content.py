"""Tests for admin-only web interface routes."""


def test_root_describes_private_service_without_exposing_admin(client):
    """Root should identify the service without leading crawlers to a password form."""
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["x-robots-tag"] == "noindex, nofollow, noarchive"
    assert "Newsbuddy" in response.text
    assert "password" not in response.text.lower()
    assert "/admin" not in response.text


def test_robots_txt_disallows_crawling_private_origin(client):
    response = client.get("/robots.txt")

    assert response.status_code == 200
    assert response.text == "User-agent: *\nDisallow: /\n"
    assert response.headers["x-robots-tag"] == "noindex, nofollow, noarchive"


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
