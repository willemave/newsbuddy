"""Tests for favorites service."""

from sqlalchemy.orm import Session

from app.models.schema import ChatSession, Content, User
from app.services import favorites


class TestToggleFavorite:
    """Tests for toggle_favorite function."""

    def test_toggle_favorite_adds_new(
        self,
        db_session: Session,
        test_user: User,
        test_content: Content,
    ) -> None:
        """Test toggling favorite adds new favorite."""
        is_favorited, favorite = favorites.toggle_favorite(
            db_session,
            test_content.id,
            test_user.id,
        )

        # Assert
        assert is_favorited is True
        assert favorite is not None
        assert favorite.content_id == test_content.id
        assert favorite.user_id == test_user.id
        assert db_session.query(ChatSession).count() == 0

    def test_toggle_favorite_removes_existing(
        self,
        db_session: Session,
        test_user: User,
        test_content: Content,
    ) -> None:
        """Test toggling favorite removes existing favorite."""
        favorites.add_favorite(db_session, test_content.id, test_user.id)

        is_favorited, favorite = favorites.toggle_favorite(
            db_session,
            test_content.id,
            test_user.id,
        )

        # Assert
        assert is_favorited is False
        assert favorite is None

        # Verify it's actually gone
        assert not favorites.is_content_favorited(db_session, test_content.id, test_user.id)

    def test_toggle_favorite_does_not_delete_existing_chat_sessions(
        self,
        db_session: Session,
        test_user: User,
        test_content: Content,
    ) -> None:
        """Removing a favorite should not delete a pre-existing chat session."""
        favorites.add_favorite(db_session, test_content.id, test_user.id)
        session = ChatSession(
            user_id=test_user.id,
            content_id=test_content.id,
            title="Existing Knowledge Chat",
            session_type="knowledge_chat",
            llm_model="openai:gpt-5.4",
            llm_provider="openai",
        )
        db_session.add(session)
        db_session.commit()

        is_favorited, favorite = favorites.toggle_favorite(
            db_session,
            test_content.id,
            test_user.id,
        )

        assert is_favorited is False
        assert favorite is None
        assert (
            db_session.query(ChatSession).filter(ChatSession.id == session.id).one_or_none()
            is not None
        )

    def test_toggle_favorite_user_isolation(
        self,
        db_session: Session,
        test_content: Content,
        user_factory,
    ):
        """Test that favorites are isolated per user."""
        user1 = user_factory(email="user1@example.com", apple_id="apple_id_1")
        user2 = user_factory(email="user2@example.com", apple_id="apple_id_2")

        # Act - user1 favorites content
        favorites.toggle_favorite(db_session, test_content.id, user1.id)

        # Assert - user1 has favorited, user2 has not
        assert favorites.is_content_favorited(db_session, test_content.id, user1.id)
        assert not favorites.is_content_favorited(db_session, test_content.id, user2.id)


class TestAddFavorite:
    """Tests for add_favorite function."""

    def test_add_favorite_success(
        self,
        db_session: Session,
        test_user: User,
        test_content: Content,
    ) -> None:
        """Test adding a favorite successfully."""
        favorite = favorites.add_favorite(db_session, test_content.id, test_user.id)

        # Assert
        assert favorite is not None
        assert favorite.content_id == test_content.id
        assert favorite.user_id == test_user.id
        assert favorite.favorited_at is not None

    def test_add_favorite_already_exists(
        self,
        db_session: Session,
        test_user: User,
        test_content: Content,
    ) -> None:
        """Test adding favorite that already exists returns existing record."""
        first = favorites.add_favorite(db_session, test_content.id, test_user.id)

        # Act - try to add again
        second = favorites.add_favorite(db_session, test_content.id, test_user.id)

        # Assert - should return the same record
        assert second is not None
        assert first.id == second.id


class TestRemoveFavorite:
    """Tests for remove_favorite function."""

    def test_remove_favorite_success(
        self,
        db_session: Session,
        test_user: User,
        test_content: Content,
    ) -> None:
        """Test removing a favorite successfully."""
        favorites.add_favorite(db_session, test_content.id, test_user.id)

        # Act
        removed = favorites.remove_favorite(db_session, test_content.id, test_user.id)

        # Assert
        assert removed is True
        assert not favorites.is_content_favorited(db_session, test_content.id, test_user.id)

    def test_remove_favorite_not_found(
        self,
        db_session: Session,
        test_user: User,
        test_content: Content,
    ) -> None:
        """Test removing non-existent favorite returns False."""
        removed = favorites.remove_favorite(db_session, test_content.id, test_user.id)

        # Assert
        assert removed is False

    def test_remove_favorite_user_isolation(
        self,
        db_session: Session,
        test_content: Content,
        user_factory,
    ):
        """Test that removing favorite only affects specific user."""
        user1 = user_factory(email="user1@example.com", apple_id="apple_id_1")
        user2 = user_factory(email="user2@example.com", apple_id="apple_id_2")

        favorites.add_favorite(db_session, test_content.id, user1.id)
        favorites.add_favorite(db_session, test_content.id, user2.id)

        # Act - remove user1's favorite
        favorites.remove_favorite(db_session, test_content.id, user1.id)

        # Assert - user1's favorite removed, user2's remains
        assert not favorites.is_content_favorited(db_session, test_content.id, user1.id)
        assert favorites.is_content_favorited(db_session, test_content.id, user2.id)


