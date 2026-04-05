"""Tests for content API response primary_topic and top_comment extraction."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

from app.models.metadata import ContentStatus, ContentType
from app.routers.api.content_responses import build_content_summary_response


def _make_content_row(**overrides) -> MagicMock:
    """Build a minimal Content ORM stub."""
    row = MagicMock()
    row.platform = overrides.get("platform")
    row.updated_at = None
    row.checked_out_by = None
    row.checked_out_at = None
    return row


def _make_domain_mock(
    content_type: ContentType = ContentType.ARTICLE,
    topics: list[str] | None = None,
    metadata: dict | None = None,
    source: str | None = None,
    platform: str | None = None,
) -> MagicMock:
    """Build a mock ContentData with controllable properties."""
    domain = MagicMock()
    domain.id = 1
    domain.content_type = content_type
    domain.url = "https://example.com/article"
    domain.source_url = None
    domain.status = ContentStatus.COMPLETED
    domain.metadata = metadata or {}
    domain.created_at = datetime(2025, 1, 1, tzinfo=UTC)
    domain.processed_at = datetime(2025, 1, 1, tzinfo=UTC)
    domain.publication_date = None
    domain.error_message = None
    domain.retry_count = 0
    domain.title = "Test Title"
    domain.display_title = "Test Title"
    domain.short_summary = "Test summary"
    domain.summary = "Test summary"
    domain.structured_summary = None
    domain.bullet_points = []
    domain.quotes = []
    domain.topics = topics or []
    domain.full_markdown = None
    domain.source = source
    domain.platform = platform
    return domain


class TestPrimaryTopic:
    """Tests for primary_topic extraction in build_content_summary_response."""

    def test_primary_topic_from_topics_list(self):
        """First topic from topics list is used as primary_topic."""
        domain = _make_domain_mock(topics=["AI", "Technology", "Future"])
        row = _make_content_row()

        response = build_content_summary_response(
            content=row,
            domain_content=domain,
            is_read=False,
            is_favorited=False,
        )

        assert response.primary_topic == "AI"

    def test_primary_topic_from_interleaved_topics(self):
        """Topics extracted from interleaved summary are used."""
        domain = _make_domain_mock(topics=["Machine Learning", "Data Science"])
        row = _make_content_row()

        response = build_content_summary_response(
            content=row,
            domain_content=domain,
            is_read=False,
            is_favorited=False,
        )

        assert response.primary_topic == "Machine Learning"

    def test_primary_topic_fallback_to_platform_for_news(self):
        """News items fallback to platform when no topics available."""
        domain = _make_domain_mock(
            content_type=ContentType.NEWS,
            topics=[],
            platform="hackernews",
        )
        row = _make_content_row(platform="hackernews")

        response = build_content_summary_response(
            content=row,
            domain_content=domain,
            is_read=False,
            is_favorited=False,
        )

        assert response.primary_topic == "hackernews"

    def test_primary_topic_none_when_no_topics_or_platform(self):
        """primary_topic is None when no topics exist and not news."""
        domain = _make_domain_mock(topics=[])
        row = _make_content_row()

        response = build_content_summary_response(
            content=row,
            domain_content=domain,
            is_read=False,
            is_favorited=False,
        )

        assert response.primary_topic is None

    def test_primary_topic_falls_back_to_content_row_platform_for_news(self):
        """News items use Content.platform when domain platform is None."""
        domain = _make_domain_mock(
            content_type=ContentType.NEWS,
            topics=[],
            platform=None,
        )
        row = _make_content_row(platform="reddit")

        response = build_content_summary_response(
            content=row,
            domain_content=domain,
            is_read=False,
            is_favorited=False,
        )

        assert response.primary_topic == "reddit"

    def test_primary_topic_blank_topic_falls_back_to_platform_for_news(self):
        """Blank first topic should still fallback to platform for news."""
        domain = _make_domain_mock(
            content_type=ContentType.NEWS,
            topics=["   "],
            platform="hackernews",
        )
        row = _make_content_row(platform=None)

        response = build_content_summary_response(
            content=row,
            domain_content=domain,
            is_read=False,
            is_favorited=False,
        )

        assert response.primary_topic == "hackernews"


class TestTopComment:
    """Tests for top_comment pass-through from metadata."""

    def test_top_comment_from_metadata(self):
        """top_comment is passed through from content_metadata."""
        domain = _make_domain_mock(
            metadata={"top_comment": {"author": "user123", "text": "Great article!"}},
        )
        row = _make_content_row()

        response = build_content_summary_response(
            content=row,
            domain_content=domain,
            is_read=False,
            is_favorited=False,
        )

        assert response.top_comment == {"author": "user123", "text": "Great article!"}

    def test_top_comment_none_when_absent(self):
        """top_comment is None when not in metadata."""
        domain = _make_domain_mock(metadata={})
        row = _make_content_row()

        response = build_content_summary_response(
            content=row,
            domain_content=domain,
            is_read=False,
            is_favorited=False,
        )

        assert response.top_comment is None

    def test_top_comment_none_when_text_missing(self):
        """Invalid top_comment payload is dropped when text is missing."""
        domain = _make_domain_mock(metadata={"top_comment": {"author": "user123"}})
        row = _make_content_row()

        response = build_content_summary_response(
            content=row,
            domain_content=domain,
            is_read=False,
            is_favorited=False,
        )

        assert response.top_comment is None
