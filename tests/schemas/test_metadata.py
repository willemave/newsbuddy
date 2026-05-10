"""Tests for metadata schema validation."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models.metadata.articles import ArticleMetadata
from app.models.metadata.podcasts import PodcastMetadata
from app.models.metadata.summaries import ContentQuote, StructuredSummary, SummaryBulletPoint
from app.models.metadata.validation import validate_content_metadata


class TestStructuredSummary:
    """Test structured summary model."""

    def test_valid_structured_summary(self):
        """Test creating a valid structured summary."""
        summary = StructuredSummary(
            title="Test Article Title for Validation",
            overview=(
                "This is a comprehensive test overview of the content that provides "
                "sufficient detail to meet the minimum length requirement"
            ),
            bullet_points=[
                SummaryBulletPoint(text="First key point", category="key_finding"),
                SummaryBulletPoint(text="Second key point", category="methodology"),
                SummaryBulletPoint(text="Third key point", category="conclusion"),
            ],
            quotes=[ContentQuote(text="This is a notable quote", context="Author Name")],
            topics=["AI", "Technology", "Innovation"],
        )

        assert summary.title == "Test Article Title for Validation"
        assert (
            summary.overview
            == "This is a comprehensive test overview of the content that provides "
            "sufficient detail to meet the minimum length requirement"
        )
        assert len(summary.bullet_points) == 3
        assert summary.bullet_points[0].category == "key_finding"
        assert len(summary.quotes) == 1
        assert len(summary.topics) == 3

    def test_minimum_bullet_points_requirement(self):
        """Test that at least 3 bullet points are required."""
        with pytest.raises(ValidationError) as exc_info:
            StructuredSummary(
                title="Test Title for Validation",
                overview=(
                    "Test overview that meets the minimum length requirement for this "
                    "validation test"
                ),
                bullet_points=[SummaryBulletPoint(text="Only one point", category="key_finding")],
            )
        assert "at least 3 items" in str(exc_info.value)

    def test_maximum_limits(self):
        """Test maximum limits for bullet points, quotes, and topics."""
        # Too many bullet points
        with pytest.raises(ValidationError):
            StructuredSummary(
                title="Test Title for Validation",
                overview=(
                    "Test overview that meets the minimum length requirement for this "
                    "validation test"
                ),
                bullet_points=[
                    SummaryBulletPoint(text=f"Point {i}", category="key_finding")
                    for i in range(11)  # 11 points, max is 10
                ],
            )

        # Too many quotes
        with pytest.raises(ValidationError):
            StructuredSummary(
                title="Test Title for Validation",
                overview=(
                    "Test overview that meets the minimum length requirement for this "
                    "validation test"
                ),
                bullet_points=[
                    SummaryBulletPoint(text=f"Point {i}", category="key_finding") for i in range(3)
                ],
                quotes=[
                    ContentQuote(text=f"Quote {i} with enough text", context="Author")
                    for i in range(6)  # 6 quotes, max is 5
                ],
            )

    def test_long_quotes_allowed(self):
        """Test that quotes up to 5000 characters are allowed."""
        # Create a quote with 2000 characters (previously would have failed)
        long_quote_text = "This is a very long quote that contains meaningful content. " * 30
        assert len(long_quote_text) > 1000  # Confirm it's over the old limit
        assert len(long_quote_text) < 5000  # But within the new limit

        summary = StructuredSummary(
            title="Test Article with Long Quote",
            overview=(
                "This is a test overview with a very long quote to ensure the new "
                "character limit works correctly"
            ),
            bullet_points=[
                SummaryBulletPoint(text="First key point", category="key_finding"),
                SummaryBulletPoint(text="Second key point", category="methodology"),
                SummaryBulletPoint(text="Third key point", category="conclusion"),
            ],
            quotes=[ContentQuote(text=long_quote_text, context="Author Name")],
        )

        assert len(summary.quotes[0].text) > 1000
        assert summary.quotes[0].text == long_quote_text

    def test_quote_exceeding_max_length(self):
        """Test that quotes exceeding 5000 characters are rejected."""
        # Create a quote with more than 5000 characters
        too_long_quote = "x" * 5001

        with pytest.raises(ValidationError) as exc_info:
            StructuredSummary(
                title="Test Title for Validation",
                overview=(
                    "Test overview that meets the minimum length requirement for this "
                    "validation test"
                ),
                bullet_points=[
                    SummaryBulletPoint(
                        text=f"Point number {i} with enough text",
                        category="key_finding",
                    )
                    for i in range(3)
                ],
                quotes=[ContentQuote(text=too_long_quote, context="Author")],
            )
        assert "at most 5000 characters" in str(exc_info.value)


class TestArticleMetadata:
    """Test article metadata validation."""

    def test_valid_article_metadata(self):
        """Test creating valid article metadata."""
        metadata = ArticleMetadata(
            content="This is the article content",
            author="John Doe",
            publication_date=datetime.now(UTC),
            content_type="html",
            final_url_after_redirects="https://example.com/article",
            word_count=100,
        )

        assert metadata.content == "This is the article content"
        assert metadata.author == "John Doe"
        assert metadata.word_count == 100

    def test_article_with_structured_summary(self):
        """Test article metadata with structured summary."""
        structured_summary = StructuredSummary(
            title="Technology Analysis Article",
            overview=(
                "This article provides a comprehensive analysis of recent "
                "technological developments and their impact on society"
            ),
            bullet_points=[
                SummaryBulletPoint(text="Key finding 1", category="key_finding"),
                SummaryBulletPoint(text="Key finding 2", category="key_finding"),
                SummaryBulletPoint(text="Conclusion point", category="conclusion"),
            ],
            topics=["Technology", "AI"],
        )

        metadata = ArticleMetadata(content="Article content", summary=structured_summary)

        assert isinstance(metadata.summary, StructuredSummary)
        assert len(metadata.summary.bullet_points) == 3

    def test_article_with_string_summary(self):
        """Test that string summaries are not allowed - must use StructuredSummary."""
        with pytest.raises(ValidationError) as exc_info:
            ArticleMetadata(content="Article content", summary="This is a simple string summary")

        # Should raise validation error for non-structured summary
        assert "summary" in str(exc_info.value)

    def test_content_type_validation(self):
        """Test content_type field validation."""
        with pytest.raises(ValidationError):
            ArticleMetadata(
                content="Article content",
                content_type="invalid_type",  # Should be html, text, or markdown
            )


class TestPodcastMetadata:
    """Test podcast metadata validation."""

    def test_valid_podcast_metadata(self):
        """Test creating valid podcast metadata."""
        metadata = PodcastMetadata(
            audio_url="https://example.com/episode.mp3",
            transcript="This is the transcript",
            duration=3600,
            episode_number=42,
        )

        assert str(metadata.audio_url) == "https://example.com/episode.mp3"
        assert metadata.transcript == "This is the transcript"
        assert metadata.duration == 3600
        assert metadata.episode_number == 42

    def test_podcast_with_structured_summary(self):
        """Test podcast metadata with structured summary."""
        structured_summary = StructuredSummary(
            title="Technology and Innovation Podcast",
            overview=(
                "This podcast episode explores various topics related to technology "
                "and innovation with expert insights"
            ),
            bullet_points=[
                SummaryBulletPoint(text="Topic 1 discussed", category="insight"),
                SummaryBulletPoint(text="Topic 2 discussed", category="insight"),
                SummaryBulletPoint(text="Key takeaway point", category="conclusion"),
            ],
            quotes=[ContentQuote(text="This is what the guest said", context="Guest Name")],
        )

        metadata = PodcastMetadata(
            audio_url="https://example.com/episode.mp3",
            summary=structured_summary,
        )

        assert isinstance(metadata.summary, StructuredSummary)
        assert len(metadata.summary.quotes) == 1


class TestContentMetadataValidation:
    """Test the validate_content_metadata function."""

    def test_validate_article_metadata(self):
        """Test validating article metadata."""
        metadata_dict = {
            "content": "Article text",
            "author": "Author Name",
            "publication_date": datetime.now(UTC).isoformat(),
        }

        result = validate_content_metadata("article", metadata_dict)
        assert isinstance(result, ArticleMetadata)
        assert result.content == "Article text"
        assert result.author == "Author Name"

    def test_validate_podcast_metadata(self):
        """Test validating podcast metadata."""
        metadata_dict = {
            "audio_url": "https://example.com/episode.mp3",
            "transcript": "Transcript text",
            "duration": 1800,
        }

        result = validate_content_metadata("podcast", metadata_dict)
        assert isinstance(result, PodcastMetadata)
        assert str(result.audio_url) == "https://example.com/episode.mp3"
        assert result.duration == 1800

    def test_validate_unknown_content_type(self):
        """Test that unknown content types raise ValueError."""
        with pytest.raises(ValueError) as exc_info:
            validate_content_metadata("video", {})
        assert "Unknown content type" in str(exc_info.value)

    def test_validate_removes_error_fields(self):
        """Test that error fields are removed during validation."""
        metadata_dict = {
            "content": "Article text",
            "error": "Some error",
            "error_type": "non_retryable",
        }

        result = validate_content_metadata("article", metadata_dict)
        # The validated result should not have error fields
        result_dict = result.model_dump()
        assert "error" not in result_dict
        assert "error_type" not in result_dict
