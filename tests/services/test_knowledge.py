"""Tests for knowledge-library service helpers."""

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.commands import save_to_knowledge as save_to_knowledge_command
from app.models.db import ChatSession, Content, User
from app.repositories import knowledge_repository
from app.services import knowledge


def _require_id(value: int | None) -> int:
    assert value is not None
    return value


class TestKnowledgeSaveMutations:
    """Tests for save/remove knowledge mutations."""

    def test_save_to_knowledge_adds_new(
        self,
        db_session: Session,
        test_user: User,
        test_content: Content,
    ) -> None:
        """Saving should create a new knowledge save."""
        content_id = _require_id(test_content.id)
        user_id = _require_id(test_user.id)
        knowledge_save = knowledge.save_to_knowledge(
            db_session,
            content_id,
            user_id,
        )

        assert knowledge_save is not None
        assert knowledge_save.content_id == content_id
        assert knowledge_save.user_id == user_id
        assert db_session.query(ChatSession).count() == 0

    def test_remove_from_knowledge_removes_existing(
        self,
        db_session: Session,
        test_user: User,
        test_content: Content,
    ) -> None:
        """Removing should delete an existing knowledge save."""
        content_id = _require_id(test_content.id)
        user_id = _require_id(test_user.id)
        knowledge.save_to_knowledge(db_session, content_id, user_id)

        removed = knowledge.remove_from_knowledge(
            db_session,
            content_id,
            user_id,
        )

        assert removed is True
        assert not knowledge.is_saved_to_knowledge(db_session, content_id, user_id)

    def test_remove_from_knowledge_does_not_delete_existing_chat_sessions(
        self,
        db_session: Session,
        test_user: User,
        test_content: Content,
    ) -> None:
        """Removing a knowledge save should not delete a pre-existing chat session."""
        content_id = _require_id(test_content.id)
        user_id = _require_id(test_user.id)
        knowledge.save_to_knowledge(db_session, content_id, user_id)
        session = ChatSession(
            user_id=user_id,
            content_id=content_id,
            title="Existing Knowledge Chat",
            session_type="knowledge_chat",
            llm_model="openai:gpt-5.5",
            llm_provider="openai",
        )
        db_session.add(session)
        db_session.commit()

        removed = knowledge.remove_from_knowledge(
            db_session,
            content_id,
            user_id,
        )

        assert removed is True
        assert (
            db_session.query(ChatSession).filter(ChatSession.id == session.id).one_or_none()
            is not None
        )

    def test_save_to_knowledge_user_isolation(
        self,
        db_session: Session,
        test_content: Content,
        user_factory,
    ):
        """Knowledge saves should remain isolated per user."""
        user1 = user_factory(email="user1@example.com", apple_id="apple_id_1")
        user2 = user_factory(email="user2@example.com", apple_id="apple_id_2")
        content_id = _require_id(test_content.id)
        user1_id = _require_id(user1.id)
        user2_id = _require_id(user2.id)

        knowledge.save_to_knowledge(db_session, content_id, user1_id)

        assert knowledge.is_saved_to_knowledge(db_session, content_id, user1_id)
        assert not knowledge.is_saved_to_knowledge(db_session, content_id, user2_id)


