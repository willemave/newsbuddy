"""Account purge coverage and behavior tests."""

from __future__ import annotations

from app.core.db import Base
from app.models.contracts import TaskQueue, TaskStatus, TaskType
from app.models.db import ChatMessage, ChatSession, ContentKnowledgeSave, ProcessingTask, User
from app.services import account_deletion


def test_user_owned_model_registry_covers_every_direct_owner_column() -> None:
    discovered: set[tuple[type, str]] = set()
    for mapper in Base.registry.mappers:
        model = mapper.class_
        for column_name in ("user_id", "owner_user_id"):
            if column_name in mapper.columns:
                discovered.add((model, column_name))

    assert set(account_deletion.USER_OWNED_MODELS) == discovered


def test_purge_user_account_removes_direct_indirect_and_file_data(
    db_session,
    test_user,
    tmp_path,
    monkeypatch,
) -> None:
    chat = ChatSession(user_id=test_user.id, title="Private", llm_model="test", llm_provider="test")
    db_session.add(chat)
    db_session.flush()
    chat_id = chat.id
    db_session.add(ChatMessage(session_id=chat.id, message_list="[]"))
    db_session.add(ContentKnowledgeSave(user_id=test_user.id, content_id=123))
    deletion_task = ProcessingTask(
        task_type=TaskType.DELETE_USER_ACCOUNT.value,
        queue_name=TaskQueue.BACKFILL.value,
        status=TaskStatus.PROCESSING.value,
        payload={"user_id": test_user.id},
        locked_at=None,
    )
    # Keep the fixture task pending while testing the purge service directly;
    # the worker owns the processing transition in production.
    deletion_task.status = TaskStatus.PENDING.value
    db_session.add(deletion_task)
    user_root = tmp_path / str(test_user.id)
    user_root.mkdir()
    (user_root / "private.md").write_text("private", encoding="utf-8")
    monkeypatch.setattr(account_deletion, "get_personal_markdown_user_root", lambda _id: user_root)
    db_session.commit()
    assert deletion_task.id is not None

    account_deletion.purge_user_account(
        db_session,
        user_id=test_user.id,
        current_task_id=deletion_task.id,
    )

    assert db_session.query(User).filter(User.id == test_user.id).first() is None
    assert db_session.query(ChatSession).filter(ChatSession.user_id == test_user.id).count() == 0
    assert db_session.query(ChatMessage).filter(ChatMessage.session_id == chat_id).count() == 0
    assert (
        db_session.query(ContentKnowledgeSave)
        .filter(ContentKnowledgeSave.user_id == test_user.id)
        .count()
        == 0
    )
    db_session.refresh(deletion_task)
    assert deletion_task.payload == {}
    assert not user_root.exists()
