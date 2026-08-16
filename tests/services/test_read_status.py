"""Tests for the canonical read-status repository."""

from sqlalchemy.orm import Session

from app.models.db import Content, User
from app.repositories import read_status_repository as read_status


def _require_id(value: int | None) -> int:
    assert value is not None
    return value


class TestMarkContentAsRead:
    """Tests for mark_content_as_read function."""

    def test_mark_content_as_read_success(
        self, db_session: Session, test_user: User, test_content: Content
    ):
        """Test marking content as read successfully."""
        content_id = _require_id(test_content.id)
        user_id = _require_id(test_user.id)
        # Act
        result = read_status.mark_content_as_read(db_session, content_id, user_id)

        # Assert
        assert result is not None
        assert result.content_id == content_id
        assert result.user_id == user_id
        assert result.read_at is not None

    def test_mark_content_as_read_already_read(
        self, db_session: Session, test_user: User, test_content: Content
    ):
        """Test marking already read content refreshes timestamp."""
        content_id = _require_id(test_content.id)
        user_id = _require_id(test_user.id)
        # Arrange - mark as read first
        first = read_status.mark_content_as_read(db_session, content_id, user_id)
        assert first is not None
        assert first.read_at is not None
        first_timestamp = first.read_at

        # Act - mark as read again
        second = read_status.mark_content_as_read(db_session, content_id, user_id)

        # Assert - should return same record with updated timestamp
        assert second is not None
        assert second.read_at is not None
        assert first.id == second.id
        assert second.read_at >= first_timestamp

    def test_mark_content_as_read_user_isolation(
        self,
        db_session: Session,
        test_content: Content,
        user_factory,
    ):
        """Test that read status is isolated per user."""
        user1 = user_factory(email="user1@example.com", apple_id="apple_id_1")
        user2 = user_factory(email="user2@example.com", apple_id="apple_id_2")
        content_id = _require_id(test_content.id)
        user1_id = _require_id(user1.id)
        user2_id = _require_id(user2.id)

        # Act - user1 marks as read
        read_status.mark_content_as_read(db_session, content_id, user1_id)

        # Assert - user1 has read, user2 has not
        assert read_status.is_content_read(db_session, content_id, user1_id)
        assert not read_status.is_content_read(db_session, content_id, user2_id)


class TestMarkContentsAsRead:
    """Tests for mark_contents_as_read function."""

    def test_mark_contents_as_read_empty_list(self, db_session: Session, test_user: User):
        """Test marking empty list returns zero."""
        user_id = _require_id(test_user.id)
        # Act
        marked_count, failed_ids = read_status.mark_contents_as_read(db_session, [], user_id)

        # Assert
        assert marked_count == 0
        assert failed_ids == []

    def test_in_session_variant_leaves_commit_with_caller(
        self,
        db_session: Session,
        test_user: User,
        test_content: Content,
    ) -> None:
        content_id = _require_id(test_content.id)
        user_id = _require_id(test_user.id)

        marked_count, failed_ids = read_status.mark_contents_as_read_in_session(
            db_session,
            [content_id],
            user_id,
        )

        assert marked_count == 1
        assert failed_ids == []
        assert read_status.is_content_read(db_session, content_id, user_id)
        db_session.rollback()
        assert not read_status.is_content_read(db_session, content_id, user_id)

    def test_mark_contents_as_read_multiple(
        self,
        db_session: Session,
        test_user: User,
        test_content: Content,
        test_content_2: Content,
        test_content_3: Content,
    ):
        """Test marking multiple contents as read."""
        content_ids = [
            _require_id(test_content.id),
            _require_id(test_content_2.id),
            _require_id(test_content_3.id),
        ]
        user_id = _require_id(test_user.id)
        # Act
        marked_count, failed_ids = read_status.mark_contents_as_read(
            db_session, content_ids, user_id
        )

        # Assert
        assert marked_count == 3
        assert failed_ids == []
        assert read_status.is_content_read(db_session, content_ids[0], user_id)
        assert read_status.is_content_read(db_session, content_ids[1], user_id)
        assert read_status.is_content_read(db_session, content_ids[2], user_id)

    def test_mark_contents_as_read_with_duplicates(
        self, db_session: Session, test_user: User, test_content: Content
    ):
        """Test marking contents with duplicate IDs."""
        content_id = _require_id(test_content.id)
        user_id = _require_id(test_user.id)
        # Act
        content_ids = [content_id, content_id, content_id]
        marked_count, failed_ids = read_status.mark_contents_as_read(
            db_session, content_ids, user_id
        )

        # Assert
        assert marked_count == 1  # Only one unique ID
        assert failed_ids == []

    def test_mark_contents_as_read_partial_already_read(
        self, db_session: Session, test_user: User, test_content: Content, test_content_2: Content
    ):
        """Test marking contents when some are already read."""
        first_content_id = _require_id(test_content.id)
        second_content_id = _require_id(test_content_2.id)
        user_id = _require_id(test_user.id)
        # Arrange - mark one as read first
        read_status.mark_content_as_read(db_session, first_content_id, user_id)

        # Act - mark both
        content_ids = [first_content_id, second_content_id]
        marked_count, failed_ids = read_status.mark_contents_as_read(
            db_session, content_ids, user_id
        )

        # Assert
        assert marked_count == 2
        assert failed_ids == []

    def test_mark_contents_as_read_user_isolation(
        self,
        db_session: Session,
        test_content: Content,
        test_content_2: Content,
        user_factory,
    ):
        """Test that bulk marking is isolated per user."""
        user1 = user_factory(email="user1@example.com", apple_id="apple_id_1")
        user2 = user_factory(email="user2@example.com", apple_id="apple_id_2")
        first_content_id = _require_id(test_content.id)
        second_content_id = _require_id(test_content_2.id)
        user1_id = _require_id(user1.id)
        user2_id = _require_id(user2.id)

        # Act - user1 marks content1, user2 marks content2
        read_status.mark_contents_as_read(db_session, [first_content_id], user1_id)
        read_status.mark_contents_as_read(db_session, [second_content_id], user2_id)

        # Assert
        user1_read = read_status.get_read_content_ids(db_session, user1_id)
        user2_read = read_status.get_read_content_ids(db_session, user2_id)
        assert user1_read == [first_content_id]
        assert user2_read == [second_content_id]


