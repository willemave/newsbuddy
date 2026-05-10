"""Tests for application middleware behaviour."""

from pathlib import Path

import app.main as main_module


def test_request_id_is_returned(client) -> None:
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["X-Request-ID"]


def test_request_id_is_propagated(client) -> None:
    response = client.get("/", headers={"X-Request-ID": "req-123"}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["X-Request-ID"] == "req-123"


def test_static_mount_paths_are_resolved(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main_module.settings, "images_base_dir", tmp_path / "data" / "images")

    images_dir, admin_static_dir = main_module._resolve_static_mount_paths()

    assert images_dir == (tmp_path / "data" / "images").resolve()
    assert admin_static_dir == main_module.ADMIN_STATIC_DIR
    assert images_dir.is_dir()


def test_versioned_static_images_are_marked_immutable() -> None:
    assert (
        main_module._static_image_cache_control(
            "/static/images/content/123.png",
            {"v"},
        )
        == "public, max-age=31536000, immutable"
    )


def test_unversioned_static_images_keep_default_cache_headers() -> None:
    assert (
        main_module._static_image_cache_control(
            "/static/images/content/123.png",
            set(),
        )
        is None
    )


def test_admin_static_css_is_served(client) -> None:
    response = client.get("/admin/static/css/app.css")

    assert response.status_code == 200
    assert b".markdown-content" in response.content


def test_health_check_includes_database_readiness(client) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "service": main_module.settings.app_name,
        "checks": {"database": "healthy"},
    }


def test_health_check_returns_503_when_database_unavailable(client, monkeypatch) -> None:
    def _raise_database_unavailable() -> None:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(main_module, "_check_database_health", _raise_database_unavailable)

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unhealthy",
        "service": main_module.settings.app_name,
        "checks": {"database": "unhealthy"},
    }
