from datetime import datetime

from pydantic import HttpUrl

from app.models.content_mapper import content_to_domain, domain_to_content
from app.models.metadata import ContentData, ContentStatus, ContentType
from app.models.schema import Content as DBContent


class TestContentToDomain:
    """Test converting database Content to domain ContentData."""

    def test_convert_article_content(self):
        """Test converting article content to domain model."""
        # Create database content
        db_content = DBContent(
            id=123,
            content_type=ContentType.ARTICLE.value,
            url="https://example.com/article",
            title="Test Article",
            status=ContentStatus.NEW.value,
            content_metadata={
                "author": "John Doe",
                "word_count": 1500,
                "publication_date": "2025-06-14T12:00:00",
            },
            error_message=None,
            retry_count=0,
            created_at=datetime(2025, 6, 14, 12, 0, 0),
            processed_at=None,
        )

        # Convert to domain
        domain_content = content_to_domain(db_content)

        # Verify conversion
        assert domain_content.id == 123
        assert domain_content.content_type == ContentType.ARTICLE
        assert str(domain_content.url) == "https://example.com/article"
        assert domain_content.title == "Test Article"
        assert domain_content.status == ContentStatus.NEW
        assert domain_content.metadata["author"] == "John Doe"
        assert domain_content.metadata["word_count"] == 1500
        assert domain_content.error_message is None
        assert domain_content.retry_count == 0
        assert domain_content.created_at == datetime(2025, 6, 14, 12, 0, 0)
        assert domain_content.processed_at is None

    def test_convert_podcast_content(self):
        """Test converting podcast content to domain model."""
        db_content = DBContent(
            id=456,
            content_type=ContentType.PODCAST.value,
            url="https://example.com/podcast/episode1",
            title="Test Podcast Episode",
            status=ContentStatus.COMPLETED.value,
            content_metadata={
                "audio_url": "https://example.com/audio.mp3",
                "duration_seconds": 3600,
                "episode_number": 1,
                "transcript": "This is the transcript...",
            },
            retry_count=0,
            created_at=datetime(2025, 6, 14, 10, 0, 0),
            processed_at=datetime(2025, 6, 14, 11, 0, 0),
        )

        domain_content = content_to_domain(db_content)

        assert domain_content.id == 456
        assert domain_content.content_type == ContentType.PODCAST
        assert domain_content.status == ContentStatus.COMPLETED
        assert domain_content.metadata["audio_url"] == "https://example.com/audio.mp3"
        assert domain_content.metadata["duration_seconds"] == 3600
        assert domain_content.metadata["transcript"] == "This is the transcript..."
        assert domain_content.processed_at == datetime(2025, 6, 14, 11, 0, 0)

    def test_convert_failed_content(self):
        """Test converting failed content with error message."""
        db_content = DBContent(
            id=789,
            content_type=ContentType.ARTICLE.value,
            url="https://example.com/failed",
            title="Failed Article",
            status=ContentStatus.FAILED.value,
            content_metadata={},
            error_message="Network timeout",
            retry_count=3,
            created_at=datetime(2025, 6, 14, 9, 0, 0),
            processed_at=None,
        )

        domain_content = content_to_domain(db_content)

        assert domain_content.status == ContentStatus.FAILED
        assert domain_content.error_message == "Network timeout"
        assert domain_content.retry_count == 3
        assert domain_content.metadata == {}

    def test_convert_content_with_empty_metadata(self):
        """Test converting content with None/empty metadata."""
        db_content = DBContent(
            id=999,
            content_type=ContentType.ARTICLE.value,
            url="https://example.com/empty",
            status=ContentStatus.NEW.value,
            content_metadata=None,  # None metadata
        )

        domain_content = content_to_domain(db_content)

        assert domain_content.metadata == {}

    def test_convert_x_digest_news_preserves_user_scoped_url(self):
        """X digest news should keep the per-user internal URL instead of the tweet URL."""
        db_content = DBContent(
            id=1001,
            content_type=ContentType.NEWS.value,
            url="https://x.com/i/status/123#newsly-digest-user-1",
            source_url="https://x.com/i/status/123",
            status=ContentStatus.NEW.value,
            content_metadata={
                "source_type": "x_timeline",
                "tweet_id": "123",
                "article": {
                    "url": "https://x.com/i/status/123",
                    "title": "Digest tweet",
                    "source_domain": "x.com",
                },
            },
        )

        domain_content = content_to_domain(db_content)

        assert str(domain_content.url) == "https://x.com/i/status/123#newsly-digest-user-1"


