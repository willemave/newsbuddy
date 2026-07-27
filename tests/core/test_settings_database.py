"""Tests for database URL validation."""

from typing import Any, cast

import pytest
from pydantic import ValidationError

from app.core.settings import Settings


def build_settings(**kwargs: Any) -> Settings:
    return Settings(**kwargs)


def test_settings_reject_sqlite_database_url() -> None:
    """SQLite DSNs should fail with an explicit deprecation error."""
    with pytest.raises(ValidationError, match="SQLite has been deprecated"):
        build_settings(
            database_url="sqlite:///tmp/newsly.db",
            JWT_SECRET_KEY="test-secret-key",
            ADMIN_PASSWORD="test-admin-password",
        )


def test_production_settings_reject_wildcard_cors() -> None:
    with pytest.raises(ValidationError, match="CORS_ALLOW_ORIGINS"):
        build_settings(
            database_url="postgresql://postgres@localhost/test",
            JWT_SECRET_KEY="test-secret-key",
            ADMIN_PASSWORD="test-admin-password",
            environment="production",
            cors_allow_origins=["*"],
        )


def test_production_settings_require_https_public_base_url() -> None:
    common = {
        "database_url": "postgresql://postgres@localhost/test",
        "JWT_SECRET_KEY": "test-secret-key",
        "ADMIN_PASSWORD": "test-admin-password",
        "environment": "production",
        "cors_allow_origins": ["https://app.example.com"],
    }

    with pytest.raises(ValidationError, match="PUBLIC_BASE_URL must be set"):
        build_settings(**common)

    with pytest.raises(ValidationError, match="PUBLIC_BASE_URL must use HTTPS"):
        build_settings(**common, public_base_url="http://app.example.com")


def test_public_base_url_must_be_an_origin() -> None:
    with pytest.raises(ValidationError, match="PUBLIC_BASE_URL must be an origin"):
        build_settings(
            database_url="postgresql://postgres@localhost/test",
            JWT_SECRET_KEY="test-secret-key",
            ADMIN_PASSWORD="test-admin-password",
            public_base_url="https://app.example.com/api",
        )


def test_settings_parse_csv_security_lists() -> None:
    settings = build_settings(
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


def test_settings_parse_csv_security_lists_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres@localhost/test")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-password")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv(
        "CORS_ALLOW_ORIGINS",
        "https://app.example.com, https://admin.example.com",
    )
    monkeypatch.setenv(
        "APPLE_SIGNIN_AUDIENCES",
        "org.willemaw.newsly, org.willemaw.newsly.ShareExtension",
    )
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.example.com")

    settings = build_settings()

    assert settings.cors_allow_origins == [
        "https://app.example.com",
        "https://admin.example.com",
    ]
    assert settings.apple_signin_audiences == [
        "org.willemaw.newsly",
        "org.willemaw.newsly.ShareExtension",
    ]
    assert str(settings.public_base_url) == "https://app.example.com/"


def test_settings_accept_legacy_twitter_bearer_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres@localhost/test")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-password")
    monkeypatch.delenv("X_APP_BEARER_TOKEN", raising=False)
    monkeypatch.setenv("TWITTER_AUTH_TOKEN", "legacy-bearer-token")

    settings = build_settings()

    assert settings.x_app_bearer_token == "legacy-bearer-token"


def test_settings_grouped_views_do_not_expose_secrets() -> None:
    settings = build_settings(
        database_url="postgresql://postgres@localhost/test",
        JWT_SECRET_KEY="jwt-secret-value",
        ADMIN_PASSWORD="admin-secret-value",
        openai_api_key="openai-secret-value",
        langfuse_secret_key="langfuse-secret-value",
        x_client_secret="x-secret-value",
    )

    diagnostics = cast(dict[str, Any], settings.redacted_diagnostics())
    rendered = str(diagnostics)

    assert diagnostics["redacted"] is True
    assert diagnostics["groups"]["auth"]["jwt_secret_configured"] is True
    assert diagnostics["groups"]["auth"]["admin_password_configured"] is True
    assert diagnostics["groups"]["providers"]["openai_api_key_configured"] is True
    assert diagnostics["groups"]["observability"]["langfuse_secret_key_configured"] is True
    assert diagnostics["groups"]["observability"]["public_base_url"] is None
    assert diagnostics["groups"]["integrations"]["x"]["x_client_secret_configured"] is True
    assert "twitter_ct0_configured" not in diagnostics["groups"]["integrations"]["x"]
    assert "jwt-secret-value" not in rendered
    assert "admin-secret-value" not in rendered
    assert "openai-secret-value" not in rendered
    assert "langfuse-secret-value" not in rendered
    assert "x-secret-value" not in rendered
