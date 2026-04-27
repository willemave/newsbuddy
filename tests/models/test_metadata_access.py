"""Tests for typed content metadata accessors."""

from app.models.metadata_access import metadata_view


def test_metadata_view_reads_legacy_flat_metadata() -> None:
    view = metadata_view(
        {
            "summary": {"title": "Flat"},
            "summary_kind": "long_structured",
            "detected_feed": {"url": "https://example.com/feed.xml", "type": "rss"},
            "submitted_by_user_id": "7",
            "image_generated_at": "2026-04-01T00:00:00Z",
        }
    )

    assert view.summary() == {"title": "Flat"}
    assert view.summary_kind() == "long_structured"
    assert view.detected_feed() == {"url": "https://example.com/feed.xml", "type": "rss"}
    assert view.submission_user_id() == 7
    assert view.image_state()["image_generated_at"] == "2026-04-01T00:00:00Z"


def test_metadata_view_reads_namespaced_metadata() -> None:
    view = metadata_view(
        {
            "domain": {
                "summary": {"title": "Namespaced"},
                "article": {"url": "https://example.com/story"},
            },
            "processing": {
                "detected_feed": {"url": "https://example.com/rss", "type": "rss"},
                "submitted_by_user_id": 9,
            },
        }
    )

    assert view.summary() == {"title": "Namespaced"}
    assert view.detected_feed() == {"url": "https://example.com/rss", "type": "rss"}
    assert view.submission_user_id() == 9
    assert view.news_fields().article == {"url": "https://example.com/story"}


def test_metadata_view_reads_mixed_dual_write_metadata() -> None:
    view = metadata_view(
        {
            "summary": {"title": "Legacy"},
            "domain": {"summary": {"title": "Domain"}, "summary_key_points": ["a"]},
            "processing": {"detected_feed": {"url": "https://example.com/feed", "type": "rss"}},
        }
    )

    assert view.summary() == {"title": "Domain"}
    assert view.detected_feed() == {"url": "https://example.com/feed", "type": "rss"}
    assert view.news_fields().summary_key_points == ["a"]
