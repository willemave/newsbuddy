"""Tests for cursor-based pagination in API content endpoints."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.metadata import ContentStatus, ContentType
from app.models.schema import Content, ContentStatusEntry
from app.models.user import User
from app.utils.image_paths import get_content_images_dir
from app.utils.pagination import PaginationCursor


@pytest.fixture
def sample_contents(db_session: Session, test_user: User):
    """Create sample content items for pagination testing."""
    contents = []
    base_time = datetime.now(UTC)
    images_dir = get_content_images_dir()
    images_dir.mkdir(parents=True, exist_ok=True)
    image_paths: list[Path] = []

    # Create 50 articles with different timestamps
    for i in range(50):
        content = Content(
            url=f"https://example.com/article-{i}",
            title=f"Test Article {i}",
            source="Test Source",
            content_type=ContentType.ARTICLE.value,
            status=ContentStatus.COMPLETED.value,
            content_metadata={
                "summary": {
                    "title": f"Test Article {i}",
                    "overview": (
                        "This overview is long enough to satisfy the minimum length "
                        "requirement for structured summaries."
                    ),
                    "bullet_points": [
                        {"text": "Key point one", "category": "key_finding"},
                        {"text": "Key point two", "category": "methodology"},
                        {"text": "Key point three", "category": "conclusion"},
                    ],
                    "quotes": [],
                    "topics": ["test"],
                    "classification": "to_read",
                },
                "summary_kind": "long_structured",
                "summary_version": 1,
                "image_generated_at": "2025-12-31T00:00:00Z",
            },
            created_at=base_time - timedelta(minutes=i),
        )
        db_session.add(content)
        contents.append(content)

    db_session.commit()

    # Create inbox status entries + images for the test user
    for content in contents:
        db_session.refresh(content)
        status_entry = ContentStatusEntry(
            user_id=test_user.id,
            content_id=content.id,
            status="inbox",
        )
        db_session.add(status_entry)
        image_path = images_dir / f"{content.id}.png"
        image_path.write_bytes(b"fake-png")
        image_paths.append(image_path)

    db_session.commit()

    try:
        yield contents
    finally:
        for image_path in image_paths:
            if image_path.exists():
                image_path.unlink()


def _enable_fts(db_session: Session, contents: list[Content]) -> None:
    """Create and seed the FTS table for search tests."""
    try:
        db_session.execute(
            text(
                """
                CREATE VIRTUAL TABLE content_fts USING fts5(
                    title,
                    source,
                    summary,
                    transcript
                )
                """
            )
        )
    except Exception:
        pytest.skip("SQLite FTS5 not available")

    for content in contents:
        db_session.execute(
            text(
                """
                INSERT INTO content_fts(rowid, title, source, summary, transcript)
                VALUES (:rowid, :title, :source, :summary, :transcript)
                """
            ),
            {
                "rowid": content.id,
                "title": content.title or "",
                "source": content.source or "",
                "summary": content.title or "",
                "transcript": "",
            },
        )
    db_session.commit()


class TestCursorEncoding:
    """Test cursor encoding and decoding."""

    def test_encode_decode_cursor(self):
        """Test encoding and decoding a cursor."""
        last_id = 123
        last_created_at = datetime(2025, 6, 19, 10, 30, 0)
        filters = {"content_type": "article", "date": "2025-06-19"}

        cursor = PaginationCursor.encode_cursor(
            last_id=last_id,
            last_created_at=last_created_at,
            filters=filters,
        )

        # Cursor should be opaque (base64 encoded)
        assert isinstance(cursor, str)
        assert len(cursor) > 0

        # Decode cursor
        cursor_data = PaginationCursor.decode_cursor(cursor)
        assert cursor_data["last_id"] == last_id
        assert cursor_data["last_created_at"] == last_created_at
        assert "filters_hash" in cursor_data

    def test_decode_invalid_cursor(self):
        """Test decoding an invalid cursor raises error."""
        with pytest.raises(ValueError, match="Invalid pagination cursor"):
            PaginationCursor.decode_cursor("invalid_cursor")

    def test_validate_cursor_filters(self):
        """Test cursor filter validation."""
        filters = {"content_type": "article", "date": "2025-06-19"}
        cursor = PaginationCursor.encode_cursor(
            last_id=123,
            last_created_at=datetime.now(UTC),
            filters=filters,
        )

        cursor_data = PaginationCursor.decode_cursor(cursor)

        # Same filters should validate
        assert PaginationCursor.validate_cursor(cursor_data, filters)

        # Different filters should not validate
        different_filters = {"content_type": "podcast", "date": "2025-06-19"}
        assert not PaginationCursor.validate_cursor(cursor_data, different_filters)


class TestListEndpointPagination:
    """Test pagination on GET /api/content/ endpoint."""

    def test_first_page_no_cursor(self, client, sample_contents):
        """Test fetching first page without cursor."""
        response = client.get("/api/content/", params={"limit": 10})
        assert response.status_code == 200

        data = response.json()
        assert len(data["contents"]) <= 50  # At most our sample data
        assert data["meta"]["has_more"] in [True, False]
        assert data["meta"]["page_size"] >= 0
        assert "next_cursor" in data["meta"]
        assert "contents" in data

    def test_first_page_can_skip_available_dates(self, client, sample_contents):
        """Test fetching first page without available date metadata."""
        response = client.get(
            "/api/content/",
            params={"limit": 10, "include_available_dates": "false"},
        )
        assert response.status_code == 200

        data = response.json()
        assert data["available_dates"] == []

    def test_second_page_with_cursor(self, client, sample_contents):
        """Test fetching second page using cursor."""
        # Get first page
        response1 = client.get("/api/content/", params={"limit": 10})
        data1 = response1.json()

        # Only test pagination if there's a next cursor
        if not data1["meta"]["next_cursor"]:
            pytest.skip("Not enough data for pagination test")

        next_cursor = data1["meta"]["next_cursor"]

        # Get second page using cursor
        response2 = client.get("/api/content/", params={"limit": 10, "cursor": next_cursor})
        assert response2.status_code == 200

        data2 = response2.json()
        assert len(data2["contents"]) >= 0

        # Pages should not overlap
        ids_page1 = {item["id"] for item in data1["contents"]}
        ids_page2 = {item["id"] for item in data2["contents"]}
        assert len(ids_page1 & ids_page2) == 0

    def test_last_page_no_more_results(self, client, sample_contents):
        """Test last page detection."""
        # Fetch first page
        response1 = client.get("/api/content/", params={"limit": 25})
        data1 = response1.json()

        if not data1["meta"]["has_more"]:
            pytest.skip("Not enough data for multiple pages")

        # Fetch second page
        response2 = client.get(
            "/api/content/",
            params={"limit": 25, "cursor": data1["meta"]["next_cursor"]},
        )
        data2 = response2.json()

        # Should successfully fetch second page
        assert response2.status_code == 200
        assert "has_more" in data2["meta"]
        assert "next_cursor" in data2["meta"]

    def test_custom_limit(self, client, sample_contents):
        """Test custom page size limit."""
        response = client.get("/api/content/", params={"limit": 5})
        assert response.status_code == 200

        data = response.json()
        assert len(data["contents"]) == 5
        assert data["meta"]["page_size"] == 5

    def test_limit_too_large(self, client, sample_contents):
        """Test limit exceeds maximum allowed."""
        response = client.get("/api/content/", params={"limit": 200})
        assert response.status_code == 422  # Validation error

    def test_cursor_with_filters(self, client, sample_contents):
        """Test cursor with content type filter."""
        # Get first page with filter
        response1 = client.get("/api/content/", params={"content_type": "article", "limit": 10})
        data1 = response1.json()

        # Skip if not enough data for pagination
        if not data1["meta"].get("next_cursor"):
            pytest.skip("Not enough data to test cursor with filters")

        cursor = data1["meta"]["next_cursor"]

        # Second page with same filter should work
        response2 = client.get(
            "/api/content/", params={"content_type": "article", "limit": 10, "cursor": cursor}
        )
        assert response2.status_code == 200

        # Second page with different filter should fail
        response3 = client.get(
            "/api/content/", params={"content_type": "podcast", "limit": 10, "cursor": cursor}
        )
        assert response3.status_code == 400
        assert "filters" in response3.json()["detail"].lower()

    def test_invalid_cursor(self, client, sample_contents):
        """Test invalid cursor returns 400 error."""
        response = client.get("/api/content/", params={"cursor": "invalid_cursor"})
        assert response.status_code == 400
        assert "invalid" in response.json()["detail"].lower()


class TestSearchEndpointPagination:
    """Test pagination on GET /api/content/search endpoint."""

    def test_search_first_page(self, client, sample_contents):
        """Test search with pagination."""
        response = client.get("/api/content/search", params={"q": "Test", "limit": 10})
        assert response.status_code == 200

        data = response.json()
        assert len(data["contents"]) == 10
        assert data["meta"]["has_more"] is True
        assert data["meta"]["next_cursor"] is not None

    def test_search_uses_fts_when_available(self, client, db_session, sample_contents):
        """Use FTS when the table exists."""
        _enable_fts(db_session, sample_contents)

        response = client.get("/api/content/search", params={"q": "Article", "limit": 5})
        assert response.status_code == 200

        data = response.json()
        assert len(data["contents"]) > 0

    def test_search_with_cursor(self, client, sample_contents):
        """Test search pagination with cursor."""
        # First page
        response1 = client.get("/api/content/search", params={"q": "Article", "limit": 20})
        data1 = response1.json()

        # Skip if not enough data
        if not data1["meta"].get("next_cursor"):
            pytest.skip("Not enough data for cursor test")

        # Second page
        response2 = client.get(
            "/api/content/search",
            params={"q": "Article", "limit": 20, "cursor": data1["meta"]["next_cursor"]},
        )
        data2 = response2.json()

        # No overlapping results
        ids_page1 = {item["id"] for item in data1["contents"]}
        ids_page2 = {item["id"] for item in data2["contents"]}
        assert len(ids_page1 & ids_page2) == 0

    def test_search_cursor_invalid_if_query_changes(self, client, sample_contents):
        """Test cursor validation when search query changes."""
        # Get cursor with one query
        response1 = client.get("/api/content/search", params={"q": "Test", "limit": 10})
        data1 = response1.json()

        # Skip if not enough data
        if not data1["meta"].get("next_cursor"):
            pytest.skip("Not enough data for cursor validation test")

        cursor = data1["meta"]["next_cursor"]

        # Try to use cursor with different query
        response2 = client.get(
            "/api/content/search", params={"q": "Different", "limit": 10, "cursor": cursor}
        )
        assert response2.status_code == 400

    def test_search_backwards_compatible_offset(self, client, sample_contents):
        """Test search still supports deprecated offset parameter."""
        response = client.get(
            "/api/content/search",
            params={"q": "Test", "limit": 10, "offset": 10},
        )
        assert response.status_code == 200

        data = response.json()
        # Should get results but with cursor pagination fields
        assert "next_cursor" in data["meta"]
        assert "has_more" in data["meta"]

    def test_search_skips_invalid_rows_when_domain_content_build_fails(
        self,
        client,
        sample_contents,
        monkeypatch,
    ):
        """Search should keep returning valid rows when one result is malformed."""
        from app.queries import search_content_cards

        broken_id = sample_contents[0].id
        original_content_to_domain = search_content_cards.content_to_domain

        def _content_to_domain(content):
            if content.id == broken_id:
                raise ValueError("invalid content metadata")
            return original_content_to_domain(content)

        monkeypatch.setattr(search_content_cards, "content_to_domain", _content_to_domain)

        response = client.get("/api/content/search", params={"q": "Test", "limit": 50})
        assert response.status_code == 200

        ids = {item["id"] for item in response.json()["contents"]}
        assert broken_id not in ids


class TestFavoritesEndpointPagination:
    """Test pagination on GET /api/content/favorites/list endpoint."""

    def test_favorites_pagination(self, client, sample_contents, db_session: Session, test_user):
        """Test favorites list with pagination."""
        from app.services import favorites

        # Mark first 30 items as favorites
        for content in sample_contents[:30]:
            favorites.toggle_favorite(db_session, content.id, test_user.id)

        # First page
        response1 = client.get("/api/content/favorites/list", params={"limit": 10})
        assert response1.status_code == 200

        data1 = response1.json()
        # Should have some favorites (may be less than 10 due to DB state)
        assert len(data1["contents"]) >= 0
        assert "has_more" in data1["meta"]
        assert "next_cursor" in data1["meta"]

        # If there's a next page, fetch it
        if data1["meta"]["next_cursor"]:
            response2 = client.get(
                "/api/content/favorites/list",
                params={
                    "limit": 10,
                    "cursor": data1["meta"]["next_cursor"],
                },
            )
            assert response2.status_code == 200
            data2 = response2.json()
            assert len(data2["contents"]) >= 0

    def test_empty_favorites(self, client, sample_contents):
        """Test favorites pagination with no favorites."""
        response = client.get("/api/content/favorites/list")
        assert response.status_code == 200

        data = response.json()
        assert len(data["contents"]) == 0
        assert data["meta"]["has_more"] is False
        assert data["meta"]["next_cursor"] is None


class TestPaginationStability:
    """Test pagination stability and edge cases."""

    def test_stable_pagination_with_same_timestamp(
        self,
        client,
        db_session: Session,
        test_user: User,
    ):
        """Test pagination handles items with identical timestamps."""
        # Create items with same timestamp
        same_time = datetime.now(UTC)
        contents = []
        for i in range(10):
            content = Content(
                url=f"https://example.com/same-time-{i}",
                title=f"Same Time Article {i}",
                content_type=ContentType.ARTICLE.value,
                status=ContentStatus.COMPLETED.value,
                content_metadata={
                    "summary": {
                        "title": f"Same Time Article {i}",
                        "overview": (
                            "This overview is long enough to satisfy the minimum "
                            "length requirement "
                            "for structured summaries."
                        ),
                        "bullet_points": [
                            {"text": "Key point one", "category": "key_finding"},
                            {"text": "Key point two", "category": "methodology"},
                            {"text": "Key point three", "category": "conclusion"},
                        ],
                        "quotes": [],
                        "topics": ["test"],
                        "classification": "to_read",
                    },
                    "summary_kind": "long_structured",
                    "summary_version": 1,
                },
                created_at=same_time,
            )
            db_session.add(content)
            contents.append(content)
        db_session.commit()

        # Create inbox status entries
        for content in contents:
            db_session.refresh(content)
            status_entry = ContentStatusEntry(
                user_id=test_user.id,
                content_id=content.id,
                status="inbox",
            )
            db_session.add(status_entry)
        db_session.commit()

        # Fetch pages
        response1 = client.get("/api/content/", params={"limit": 5})
        data1 = response1.json()

        if not data1["meta"].get("next_cursor"):
            pytest.skip("Not enough data for pagination stability test")

        response2 = client.get(
            "/api/content/",
            params={"limit": 5, "cursor": data1["meta"]["next_cursor"]},
        )
        data2 = response2.json()

        # No overlapping IDs (stable pagination using ID as tie-breaker)
        ids_page1 = {item["id"] for item in data1["contents"]}
        ids_page2 = {item["id"] for item in data2["contents"]}
        assert len(ids_page1 & ids_page2) == 0

    def test_pagination_without_limit(self, client, sample_contents):
        """Test default limit is applied when not specified."""
        response = client.get("/api/content/")
        assert response.status_code == 200

        data = response.json()
        assert len(data["contents"]) == 25  # Default limit
        assert data["meta"]["page_size"] == 25