class TestGetFavoriteContentIds:
    """Tests for get_favorite_content_ids function."""

    def test_get_favorite_content_ids_empty(self, db_session: Session, test_user: User):
        """Test getting favorite IDs when user has no favorites."""
        # Act
        content_ids = favorites.get_favorite_content_ids(db_session, test_user.id)

        # Assert
        assert content_ids == []

    def test_get_favorite_content_ids_multiple(
        self, db_session: Session, test_user: User, test_content: Content, test_content_2: Content
    ):
        """Test getting favorite IDs when user has multiple favorites."""
        # Arrange - add favorites
        favorites.add_favorite(db_session, test_content.id, test_user.id)
        favorites.add_favorite(db_session, test_content_2.id, test_user.id)

        # Act
        content_ids = favorites.get_favorite_content_ids(db_session, test_user.id)

        # Assert
        assert len(content_ids) == 2
        assert test_content.id in content_ids
        assert test_content_2.id in content_ids

    def test_get_favorite_content_ids_user_isolation(
        self,
        db_session: Session,
        test_content: Content,
        test_content_2: Content,
        user_factory,
    ):
        """Test that favorite IDs are isolated per user."""
        user1 = user_factory(email="user1@example.com", apple_id="apple_id_1")
        user2 = user_factory(email="user2@example.com", apple_id="apple_id_2")

        favorites.add_favorite(db_session, test_content.id, user1.id)
        favorites.add_favorite(db_session, test_content_2.id, user2.id)

        # Act
        user1_favorites = favorites.get_favorite_content_ids(db_session, user1.id)
        user2_favorites = favorites.get_favorite_content_ids(db_session, user2.id)

        # Assert
        assert user1_favorites == [test_content.id]
        assert user2_favorites == [test_content_2.id]


class TestIsContentFavorited:
    """Tests for is_content_favorited function."""

    def test_is_content_favorited_true(
        self,
        db_session: Session,
        test_user: User,
        test_content: Content,
    ) -> None:
        """Test checking if content is favorited returns True when it is."""
        favorites.add_favorite(db_session, test_content.id, test_user.id)

        is_favorited = favorites.is_content_favorited(db_session, test_content.id, test_user.id)

        # Assert
        assert is_favorited is True

    def test_is_content_favorited_false(
        self,
        db_session: Session,
        test_user: User,
        test_content: Content,
    ) -> None:
        """Test checking if content is favorited returns False when it isn't."""
        is_favorited = favorites.is_content_favorited(db_session, test_content.id, test_user.id)

        # Assert
        assert is_favorited is False


class TestClearFavorites:
    """Tests for clear_favorites function."""

    def test_clear_favorites_success(
        self, db_session: Session, test_user: User, test_content: Content, test_content_2: Content
    ):
        """Test clearing all favorites for a user."""
        # Arrange - add multiple favorites
        favorites.add_favorite(db_session, test_content.id, test_user.id)
        favorites.add_favorite(db_session, test_content_2.id, test_user.id)

        # Act
        count = favorites.clear_favorites(db_session, test_user.id)

        # Assert
        assert count == 2
        assert favorites.get_favorite_content_ids(db_session, test_user.id) == []

    def test_clear_favorites_empty(self, db_session: Session, test_user: User):
        """Test clearing favorites when user has none."""
        # Act
        count = favorites.clear_favorites(db_session, test_user.id)

        # Assert
        assert count == 0

    def test_clear_favorites_user_isolation(
        self,
        db_session: Session,
        test_content: Content,
        test_content_2: Content,
        user_factory,
    ):
        """Test that clearing favorites only affects specific user."""
        user1 = user_factory(email="user1@example.com", apple_id="apple_id_1")
        user2 = user_factory(email="user2@example.com", apple_id="apple_id_2")

        favorites.add_favorite(db_session, test_content.id, user1.id)
        favorites.add_favorite(db_session, test_content_2.id, user2.id)

        # Act - clear user1's favorites
        favorites.clear_favorites(db_session, user1.id)

        # Assert - user1's favorites cleared, user2's remain
        assert favorites.get_favorite_content_ids(db_session, user1.id) == []
        assert favorites.get_favorite_content_ids(db_session, user2.id) == [test_content_2.id]
