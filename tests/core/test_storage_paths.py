from pathlib import Path

import app.core.settings as settings_module
from app.core.settings import Settings, get_settings


def test_settings_default_directories(monkeypatch, tmp_path):
    """Ensure default directory configuration respects the current working directory."""

    monkeypatch.chdir(tmp_path)
    for env_name in (
        "MEDIA_BASE_DIR",
        "LOGS_BASE_DIR",
        "IMAGES_BASE_DIR",
        "CONTENT_BODY_LOCAL_ROOT",
        "PODCAST_SCRATCH_DIR",
        "PERSONAL_MARKDOWN_ROOT",
    ):
        monkeypatch.delenv(env_name, raising=False)
    settings = Settings(
        database_url="postgresql://postgres@localhost/test_db",
        JWT_SECRET_KEY="test-secret-key",
        ADMIN_PASSWORD="test-admin-password",
    )

    assert settings.media_base_dir == tmp_path / "data" / "media"
    assert settings.logs_base_dir == tmp_path / "logs"
    assert (
        settings.tweet_video_media_dir == (tmp_path / "data" / "media" / "tweet_videos").resolve()
    )
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


def test_container_storage_paths_fallback_to_local_when_data_root_is_not_writable(
    monkeypatch, tmp_path
):
    """Container-style /data paths should fall back to local dev roots when unavailable."""

    original_access = settings_module.os.access

    def fake_access(path, mode):
        if str(path) in {"/", "/data"}:
            return False
        return original_access(path, mode)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings_module.os, "access", fake_access)

    settings = Settings(
        database_url="postgresql://postgres@localhost/test_db",
        JWT_SECRET_KEY="test-secret-key",
        ADMIN_PASSWORD="test-admin-password",
        media_base_dir=Path("/data/media"),
        logs_base_dir=Path("/data/logs"),
        images_base_dir=Path("/data/images"),
        content_body_local_root=Path("/data/content_bodies"),
        podcast_scratch_dir=Path("/data/scratch"),
        personal_markdown_root=Path("/data/personal_markdown"),
    )

    assert settings.media_base_dir == tmp_path / "data" / "media"
    assert settings.logs_base_dir == tmp_path / "logs"
    assert settings.images_base_dir == tmp_path / "data" / "images"
    assert settings.content_body_local_root == tmp_path / "data" / "content_bodies"
    assert settings.podcast_scratch_dir == tmp_path / "data" / "scratch"
    assert settings.personal_markdown_root == tmp_path / "data" / "personal_markdown"