class TestSaveToKnowledge:
    """Tests for creating knowledge saves."""

    def test_save_to_knowledge_success(
        self,
        db_session: Session,
        test_user: User,
        test_content: Content,
    ) -> None:
        """Saving to knowledge should create a row."""
        content_id = _require_id(test_content.id)
        user_id = _require_id(test_user.id)
        knowledge_save = knowledge.save_to_knowledge(db_session, content_id, user_id)

        # Assert
        assert knowledge_save is not None
        assert knowledge_save.content_id == content_id
        assert knowledge_save.user_id == user_id
        assert knowledge_save.saved_at is not None

    def test_save_to_knowledge_returns_existing_row(
        self,
        db_session: Session,
        test_user: User,
        test_content: Content,
    ) -> None:
        """Saving again should return the existing row."""
        content_id = _require_id(test_content.id)
        user_id = _require_id(test_user.id)
        first = knowledge.save_to_knowledge(db_session, content_id, user_id)

        # Act - try to add again
        second = knowledge.save_to_knowledge(db_session, content_id, user_id)

        # Assert - should return the same record
        assert first is not None
        assert second is not None
        assert first.id == second.id

    def test_markdown_sync_failure_rolls_back_best_effort_transaction(
        self,
        db_session: Session,
        test_user: User,
        test_content: Content,
        monkeypatch,
    ) -> None:
        """A failed optional markdown sync must not poison the caller's session."""
        content_id = _require_id(test_content.id)
        user_id = _require_id(test_user.id)

        def fail_markdown_sync(db: Session, **_kwargs) -> None:
            db.execute(text("SELECT 1 / 0"))

        monkeypatch.setattr(knowledge, "sync_personal_markdown_for_content", fail_markdown_sync)

        knowledge.save_to_knowledge(db_session, content_id, user_id)

        assert knowledge.is_saved_to_knowledge(db_session, content_id, user_id)

    def test_repository_save_to_knowledge_raises_database_errors(
        self,
        db_session: Session,
        test_user: User,
        test_content: Content,
        monkeypatch,
    ) -> None:
        """Repository failures should raise instead of collapsing into None."""
        content_id = _require_id(test_content.id)
        user_id = _require_id(test_user.id)

        def fail_flush(*args, **kwargs) -> None:
            raise RuntimeError("flush failed")

        monkeypatch.setattr(db_session, "flush", fail_flush)
        with pytest.raises(RuntimeError, match="flush failed"):
            knowledge_repository.save_to_knowledge(db_session, content_id, user_id)
        db_session.rollback()

    def test_save_command_maps_database_failure_to_http_error(
        self,
        db_session: Session,
        test_user: User,
        test_content: Content,
        monkeypatch,
    ) -> None:
        """HTTP command callers should get a failure response for save errors."""
        content_id = _require_id(test_content.id)
        user_id = _require_id(test_user.id)

        def fail_save(*args, **kwargs) -> None:
            raise RuntimeError("save failed")

        monkeypatch.setattr(
            save_to_knowledge_command.knowledge_service,
            "save_to_knowledge",
            fail_save,
        )
        with pytest.raises(HTTPException) as exc_info:
            save_to_knowledge_command.execute(db_session, user_id=user_id, content_id=content_id)

        assert exc_info.value.status_code == 500


class TestRemoveFromKnowledge:
    """Tests for removing knowledge saves."""

    def test_remove_from_knowledge_success(
        self,
        db_session: Session,
        test_user: User,
        test_content: Content,
    ) -> None:
        """Removing from knowledge should delete the row."""
        content_id = _require_id(test_content.id)
        user_id = _require_id(test_user.id)
        knowledge.save_to_knowledge(db_session, content_id, user_id)

        # Act
        removed = knowledge.remove_from_knowledge(db_session, content_id, user_id)

        # Assert
        assert removed is True
        assert not knowledge.is_saved_to_knowledge(db_session, content_id, user_id)

    def test_remove_from_knowledge_not_found(
        self,
        db_session: Session,
        test_user: User,
        test_content: Content,
    ) -> None:
        """Removing a missing knowledge save should return False."""
        content_id = _require_id(test_content.id)
        user_id = _require_id(test_user.id)
        removed = knowledge.remove_from_knowledge(db_session, content_id, user_id)

        # Assert
        assert removed is False

    def test_remove_from_knowledge_user_isolation(
        self,
        db_session: Session,
        test_content: Content,
        user_factory,
    ):
        """Removing a knowledge save should only affect the current user."""
        user1 = user_factory(email="user1@example.com", apple_id="apple_id_1")
        user2 = user_factory(email="user2@example.com", apple_id="apple_id_2")
        content_id = _require_id(test_content.id)
        user1_id = _require_id(user1.id)
        user2_id = _require_id(user2.id)

        knowledge.save_to_knowledge(db_session, content_id, user1_id)
        knowledge.save_to_knowledge(db_session, content_id, user2_id)

        # Act - remove user1's save
        knowledge.remove_from_knowledge(db_session, content_id, user1_id)

        # Assert - user1's save removed, user2's remains
        assert not knowledge.is_saved_to_knowledge(db_session, content_id, user1_id)
        assert knowledge.is_saved_to_knowledge(db_session, content_id, user2_id)


