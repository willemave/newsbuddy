"""Tests for ElevenLabs settings alias support."""

from app.core.settings import get_settings


def test_settings_accept_legacy_elevenlabs_env_alias(monkeypatch):
    """ELEVENLABS should map to elevenlabs_api_key for backward compatibility."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres@localhost/test_db")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-password")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setenv("ELEVENLABS", "legacy-key")

    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.elevenlabs_api_key == "legacy-key"
    finally:
        get_settings.cache_clear()
