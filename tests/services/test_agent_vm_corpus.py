from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from app.core.settings import get_settings
from app.models.contracts import ContentStatus, ContentType
from app.models.db import Content, ContentStatusEntry
from app.services.agent_data_sync import (
    AgentDataSyncSelection,
    publish_agent_data_index,
    sync_agent_data_for_user,
)
from app.services.agent_vm_corpus import (
    AgentDataRevisionError,
    _install_command,
    materialize_agent_data_transfer,
)


def _content(db_session, *, user_id: int) -> Content:
    content = Content(
        url="https://example.com/delta",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        title="Original title",
        source="Example",
        content_metadata={
            "content": "Original body",
            "summary": {"full_markdown": "Original summary"},
        },
    )
    db_session.add(content)
    db_session.flush()
    db_session.add(ContentStatusEntry(user_id=user_id, content_id=content.id, status="inbox"))
    db_session.flush()
    return content


def _archive_json(archive: tarfile.TarFile, name: str):
    member = archive.extractfile(name)
    assert member is not None
    return json.loads(member.read())


def test_recovery_transfer_is_full_once_then_revision_delta(
    db_session,
    test_user,
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(get_settings(), "agent_data_mirror_root", tmp_path / "agent-data")
    content = _content(db_session, user_id=test_user.id)
    db_session.commit()
    assert content.id is not None

    first = sync_agent_data_for_user(
        db_session,
        user_id=test_user.id,
        selection=AgentDataSyncSelection(content_ids=frozenset({content.id})),
    )
    publish_agent_data_index(db_session, user_id=test_user.id, mark_complete=True)
    db_session.commit()

    with materialize_agent_data_transfer(
        db_session,
        user_id=test_user.id,
        remote_revision=0,
    ) as transfer:
        assert transfer is not None
        assert transfer.full is True
        assert transfer.from_revision == 0
        assert transfer.to_revision == first.revision
        with tarfile.open(transfer.archive_path, "r:gz") as archive:
            names = set(archive.getnames())
            assert "index_full.jsonl" in names
            assert f"files/{first.written_paths[0]}" in names
            assert _archive_json(archive, "transfer.json")["full"] is True

    original_path = first.written_paths[0]
    content.title = "Renamed title"
    content.content_metadata = {
        "content": "Changed body",
        "summary": {"full_markdown": "Changed summary"},
    }
    db_session.commit()
    changed = sync_agent_data_for_user(
        db_session,
        user_id=test_user.id,
        selection=AgentDataSyncSelection(content_ids=frozenset({content.id})),
    )
    publish_agent_data_index(db_session, user_id=test_user.id)
    db_session.commit()

    assert changed.revision > first.revision
    assert changed.deleted_paths == (original_path,)
    with materialize_agent_data_transfer(
        db_session,
        user_id=test_user.id,
        remote_revision=first.revision,
    ) as transfer:
        assert transfer is not None
        assert transfer.full is False
        assert transfer.changed_file_count == 1
        assert transfer.deleted_path_count == 1
        with tarfile.open(transfer.archive_path, "r:gz") as archive:
            names = set(archive.getnames())
            assert "index_upserts.jsonl" in names
            assert "index_full.jsonl" not in names
            assert f"files/{changed.written_paths[0]}" in names
            assert _archive_json(archive, "deletions.json") == [original_path]


def test_transfer_rejects_remote_revision_ahead_of_host(
    db_session,
    test_user,
) -> None:
    with (
        pytest.raises(AgentDataRevisionError, match="ahead of host revision"),
        materialize_agent_data_transfer(
            db_session,
            user_id=test_user.id,
            remote_revision=1,
        ),
    ):
        pass


def test_transfer_skips_when_remote_revision_is_current(
    db_session,
    test_user,
) -> None:
    with materialize_agent_data_transfer(
        db_session,
        user_id=test_user.id,
        remote_revision=0,
    ) as transfer:
        assert transfer is None


def test_embedded_corpus_installer_is_valid_python() -> None:
    command = _install_command("/tmp/corpus.tar.gz")
    source = command.split("<<'PY'\n", maxsplit=1)[1].rsplit("\nPY", maxsplit=1)[0]

    compile(source, "agent-vm-corpus-installer", "exec")
    assert "for path in sorted(root.rglob" not in source
