"""Integration tests for authentication on protected endpoints."""

from datetime import timedelta

from app.core.security import create_access_token, create_token


def test_api_endpoints_require_authentication(client_factory):
    """Test that API endpoints reject requests without authentication."""
    with client_factory(authenticate=False) as client:
        endpoints_to_test = [
            ("GET", "/api/content/"),
            ("GET", "/api/content/1"),
            ("POST", "/api/content/1/knowledge"),
            ("POST", "/api/content/1/mark-read"),
        ]

        for method, endpoint in endpoints_to_test:
            if method == "GET":
                response = client.get(endpoint)
            elif method == "POST":
                response = client.post(endpoint)

            assert response.status_code in [401, 403], (
                f"{method} {endpoint} should require auth (got {response.status_code})"
            )


def test_authenticated_requests_accepted(
    client_factory,
    content_factory,
    status_entry_factory,
    user_factory,
):
    """Test that authenticated requests are accepted."""
    user = user_factory(
        apple_id="test.integration.001",
        email="integration@example.com",
    )
    content = content_factory(
        title="Test Article",
        url="https://example.com/test",
    )
    status_entry_factory(user=user, content=content, status="inbox")

    with client_factory(user=user) as client:
        headers = {"Authorization": f"Bearer {create_access_token(user.id)}"}

        response = client.get("/api/content/", headers=headers)
        assert response.status_code == 200

        response = client.get(f"/api/content/{content.id}", headers=headers)
        assert response.status_code == 200

        response = client.post(f"/api/content/{content.id}/knowledge", headers=headers)
        assert response.status_code == 200


def test_invalid_token_rejected(client_factory):
    """Test that requests with invalid tokens are rejected."""
    with client_factory(authenticate=False) as client:
        response = client.get(
            "/api/content/",
            headers={"Authorization": "Bearer invalid.token.here"},
        )
        assert response.status_code == 401


def test_expired_token_rejected(client_factory, user_factory):
    """Test that expired tokens are rejected."""
    user = user_factory(
        apple_id="test.expired.001",
        email="expired@example.com",
    )
    expired_token = create_token(user.id, "access", timedelta(hours=-1))
    with client_factory(authenticate=False) as client:
        response = client.get(
            "/api/content/",
            headers={"Authorization": f"Bearer {expired_token}"},
        )
        assert response.status_code == 401
