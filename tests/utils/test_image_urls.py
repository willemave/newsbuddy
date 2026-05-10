from app.utils.image_urls import (
    append_image_version,
    build_content_image_url,
    build_thumbnail_url,
)


def test_build_generated_image_urls_include_encoded_version() -> None:
    assert build_content_image_url(123, version="2026-01-01T00:00:00+00:00") == (
        "/static/images/content/123.png?v=2026-01-01T00%3A00%3A00%2B00%3A00"
    )
    assert build_thumbnail_url(123, version="2026-01-01T00:00:00Z") == (
        "/static/images/thumbnails/123.png?v=2026-01-01T00%3A00%3A00Z"
    )


def test_append_image_version_only_versions_static_images() -> None:
    assert (
        append_image_version(
            "/static/images/content/123.png?size=full",
            "version-1",
        )
        == "/static/images/content/123.png?size=full&v=version-1"
    )
    assert (
        append_image_version(
            "https://cdn.example.com/thumb.png",
            "version-1",
        )
        == "https://cdn.example.com/thumb.png"
    )
    assert (
        append_image_version(
            "/static/images/content/123.png?v=existing",
            "version-1",
        )
        == "/static/images/content/123.png?v=existing"
    )