class TestGetReadContentIds:
    """Tests for get_read_content_ids function."""

    def test_get_read_content_ids_empty(self, db_session: Session, test_user: User):
        """Test getting read IDs when user has read nothing."""
        user_id = _require_id(test_user.id)
        # Act
        content_ids = read_status.get_read_content_ids(db_session, user_id)

        # Assert
        assert content_ids == []

    def test_get_read_content_ids_multiple(
        self, db_session: Session, test_user: User, test_content: Content, test_content_2: Content
    ):
        """Test getting read IDs when user has read multiple items."""
        first_content_id = _require_id(test_content.id)
        second_content_id = _require_id(test_content_2.id)
        user_id = _require_id(test_user.id)
        # Arrange - mark as read
        read_status.mark_content_as_read(db_session, first_content_id, user_id)
        read_status.mark_content_as_read(db_session, second_content_id, user_id)

        # Act
        content_ids = read_status.get_read_content_ids(db_session, user_id)

        # Assert
        assert len(content_ids) == 2
        assert first_content_id in content_ids
        assert second_content_id in content_ids

    def test_get_read_content_ids_user_isolation(
        self,
        db_session: Session,
        test_content: Content,
        test_content_2: Content,
        user_factory,
    ):
        """Test that read IDs are isolated per user."""
        user1 = user_factory(email="user1@example.com", apple_id="apple_id_1")
        user2 = user_factory(email="user2@example.com", apple_id="apple_id_2")
        first_content_id = _require_id(test_content.id)
        second_content_id = _require_id(test_content_2.id)
        user1_id = _require_id(user1.id)
        user2_id = _require_id(user2.id)

        read_status.mark_content_as_read(db_session, first_content_id, user1_id)
        read_status.mark_content_as_read(db_session, second_content_id, user2_id)

        # Act
        user1_read = read_status.get_read_content_ids(db_session, user1_id)
        user2_read = read_status.get_read_content_ids(db_session, user2_id)

        # Assert
        assert user1_read == [first_content_id]
        assert user2_read == [second_content_id]


class TestIsContentRead:
    """Tests for is_content_read function."""

    def test_is_content_read_true(
        self, db_session: Session, test_user: User, test_content: Content
    ):
        """Test checking if content is read returns True when it is."""
        content_id = _require_id(test_content.id)
        user_id = _require_id(test_user.id)
        # Arrange
        read_status.mark_content_as_read(db_session, content_id, user_id)

        # Act
        is_read = read_status.is_content_read(db_session, content_id, user_id)

        # Assert
        assert is_read is True

    def test_is_content_read_false(
        self, db_session: Session, test_user: User, test_content: Content
    ):
        """Test checking if content is read returns False when it isn't."""
        content_id = _require_id(test_content.id)
        user_id = _require_id(test_user.id)
        # Act
        is_read = read_status.is_content_read(db_session, content_id, user_id)

        # Assert
        assert is_read is False


class TestClearReadStatus:
    """Tests for clear_read_status function."""

    def test_clear_read_status_success(
        self, db_session: Session, test_user: User, test_content: Content, test_content_2: Content
    ):
        """Test clearing all read status for a user."""
        first_content_id = _require_id(test_content.id)
        second_content_id = _require_id(test_content_2.id)
        user_id = _require_id(test_user.id)
        # Arrange - mark multiple as read
        read_status.mark_content_as_read(db_session, first_content_id, user_id)
        read_status.mark_content_as_read(db_session, second_content_id, user_id)

        # Act
        count = read_status.clear_read_status(db_session, user_id)

        # Assert
        assert count == 2
        assert read_status.get_read_content_ids(db_session, user_id) == []

    def test_clear_read_status_empty(self, db_session: Session, test_user: User):
        """Test clearing read status when user has none."""
        user_id = _require_id(test_user.id)
        # Act
        count = read_status.clear_read_status(db_session, user_id)

        # Assert
        assert count == 0

    def test_clear_read_status_user_isolation(
        self,
        db_session: Session,
        test_content: Content,
        test_content_2: Content,
        user_factory,
    ):
        """Test that clearing read status only affects specific user."""
        user1 = user_factory(email="user1@example.com", apple_id="apple_id_1")
        user2 = user_factory(email="user2@example.com", apple_id="apple_id_2")
        first_content_id = _require_id(test_content.id)
        second_content_id = _require_id(test_content_2.id)
        user1_id = _require_id(user1.id)
        user2_id = _require_id(user2.id)

        read_status.mark_content_as_read(db_session, first_content_id, user1_id)
        read_status.mark_content_as_read(db_session, second_content_id, user2_id)

        # Act - clear user1's read status
        read_status.clear_read_status(db_session, user1_id)

        # Assert - user1's cleared, user2's remains
        assert read_status.get_read_content_ids(db_session, user1_id) == []
        assert read_status.get_read_content_ids(db_session, user2_id) == [second_content_id]
