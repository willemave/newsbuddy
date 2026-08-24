"""Account purge coverage and behavior tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.db import Base
from app.models.contracts import TaskQueue, TaskStatus, TaskType
from app.models.db import (
    ChatMessage,
    ChatSession,
    ContentKnowledgeSave,
    ProcessingTask,
    User,
)
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
    agent_data_root = tmp_path / "agent-data" / str(test_user.id)
    agent_data_root.mkdir(parents=True)
    (agent_data_root / "manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(account_deletion, "get_personal_markdown_user_root", lambda _id: user_root)
    monkeypatch.setattr(account_deletion, "get_agent_data_user_root", lambda _id: agent_data_root)
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
    assert not agent_data_root.exists()


def test_destroy_agent_vm_removes_sandbox_and_snapshot(monkeypatch) -> None:
    calls: list[tuple[object, ...]] = []

    class FakeSandbox:
        @classmethod
        def kill(cls, sandbox_id: str, **kwargs) -> None:
            calls.append(("kill", sandbox_id, kwargs))

        @classmethod
        def delete_snapshot(cls, snapshot_id: str, **kwargs) -> None:
            calls.append(("delete_snapshot", snapshot_id, kwargs))

    settings = type("Settings", (), {"llm_task_sandbox_e2b_api_key": "secret"})()
    monkeypatch.setattr("app.core.settings.get_settings", lambda: settings)
    monkeypatch.setitem(
        __import__("sys").modules,
        "e2b_code_interpreter",
        type("E2BCodeInterpreter", (), {"Sandbox": FakeSandbox}),
    )
    user = User(agent_vm_sandbox_id="sandbox-1", agent_vm_snapshot_id="snapshot-1")

    account_deletion._destroy_agent_vm(user)

    assert calls == [
        ("kill", "sandbox-1", {"api_key": "secret"}),
        ("delete_snapshot", "snapshot-1", {"api_key": "secret"}),
    ]


def test_destroy_agent_vm_requires_key_before_discarding_external_ids(monkeypatch) -> None:
    settings = type("Settings", (), {"llm_task_sandbox_e2b_api_key": ""})()
    monkeypatch.setattr("app.core.settings.get_settings", lambda: settings)
    user = User(agent_vm_sandbox_id="sandbox-1", agent_vm_snapshot_id="snapshot-1")

    with pytest.raises(RuntimeError, match="E2B_API_KEY is required"):
        account_deletion._destroy_agent_vm(user)


def test_cancel_pending_user_tasks_uses_explicit_owner_not_payload(
    db_session,
    test_user,
) -> None:
    owned_pending = ProcessingTask(
        owner_user_id=test_user.id,
        task_type=TaskType.DIG_DEEPER.value,
        queue_name=TaskQueue.CHAT.value,
        status=TaskStatus.PENDING.value,
        payload={"user_id": test_user.id},
    )
    shared_payload_only = ProcessingTask(
        task_type=TaskType.ANALYZE_URL.value,
        queue_name=TaskQueue.CONTENT.value,
        status=TaskStatus.PENDING.value,
        payload={"user_id": test_user.id},
    )
    owned_processing = ProcessingTask(
        owner_user_id=test_user.id,
        task_type=TaskType.DIG_DEEPER.value,
        queue_name=TaskQueue.CHAT.value,
        status=TaskStatus.PROCESSING.value,
        payload={"user_id": test_user.id},
        locked_at=datetime.now(UTC).replace(tzinfo=None),
        locked_by="test-worker",
        lease_expires_at=(datetime.now(UTC) + timedelta(minutes=1)).replace(tzinfo=None),
    )
    deletion_task = ProcessingTask(
        task_type=TaskType.DELETE_USER_ACCOUNT.value,
        queue_name=TaskQueue.BACKFILL.value,
        status=TaskStatus.PENDING.value,
        payload={"user_id": test_user.id},
    )
    db_session.add_all([owned_pending, shared_payload_only, owned_processing, deletion_task])
    db_session.flush()
    deletion_task_id = deletion_task.id
    assert deletion_task_id is not None

    assert (
        account_deletion.cancel_pending_user_tasks(
            db_session,
            user_id=test_user.id,
            current_task_id=deletion_task_id,
        )
        is False
    )
    assert db_session.query(ProcessingTask).filter_by(id=owned_pending.id).count() == 0
    assert db_session.query(ProcessingTask).filter_by(id=shared_payload_only.id).count() == 1

    owned_processing.status = TaskStatus.COMPLETED.value
    owned_processing.locked_at = None
    owned_processing.locked_by = None
    owned_processing.lease_token = None
    owned_processing.lease_expires_at = None
    db_session.flush()
    assert (
        account_deletion.cancel_pending_user_tasks(
            db_session,
            user_id=test_user.id,
            current_task_id=deletion_task_id,
        )
        is True
    )


def test_purge_scrubs_only_deleted_user_from_shared_content_metadata(
    db_session,
    test_user,
    user_factory,
    content_factory,
    tmp_path,
    monkeypatch,
) -> None:
    other_user = user_factory()
    content = content_factory(
        content_metadata={
            "submitted_by_user_id": test_user.id,
            "share_and_chat_user_ids": [test_user.id, other_user.id],
            "share_and_chat_requests": [
                {"user_id": test_user.id, "initial_message": "private"},
                {"user_id": other_user.id, "initial_message": "keep"},
            ],
            "processing": {
                "submitted_by_user_id": test_user.id,
                "share_and_chat_user_ids": [test_user.id, other_user.id],
                "share_and_chat_requests": [
                    {"user_id": test_user.id},
                    {"user_id": other_user.id},
                ],
            },
        }
    )
    deletion_task = ProcessingTask(
        task_type=TaskType.DELETE_USER_ACCOUNT.value,
        queue_name=TaskQueue.BACKFILL.value,
        status=TaskStatus.PENDING.value,
        payload={"user_id": test_user.id},
    )
    db_session.add(deletion_task)
    db_session.commit()
    deletion_task_id = deletion_task.id
    assert deletion_task_id is not None
    monkeypatch.setattr(
        account_deletion,
        "get_personal_markdown_user_root",
        lambda _id: tmp_path / "missing",
    )

    account_deletion.purge_user_account(
        db_session,
        user_id=test_user.id,
        current_task_id=deletion_task_id,
    )

    db_session.refresh(content)
    assert "submitted_by_user_id" not in content.content_metadata
    assert content.content_metadata["share_and_chat_user_ids"] == [other_user.id]
    assert content.content_metadata["share_and_chat_requests"] == [
        {"user_id": other_user.id, "initial_message": "keep"}
    ]
    processing = content.content_metadata["processing"]
    assert "submitted_by_user_id" not in processing
    assert processing["share_and_chat_user_ids"] == [other_user.id]
    assert processing["share_and_chat_requests"] == [{"user_id": other_user.id}]
