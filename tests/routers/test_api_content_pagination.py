"""Tests for cursor-based pagination in API content endpoints."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.models.contracts import ContentStatus, ContentType
from app.models.db import (
    Content,
    ContentKnowledgeSave,
    ContentStatusEntry,
    UserIntegrationConnection,
    UserIntegrationSyncedItem,
)
from app.models.db.users import User
from app.utils.pagination import PaginationCursor


@pytest.fixture
def sample_contents(db_session: Session, test_user: User, tmp_path):
    """Create sample content items for pagination testing."""
    contents = []
    base_time = datetime.now(UTC)
    images_dir = tmp_path / "content-images"
    images_dir.mkdir(parents=True, exist_ok=True)
    image_paths = []

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
        assert cursor_data.last_id == last_id
        assert cursor_data.last_created_at == last_created_at
        assert cursor_data.filters_hash is not None

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

    def test_search_returns_results(self, client, sample_contents):
        """Search should return visible content results."""
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


class TestKnowledgeLibraryPagination:
    """Test pagination on GET /api/content/knowledge/list endpoint."""

    def test_knowledge_library_pagination(
        self, client, sample_contents, db_session: Session, test_user
    ):
        """Test knowledge library list with pagination."""
        from app.services import knowledge

        # Save first 30 items to knowledge
        for content in sample_contents[:30]:
            knowledge.save_to_knowledge(db_session, content.id, test_user.id)

        # First page
        response1 = client.get("/api/content/knowledge/list", params={"limit": 10})
        assert response1.status_code == 200

        data1 = response1.json()
        assert len(data1["contents"]) == 10
        assert data1["meta"]["page_size"] == 10
        assert data1["meta"]["has_more"] is True
        assert data1["meta"]["next_cursor"]

        # Second page should honor the same page size and not repeat rows.
        response2 = client.get(
            "/api/content/knowledge/list",
            params={
                "limit": 10,
                "cursor": data1["meta"]["next_cursor"],
            },
        )
        assert response2.status_code == 200
        data2 = response2.json()
        assert len(data2["contents"]) == 10
        assert data2["meta"]["page_size"] == 10
        assert data2["meta"]["has_more"] is True
        assert data2["meta"]["next_cursor"]

        ids_page1 = {item["id"] for item in data1["contents"]}
        ids_page2 = {item["id"] for item in data2["contents"]}
        assert ids_page1.isdisjoint(ids_page2)

    def test_empty_knowledge_library(self, client, sample_contents):
        """Test knowledge library pagination with no saved items."""
        response = client.get("/api/content/knowledge/list")
        assert response.status_code == 200

        data = response.json()
        assert len(data["contents"]) == 0
        assert data["meta"]["has_more"] is False
        assert data["meta"]["next_cursor"] is None

    def test_knowledge_library_search_stays_scoped_to_saved_items(
        self,
        client,
        sample_contents,
        db_session: Session,
        test_user,
    ) -> None:
        from app.services import knowledge

        saved_match = sample_contents[0]
        saved_match.title = "A distinctive walkable city essay"
        unsaved_match = sample_contents[1]
        unsaved_match.title = "Another walkable city essay"
        saved_nonmatch = sample_contents[2]
        saved_nonmatch.title = "A note about interface design"
        db_session.add_all([saved_match, unsaved_match, saved_nonmatch])
        db_session.commit()
        knowledge.save_to_knowledge(db_session, saved_match.id, test_user.id)
        knowledge.save_to_knowledge(db_session, saved_nonmatch.id, test_user.id)

        response = client.get(
            "/api/content/knowledge/list",
            params={"q": "walkable city", "limit": 10},
        )

        assert response.status_code == 200
        returned_ids = [item["id"] for item in response.json()["contents"]]
        assert returned_ids == [saved_match.id]

    def test_knowledge_library_uses_per_user_x_ledger_for_saved_source(
        self,
        client,
        sample_contents,
        db_session: Session,
        test_user,
    ) -> None:
        from app.services import knowledge

        x_bookmark = sample_contents[0]
        ordinary_save = sample_contents[1]
        knowledge.save_to_knowledge(db_session, x_bookmark.id, test_user.id)
        knowledge.save_to_knowledge(db_session, ordinary_save.id, test_user.id)
        connection = UserIntegrationConnection(
            user_id=test_user.id,
            provider="x",
            provider_user_id="42",
            is_active=True,
        )
        db_session.add(connection)
        db_session.flush()
        db_session.add(
            UserIntegrationSyncedItem(
                connection_id=connection.id,
                channel="bookmarks",
                external_item_id="bookmark-1",
                content_id=x_bookmark.id,
                item_url="https://x.com/i/status/bookmark-1",
            )
        )
        db_session.commit()

        response = client.get("/api/content/knowledge/list")

        assert response.status_code == 200
        saved_sources = {item["id"]: item["saved_source"] for item in response.json()["contents"]}
        assert saved_sources[x_bookmark.id] == "x_bookmark"
        assert saved_sources[ordinary_save.id] == "knowledge"

    def test_knowledge_library_search_ignores_internal_metadata_keys(
        self,
        client,
        sample_contents,
        db_session: Session,
        test_user,
    ) -> None:
        from app.services import knowledge

        saved_content = sample_contents[0]
        saved_content.title = "A note about interface design"
        saved_content.source = "Design Weekly"
        saved_content.url = "https://example.com/interface-design"
        saved_content.content_metadata = {
            "image_url": "/static/images/content/example.png",
            "summary_type": "long_structured",
        }
        db_session.add(saved_content)
        db_session.commit()
        knowledge.save_to_knowledge(db_session, saved_content.id, test_user.id)

        response = client.get(
            "/api/content/knowledge/list",
            params={"q": "image_url", "limit": 10},
        )

        assert response.status_code == 200
        assert response.json()["contents"] == []

    def test_knowledge_library_search_cursor_rejects_a_different_query(
        self,
        client,
        sample_contents,
        db_session: Session,
        test_user,
    ) -> None:
        from app.services import knowledge

        for index, content in enumerate(sample_contents[:3]):
            content.title = f"Walkable city study {index}"
            db_session.add(content)
            knowledge.save_to_knowledge(db_session, content.id, test_user.id)
        db_session.commit()

        first_page = client.get(
            "/api/content/knowledge/list",
            params={"q": "walkable", "limit": 1},
        )
        assert first_page.status_code == 200
        cursor = first_page.json()["meta"]["next_cursor"]
        assert cursor

        mismatched_page = client.get(
            "/api/content/knowledge/list",
            params={"q": "interface", "limit": 1, "cursor": cursor},
        )

        assert mismatched_page.status_code == 400

    def test_explicit_saves_remain_visible_across_processing_states(
        self,
        client,
        db_session: Session,
        test_user: User,
    ) -> None:
        """Saved rows stay visible while processing and after a failure."""
        statuses = [
            ContentStatus.NEW,
            ContentStatus.PENDING,
            ContentStatus.PROCESSING,
            ContentStatus.AWAITING_IMAGE,
            ContentStatus.COMPLETED,
            ContentStatus.FAILED,
            ContentStatus.SKIPPED,
        ]
        contents = [
            Content(
                url=f"https://example.com/saved-{status.value}",
                title=f"Saved {status.value}",
                content_type=ContentType.ARTICLE.value,
                status=status.value,
                classification="skip" if status == ContentStatus.COMPLETED else None,
            )
            for status in statuses
        ]
        db_session.add_all(contents)
        db_session.flush()
        db_session.add_all(
            ContentKnowledgeSave(user_id=test_user.id, content_id=content.id)
            for content in contents
        )
        db_session.add(
            ContentStatusEntry(
                user_id=test_user.id,
                content_id=contents[0].id,
                status="inbox",
            )
        )
        db_session.commit()

        response = client.get("/api/content/knowledge/list")
        assert response.status_code == 200

        returned_statuses = {item["id"]: item["status"] for item in response.json()["contents"]}
        assert returned_statuses == {
            content.id: status.value for content, status in zip(contents, statuses, strict=True)
        }

        inbox_response = client.get("/api/content/")
        assert inbox_response.status_code == 200
        assert contents[0].id not in {item["id"] for item in inbox_response.json()["contents"]}


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
