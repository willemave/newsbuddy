from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.settings import get_settings
from app.models.contracts import ContentStatus, ContentType
from app.models.db import (
    AgentDataFile,
    Content,
    ContentKnowledgeSave,
    ContentStatusEntry,
)
from app.services.agent_data_documents import (
    collect_agent_data_documents,
    next_agent_data_backfill_page,
)
from app.services.agent_data_sync import (
    AgentDataSyncSelection,
    next_agent_data_reconcile_page,
    publish_agent_data_index,
    sync_agent_data_for_user,
)


def _visible_content(db_session, *, user_id: int, title: str, body: str) -> Content:
    content = Content(
        url=f"https://example.com/{title.lower().replace(' ', '-')}",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        title=title,
        source="Example",
        content_metadata={
            "content": body,
            "summary": {"full_markdown": f"Summary of {title}."},
        },
    )
    db_session.add(content)
    db_session.flush()
    db_session.add(
        ContentStatusEntry(
            user_id=user_id,
            content_id=content.id,
            status="inbox",
        )
    )
    db_session.flush()
    return content


def _configure_local_mirror(monkeypatch, tmp_path: Path) -> Path:
    settings = get_settings()
    root = tmp_path / "agent-data"
    monkeypatch.setattr(settings, "agent_data_mirror_root", root)
    return root


def test_incremental_sync_moves_saved_content_and_publishes_complete_manifest(
    db_session,
    test_user,
    monkeypatch,
    tmp_path: Path,
) -> None:
    mirror_root = _configure_local_mirror(monkeypatch, tmp_path)
    content = _visible_content(
        db_session,
        user_id=test_user.id,
        title="Persistent Agent Data",
        body="A durable article body.",
    )
    db_session.commit()
    assert content.id is not None

    first = sync_agent_data_for_user(
        db_session,
        user_id=test_user.id,
        selection=AgentDataSyncSelection(content_ids=frozenset({content.id})),
    )
    db_session.commit()

    assert len(first.written_paths) == 1
    original_path = first.written_paths[0]
    assert original_path.startswith("content/")
    assert (mirror_root / str(test_user.id) / original_path).is_file()
    rendered = (mirror_root / str(test_user.id) / original_path).read_text(encoding="utf-8")
    assert "## Summary" in rendered
    assert "## Content" in rendered

    db_session.add(ContentKnowledgeSave(user_id=test_user.id, content_id=content.id))
    db_session.commit()
    moved = sync_agent_data_for_user(
        db_session,
        user_id=test_user.id,
        selection=AgentDataSyncSelection(content_ids=frozenset({content.id})),
    )
    db_session.commit()

    assert moved.deleted_paths == (original_path,)
    assert len(moved.written_paths) == 1
    saved_path = moved.written_paths[0]
    assert saved_path.startswith("knowledge/")
    assert not (mirror_root / str(test_user.id) / original_path).exists()
    assert (mirror_root / str(test_user.id) / saved_path).is_file()

    unchanged = sync_agent_data_for_user(
        db_session,
        user_id=test_user.id,
        selection=AgentDataSyncSelection(content_ids=frozenset({content.id})),
    )
    assert unchanged.written_paths == ()
    assert unchanged.deleted_paths == ()

    published = publish_agent_data_index(
        db_session,
        user_id=test_user.id,
        mark_complete=True,
    )
    db_session.commit()
    user_root = mirror_root / str(test_user.id)
    manifest = json.loads((user_root / "manifest.json").read_text(encoding="utf-8"))
    index_rows = [
        json.loads(line)
        for line in (user_root / "index.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert published.written_paths == ("index.jsonl", "manifest.json")
    assert manifest["complete"] is True
    assert manifest["revision"] == unchanged.revision
    assert index_rows[0]["path"] == saved_path
    assert index_rows[0]["saved"] is True
    assert db_session.query(AgentDataFile).filter_by(
        user_id=test_user.id, document_kind="content", document_key=str(content.id)
    ).one().stale_paths == [original_path]


def test_agent_document_body_is_utf8_safe_and_byte_bounded(
    db_session,
    test_user,
    monkeypatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "agent_data_document_max_bytes", 10_000)
    content = _visible_content(
        db_session,
        user_id=test_user.id,
        title="Large Body",
        body="🛰️" * 10_000,
    )
    db_session.commit()
    assert content.id is not None

    documents = collect_agent_data_documents(
        db_session,
        user_id=test_user.id,
        content_ids={content.id},
    )

    assert len(documents) == 1
    document = documents[0]
    assert document.byte_size <= 10_000
    assert "document truncated" in document.text
    document.text.encode("utf-8").decode("utf-8")


def test_backfill_pages_knowledge_before_other_visible_content(
    db_session,
    test_user,
) -> None:
    saved = _visible_content(
        db_session,
        user_id=test_user.id,
        title="Saved First",
        body="saved",
    )
    ordinary = _visible_content(
        db_session,
        user_id=test_user.id,
        title="Ordinary Second",
        body="ordinary",
    )
    db_session.flush()
    db_session.add(ContentKnowledgeSave(user_id=test_user.id, content_id=saved.id))
    db_session.commit()
    assert saved.id is not None
    assert ordinary.id is not None

    first = next_agent_data_backfill_page(
        db_session,
        user_id=test_user.id,
        stage=None,
        before_id=None,
        limit=10,
    )
    assert first is not None
    assert first.stage == "knowledge"
    assert first.ids == (saved.id,)

    second = next_agent_data_backfill_page(
        db_session,
        user_id=test_user.id,
        stage=first.next_stage,
        before_id=first.next_before_id,
        limit=10,
    )
    assert second is not None
    assert second.stage == "content"
    assert second.ids == (ordinary.id,)


def test_backfill_rejects_unknown_stage(db_session, test_user) -> None:
    with pytest.raises(ValueError, match="Unsupported agent-data backfill stage"):
        next_agent_data_backfill_page(
            db_session,
            user_id=test_user.id,
            stage="unknown",  # type: ignore[arg-type]
            before_id=None,
            limit=10,
        )


def test_reconcile_page_repairs_corrupt_file_in_bounded_slice(
    db_session,
    test_user,
    monkeypatch,
    tmp_path: Path,
) -> None:
    mirror_root = _configure_local_mirror(monkeypatch, tmp_path)
    content = _visible_content(
        db_session,
        user_id=test_user.id,
        title="Repair Me",
        body="canonical body",
    )
    db_session.commit()
    assert content.id is not None
    initial = sync_agent_data_for_user(
        db_session,
        user_id=test_user.id,
        selection=AgentDataSyncSelection(content_ids=frozenset({content.id})),
    )
    db_session.commit()
    target = mirror_root / str(test_user.id) / initial.written_paths[0]
    target.write_text("corrupt", encoding="utf-8")

    page = next_agent_data_reconcile_page(
        db_session,
        user_id=test_user.id,
        before_id=None,
        limit=1,
    )
    assert page is not None
    assert page.selection.content_ids == frozenset({content.id})
    repaired = sync_agent_data_for_user(
        db_session,
        user_id=test_user.id,
        selection=page.selection,
    )

    assert repaired.written_paths == initial.written_paths
    assert "canonical body" in target.read_text(encoding="utf-8")
