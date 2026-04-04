"""Tests for the per-user personal markdown library."""

from datetime import UTC, datetime
from pathlib import Path

from app.core.settings import get_settings
from app.models.schema import ChatSession, Content
from app.repositories import favorites_repository
from app.services.personal_markdown_library import (
    get_personal_markdown_user_root,
    sync_personal_markdown_for_content,
    sync_personal_markdown_library_for_user,
)


def _make_content() -> Content:
    content = Content(
        content_type="article",
        url="https://example.com/how-agents-work",
        title="How Agents Work",
        source="New York Times",
        publication_date=datetime(2026, 4, 3, 8, 0, 0, tzinfo=UTC).replace(tzinfo=None),
        status="completed",
        content_metadata={
            "content": "Raw body text from the article.",
            "summary": {
                "title": "How Agents Work",
                "overview": "A compact summary of how agents work.",
                "full_markdown": "# How Agents Work\n\nA compact summary of how agents work.\n",
                "bullet_points": [
                    {"text": "Agents coordinate tools.", "category": "key_finding"},
                    {"text": "Execution needs boundaries.", "category": "risk"},
                ],
            },
        },
    )
    return content


def test_sync_personal_markdown_library_writes_source_and_summary_files(
    db_session,
    test_user,
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "personal_markdown_root", tmp_path / "personal_markdown")
    monkeypatch.setattr(settings, "personal_markdown_enabled", True)

    content = _make_content()
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    favorites_repository.add_favorite(db_session, content.id, test_user.id)

    result = sync_personal_markdown_library_for_user(db_session, user_id=test_user.id)

    assert result.written_files
    user_root = get_personal_markdown_user_root(test_user.id)
    source_path = (
        user_root
        / "article"
        / "new-york-times"
        / f"how-agents-work__2026-04-03__source__c{content.id}.md"
    )
    summary_path = (
        user_root
        / "article"
        / "new-york-times"
        / f"how-agents-work__2026-04-03__summary__c{content.id}.md"
    )
    assert source_path.exists()
    assert summary_path.exists()

    source_text = source_path.read_text(encoding="utf-8")
    summary_text = summary_path.read_text(encoding="utf-8")
    assert "variant: source" in source_text
    assert "reasons:" in source_text
    assert "- favorited" in source_text
    assert "Raw body text from the article." in source_text
    assert "variant: summary" in summary_text
    assert "# How Agents Work" in summary_text


def test_sync_personal_markdown_for_content_keeps_files_when_chat_session_exists(
    db_session,
    test_user,
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "personal_markdown_root", tmp_path / "personal_markdown")
    monkeypatch.setattr(settings, "personal_markdown_enabled", True)

    content = _make_content()
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    favorites_repository.add_favorite(db_session, content.id, test_user.id)
    chat_session = ChatSession(
        user_id=test_user.id,
        content_id=content.id,
        title=content.title,
        session_type="knowledge_chat",
        llm_model="openai:gpt-5.4",
        llm_provider="openai",
    )
    db_session.add(chat_session)
    db_session.commit()

    favorites_repository.remove_favorite(db_session, content.id, test_user.id)
    result = sync_personal_markdown_for_content(
        db_session,
        user_id=test_user.id,
        content_id=content.id,
    )

    assert result.written_files
    summary_path = (
        get_personal_markdown_user_root(test_user.id)
        / "article"
        / "new-york-times"
        / f"how-agents-work__2026-04-03__summary__c{content.id}.md"
    )
    assert summary_path.exists()
    assert "- chatted" in summary_path.read_text(encoding="utf-8")


def test_sync_personal_markdown_for_content_deletes_files_when_no_reasons_remain(
    db_session,
    test_user,
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "personal_markdown_root", tmp_path / "personal_markdown")
    monkeypatch.setattr(settings, "personal_markdown_enabled", True)

    content = _make_content()
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    favorites_repository.add_favorite(db_session, content.id, test_user.id)
    favorites_repository.remove_favorite(db_session, content.id, test_user.id)

    result = sync_personal_markdown_for_content(
        db_session,
        user_id=test_user.id,
        content_id=content.id,
    )

    assert result.written_files == []
    assert list(get_personal_markdown_user_root(test_user.id).rglob("*.md")) == []
