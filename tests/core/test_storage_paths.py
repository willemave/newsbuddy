from app.core.settings import Settings, get_settings


def test_settings_default_directories(monkeypatch, tmp_path):
    """Ensure default directory configuration respects the current working directory."""

    monkeypatch.chdir(tmp_path)
    settings = Settings(
        database_url="postgresql://postgres@localhost/test_db",
        JWT_SECRET_KEY="test-secret-key",
        ADMIN_PASSWORD="test-admin-password",
    )

    assert settings.media_base_dir == tmp_path / "data" / "media"
    assert settings.logs_base_dir == tmp_path / "logs"
    assert settings.podcast_media_dir == (tmp_path / "data" / "media" / "podcasts").resolve()
    assert settings.logs_dir == (tmp_path / "logs").resolve()


def test_logs_dir_from_settings(monkeypatch, tmp_path):
    """Ensure logs_dir setting is correctly resolved."""

    log_root = tmp_path / "custom_logs"
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres@localhost/test_db")
    monkeypatch.setenv("LOGS_BASE_DIR", str(log_root))
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-password")

    get_settings.cache_clear()
    try:
        settings = get_settings()
        expected_dir = log_root.resolve()
        assert settings.logs_dir == expected_dir
    finally:
        get_settings.cache_clear()