class TestDomainToContent:
    """Test converting domain ContentData to database Content."""

    def test_convert_new_article_domain(self):
        """Test converting new article domain model to database."""
        domain_content = ContentData(
            content_type=ContentType.ARTICLE,
            url=HttpUrl("https://example.com/new-article"),
            title="New Article",
            status=ContentStatus.NEW,
            metadata={"author": "Jane Smith", "source": "blog", "tags": ["tech", "AI"]},
            created_at=datetime(2025, 6, 14, 14, 0, 0),
        )

        db_content = domain_to_content(domain_content)

        assert db_content.content_type == ContentType.ARTICLE.value
        assert db_content.url == "https://example.com/new-article"
        assert db_content.title == "New Article"
        assert db_content.status == ContentStatus.NEW.value
        assert db_content.content_metadata["author"] == "Jane Smith"
        assert db_content.content_metadata["tags"] == ["tech", "AI"]
        assert db_content.created_at == datetime(2025, 6, 14, 14, 0, 0)
        assert db_content.retry_count == 0

    def test_convert_podcast_domain(self):
        """Test converting podcast domain model to database."""
        domain_content = ContentData(
            content_type=ContentType.PODCAST,
            url=HttpUrl("https://example.com/podcast/ep2"),
            title="Podcast Episode 2",
            status=ContentStatus.PROCESSING,
            metadata={
                "audio_url": "https://example.com/ep2.mp3",
                "episode_number": 2,
                "duration_seconds": 2400,
            },
        )

        db_content = domain_to_content(domain_content)

        assert db_content.content_type == ContentType.PODCAST.value
        assert db_content.status == ContentStatus.PROCESSING.value
        assert db_content.content_metadata["audio_url"] == "https://example.com/ep2.mp3"
        assert db_content.content_metadata["episode_number"] == 2

    def test_update_existing_content(self):
        """Test updating existing database content from domain model."""
        # Existing database content
        existing_db = DBContent(
            id=123,
            content_type=ContentType.ARTICLE.value,
            url="https://example.com/existing",
            title="Old Title",
            status=ContentStatus.PROCESSING.value,
            content_metadata={"old": "data"},
            retry_count=1,
            created_at=datetime(2025, 6, 14, 10, 0, 0),
        )

        # Updated domain content
        updated_domain = ContentData(
            id=123,
            content_type=ContentType.ARTICLE,
            url=HttpUrl("https://example.com/existing"),
            title="Updated Title",
            status=ContentStatus.COMPLETED,
            metadata={"author": "Updated Author", "word_count": 2000},
            processed_at=datetime(2025, 6, 14, 11, 30, 0),
        )

        # Update existing
        result = domain_to_content(updated_domain, existing=existing_db)

        # Should return the same object, updated
        assert result is existing_db
        assert result.title == "Updated Title"
        assert result.status == ContentStatus.COMPLETED.value
        assert result.content_metadata["author"] == "Updated Author"
        assert result.content_metadata["word_count"] == 2000
        assert result.processed_at == datetime(2025, 6, 14, 11, 30, 0)
        assert result.updated_at is not None
        # Original fields should be preserved
        assert result.id == 123
        assert result.created_at == datetime(2025, 6, 14, 10, 0, 0)

    def test_convert_failed_domain_content(self):
        """Test converting failed domain content."""
        domain_content = ContentData(
            content_type=ContentType.ARTICLE,
            url=HttpUrl("https://example.com/failed-domain"),
            title="Failed Content",
            status=ContentStatus.FAILED,
            metadata={},
            error_message="Processing failed",
            retry_count=2,
        )

        db_content = domain_to_content(domain_content)

        assert db_content.status == ContentStatus.FAILED.value
        assert db_content.error_message == "Processing failed"
        assert db_content.retry_count == 2

    def test_convert_with_complex_metadata(self):
        """Test converting content with complex nested metadata."""
        domain_content = ContentData(
            content_type=ContentType.ARTICLE,
            url=HttpUrl("https://example.com/complex"),
            metadata={
                "author": "Complex Author",
                "tags": ["tag1", "tag2", "tag3"],
                "stats": {"word_count": 1500, "reading_time": 7, "complexity_score": 0.75},
                "dates": {"published": "2025-06-14", "updated": "2025-06-15"},
            },
        )

        db_content = domain_to_content(domain_content)

        # Verify complex metadata is preserved
        assert db_content.content_metadata["author"] == "Complex Author"
        assert db_content.content_metadata["tags"] == ["tag1", "tag2", "tag3"]
        assert db_content.content_metadata["stats"]["word_count"] == 1500
        assert db_content.content_metadata["stats"]["complexity_score"] == 0.75
        assert db_content.content_metadata["dates"]["published"] == "2025-06-14"


