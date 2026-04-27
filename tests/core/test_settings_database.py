"""Tests for database URL validation."""

import pytest
from pydantic import ValidationError

from app.core.settings import Settings


def test_settings_reject_sqlite_database_url() -> None:
    """SQLite DSNs should fail with an explicit deprecation error."""
    with pytest.raises(ValidationError, match="SQLite has been deprecated"):
        Settings(
            database_url="sqlite:///tmp/newsly.db",
            JWT_SECRET_KEY="test-secret-key",
            ADMIN_PASSWORD="test-admin-password",
        )


def test_production_settings_reject_wildcard_cors() -> None:
    with pytest.raises(ValidationError, match="CORS_ALLOW_ORIGINS"):
        Settings(
            database_url="postgresql://postgres@localhost/test",
            JWT_SECRET_KEY="test-secret-key",
            ADMIN_PASSWORD="test-admin-password",
            environment="production",
            cors_allow_origins=["*"],
        )


def test_settings_parse_csv_security_lists() -> None:
    settings = Settings(
        database_url="postgresql://postgres@localhost/test",
        JWT_SECRET_KEY="test-secret-key",
        ADMIN_PASSWORD="test-admin-password",
        cors_allow_origins="https://app.example.com, https://admin.example.com",
        apple_signin_audiences="org.willemaw.newsly, org.willemaw.newsly.ShareExtension",
    )

    assert settings.cors_allow_origins == [
        "https://app.example.com",
        "https://admin.example.com",
    ]
    assert settings.apple_signin_audiences == [
        "org.willemaw.newsly",
        "org.willemaw.newsly.ShareExtension",
    ]


def test_settings_grouped_views_do_not_expose_secrets() -> None:
    settings = Settings(
        database_url="postgresql://postgres@localhost/test",
        JWT_SECRET_KEY="jwt-secret-value",
        ADMIN_PASSWORD="admin-secret-value",
        openai_api_key="openai-secret-value",
        langfuse_secret_key="langfuse-secret-value",
        x_client_secret="x-secret-value",
    )

    diagnostics = settings.redacted_diagnostics()
    rendered = str(diagnostics)

    assert diagnostics["redacted"] is True
    assert diagnostics["groups"]["auth"]["jwt_secret_configured"] is True
    assert diagnostics["groups"]["auth"]["admin_password_configured"] is True
    assert diagnostics["groups"]["providers"]["openai_api_key_configured"] is True
    assert diagnostics["groups"]["observability"]["langfuse_secret_key_configured"] is True
    assert diagnostics["groups"]["integrations"]["x"]["x_client_secret_configured"] is True
    assert "jwt-secret-value" not in rendered
    assert "admin-secret-value" not in rendered
    assert "openai-secret-value" not in rendered
    assert "langfuse-secret-value" not in rendered
    assert "x-secret-value" not in rendered