class TestListKnowledgeContentIds:
    """Tests for listing saved knowledge content ids."""

    def test_list_knowledge_content_ids_empty(self, db_session: Session, test_user: User):
        """Listing should return an empty set when nothing is saved."""
        user_id = _require_id(test_user.id)
        # Act
        content_ids = knowledge.list_knowledge_content_ids(db_session, user_id)

        # Assert
        assert content_ids == []

    def test_list_knowledge_content_ids_multiple(
        self, db_session: Session, test_user: User, test_content: Content, test_content_2: Content
    ):
        """Listing should include each saved content id."""
        first_content_id = _require_id(test_content.id)
        second_content_id = _require_id(test_content_2.id)
        user_id = _require_id(test_user.id)
        # Arrange - add knowledge saves
        knowledge.save_to_knowledge(db_session, first_content_id, user_id)
        knowledge.save_to_knowledge(db_session, second_content_id, user_id)

        # Act
        content_ids = knowledge.list_knowledge_content_ids(db_session, user_id)

        # Assert
        assert len(content_ids) == 2
        assert first_content_id in content_ids
        assert second_content_id in content_ids

    def test_list_knowledge_content_ids_user_isolation(
        self,
        db_session: Session,
        test_content: Content,
        test_content_2: Content,
        user_factory,
    ):
        """Saved content ids should remain isolated per user."""
        user1 = user_factory(email="user1@example.com", apple_id="apple_id_1")
        user2 = user_factory(email="user2@example.com", apple_id="apple_id_2")
        first_content_id = _require_id(test_content.id)
        second_content_id = _require_id(test_content_2.id)
        user1_id = _require_id(user1.id)
        user2_id = _require_id(user2.id)

        knowledge.save_to_knowledge(db_session, first_content_id, user1_id)
        knowledge.save_to_knowledge(db_session, second_content_id, user2_id)

        # Act
        user1_knowledge = knowledge.list_knowledge_content_ids(db_session, user1_id)
        user2_knowledge = knowledge.list_knowledge_content_ids(db_session, user2_id)

        # Assert
        assert user1_knowledge == [first_content_id]
        assert user2_knowledge == [second_content_id]


class TestIsSavedToKnowledge:
    """Tests for checking saved-to-knowledge state."""

    def test_is_saved_to_knowledge_true(
        self,
        db_session: Session,
        test_user: User,
        test_content: Content,
    ) -> None:
        """Checking saved-to-knowledge should return True when present."""
        content_id = _require_id(test_content.id)
        user_id = _require_id(test_user.id)
        knowledge.save_to_knowledge(db_session, content_id, user_id)

        is_saved_to_knowledge = knowledge.is_saved_to_knowledge(
            db_session,
            content_id,
            user_id,
        )

        # Assert
        assert is_saved_to_knowledge is True

    def test_is_saved_to_knowledge_false(
        self,
        db_session: Session,
        test_user: User,
        test_content: Content,
    ) -> None:
        """Checking saved-to-knowledge should return False when absent."""
        content_id = _require_id(test_content.id)
        user_id = _require_id(test_user.id)
        is_saved_to_knowledge = knowledge.is_saved_to_knowledge(
            db_session,
            content_id,
            user_id,
        )

        # Assert
        assert is_saved_to_knowledge is False


class TestClearKnowledgeLibrary:
    """Tests for clearing saved knowledge."""

    def test_clear_knowledge_library_success(
        self, db_session: Session, test_user: User, test_content: Content, test_content_2: Content
    ):
        """Clearing should remove every knowledge save for the user."""
        first_content_id = _require_id(test_content.id)
        second_content_id = _require_id(test_content_2.id)
        user_id = _require_id(test_user.id)
        # Arrange - add multiple knowledge saves
        knowledge.save_to_knowledge(db_session, first_content_id, user_id)
        knowledge.save_to_knowledge(db_session, second_content_id, user_id)

        # Act
        count = knowledge.clear_knowledge_library(db_session, user_id)

        # Assert
        assert count == 2
        assert knowledge.list_knowledge_content_ids(db_session, user_id) == []

    def test_clear_knowledge_library_empty(self, db_session: Session, test_user: User):
        """Clearing should return zero when nothing is saved."""
        user_id = _require_id(test_user.id)
        # Act
        count = knowledge.clear_knowledge_library(db_session, user_id)

        # Assert
        assert count == 0

    def test_clear_knowledge_library_user_isolation(
        self,
        db_session: Session,
        test_content: Content,
        test_content_2: Content,
        user_factory,
    ):
        """Clearing should only affect the selected user's saves."""
        user1 = user_factory(email="user1@example.com", apple_id="apple_id_1")
        user2 = user_factory(email="user2@example.com", apple_id="apple_id_2")
        first_content_id = _require_id(test_content.id)
        second_content_id = _require_id(test_content_2.id)
        user1_id = _require_id(user1.id)
        user2_id = _require_id(user2.id)

        knowledge.save_to_knowledge(db_session, first_content_id, user1_id)
        knowledge.save_to_knowledge(db_session, second_content_id, user2_id)

        # Act - clear user1's saved knowledge
        knowledge.clear_knowledge_library(db_session, user1_id)

        # Assert - user1's saves cleared, user2's remain
        assert knowledge.list_knowledge_content_ids(db_session, user1_id) == []
        assert knowledge.list_knowledge_content_ids(db_session, user2_id) == [second_content_id]