class TestConverterRoundTrip:
    """Test round-trip conversions between domain and database models."""

    def test_article_round_trip(self):
        """Test domain -> db -> domain conversion for article."""
        original_domain = ContentData(
            content_type=ContentType.ARTICLE,
            url=HttpUrl("https://example.com/roundtrip"),
            title="Round Trip Article",
            status=ContentStatus.COMPLETED,
            metadata={
                "author": "Round Trip Author",
                "word_count": 1800,
                "tags": ["test", "roundtrip"],
            },
            retry_count=1,
            created_at=datetime(2025, 6, 14, 12, 0, 0),
            processed_at=datetime(2025, 6, 14, 13, 0, 0),
        )

        # Convert to database model
        db_content = domain_to_content(original_domain)
        db_content.id = 999  # Simulate database assignment

        # Convert back to domain
        final_domain = content_to_domain(db_content)

        # Verify round-trip preservation
        assert final_domain.id == 999
        assert final_domain.content_type == original_domain.content_type
        assert str(final_domain.url) == str(original_domain.url)
        assert final_domain.title == original_domain.title
        assert final_domain.status == original_domain.status
        # Check that original metadata fields are preserved (model may add defaults)
        assert final_domain.metadata["author"] == original_domain.metadata["author"]
        assert final_domain.metadata["word_count"] == original_domain.metadata["word_count"]
        assert final_domain.metadata["tags"] == original_domain.metadata["tags"]
        assert final_domain.retry_count == original_domain.retry_count
        assert final_domain.created_at == original_domain.created_at
        assert final_domain.processed_at == original_domain.processed_at

    def test_podcast_round_trip(self):
        """Test domain -> db -> domain conversion for podcast."""
        original_domain = ContentData(
            content_type=ContentType.PODCAST,
            url=HttpUrl("https://example.com/podcast-roundtrip"),
            title="Round Trip Podcast",
            status=ContentStatus.NEW,
            metadata={
                "audio_url": "https://example.com/audio-roundtrip.mp3",
                "episode_number": 42,
                "duration_seconds": 5400,
                "host": "Test Host",
            },
        )

        # Round trip
        db_content = domain_to_content(original_domain)
        db_content.id = 888
        final_domain = content_to_domain(db_content)

        # Verify preservation
        assert final_domain.content_type == ContentType.PODCAST
        assert final_domain.metadata["audio_url"] == "https://example.com/audio-roundtrip.mp3"
        assert final_domain.metadata["episode_number"] == 42
        assert final_domain.metadata["duration_seconds"] == 5400
        assert final_domain.metadata["host"] == "Test Host"


class TestConverterEdgeCases:
    """Test edge cases and error conditions."""

    def test_convert_minimal_domain_content(self):
        """Test converting domain content with minimal fields."""
        minimal_domain = ContentData(
            content_type=ContentType.ARTICLE, url=HttpUrl("https://example.com/minimal")
        )

        db_content = domain_to_content(minimal_domain)

        assert db_content.content_type == ContentType.ARTICLE.value
        assert db_content.url == "https://example.com/minimal"
        assert db_content.title is None
        assert db_content.status == ContentStatus.NEW.value
        assert db_content.content_metadata.get("domain") == {}
        assert db_content.content_metadata.get("processing") == {}
        # Legacy compatibility field persisted by metadata adapter.
        assert db_content.content_metadata.get("content_type") == "html"
        assert db_content.retry_count == 0
        assert db_content.error_message is None

    def test_convert_minimal_db_content(self):
        """Test converting database content with minimal fields."""
        minimal_db = DBContent(
            content_type=ContentType.PODCAST.value,
            url="https://example.com/minimal-podcast",
            status=ContentStatus.NEW.value,
        )

        domain_content = content_to_domain(minimal_db)

        assert domain_content.content_type == ContentType.PODCAST
        assert str(domain_content.url) == "https://example.com/minimal-podcast"
        assert domain_content.title is None
        assert domain_content.status == ContentStatus.NEW
        assert domain_content.metadata == {}
        assert domain_content.retry_count == 0

    def test_update_preserves_database_fields(self):
        """Test that updating preserves database-specific fields."""
        existing_db = DBContent(
            id=555,
            content_type=ContentType.ARTICLE.value,
            url="https://example.com/preserve",
            title="Original",
            status=ContentStatus.NEW.value,
            checked_out_by="worker-1",
            checked_out_at=datetime(2025, 6, 14, 10, 0, 0),
            created_at=datetime(2025, 6, 14, 9, 0, 0),
        )

        updated_domain = ContentData(
            content_type=ContentType.ARTICLE,
            url=HttpUrl("https://example.com/preserve"),
            title="Updated",
            status=ContentStatus.PROCESSING,
        )

        result = domain_to_content(updated_domain, existing=existing_db)

        # Updated fields
        assert result.title == "Updated"
        assert result.status == ContentStatus.PROCESSING.value

        # Preserved database-specific fields
        assert result.id == 555
        assert result.checked_out_by == "worker-1"
        assert result.checked_out_at == datetime(2025, 6, 14, 10, 0, 0)
        assert result.created_at == datetime(2025, 6, 14, 9, 0, 0)
        assert result.updated_at is not None
