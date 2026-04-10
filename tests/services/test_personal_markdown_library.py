"""Tests for the per-user personal markdown library."""

from datetime import UTC, datetime
from pathlib import Path

from app.core.settings import get_settings
from app.models.schema import ChatSession, Content, ContentBody
from app.repositories import knowledge_repository
from app.services import content_bodies
from app.services.content_bodies import ContentBodyFormat, ContentBodyVariant
from app.services.gateways import object_storage_gateway
from app.services.personal_markdown_library import (
    collect_personal_markdown_documents_for_user,
    get_personal_markdown_user_root,
    sync_personal_markdown_for_content,
    sync_personal_markdown_library_for_user,
)


def _enable_personal_markdown(
    monkeypatch,
    tmp_path: Path,
    *,
    content_body_root: bool = False,
) -> None:
    settings = get_settings()
    if content_body_root:
        monkeypatch.setattr(settings, "content_body_local_root", tmp_path / "content_bodies")
    monkeypatch.setattr(settings, "personal_markdown_root", tmp_path / "personal_markdown")
    monkeypatch.setattr(settings, "personal_markdown_enabled", True)


def _persist_content(db_session, *contents: Content) -> None:
    db_session.add_all(list(contents))
    db_session.commit()
    for content in contents:
        db_session.refresh(content)


def _make_content() -> Content:
    return Content(
        content_type="article",
        url="https://example.com/how-agents-work",
        title="How Agents Work",
        source="New York Times",
        publication_date=datetime(2026, 4, 3, 8, 0, 0, tzinfo=UTC).replace(tzinfo=None),
        status="completed",
        content_metadata={
            "summary_kind": "long_structured",
            "summary_version": 1,
            "content": "Raw body text from the article.",
            "summary": {
                "overview": "A compact summary of how agents work.",
                "bullet_points": [
                    {"text": "Agents coordinate tools.", "category": "key_finding"},
                    {"text": "Execution needs boundaries.", "category": "risk"},
                ],
                "quotes": [{"text": "Agents work best with clear boundaries."}],
                "topics": ["Agents", "Execution"],
                "summarization_date": "2026-04-03T12:00:00Z",
            },
        },
    )


def _make_podcast_content() -> Content:
    return Content(
        content_type="podcast",
        url="https://example.com/podcasts/episode-42",
        title="Episode 42: Systems Thinking",
        source=None,
        publication_date=datetime(2026, 4, 4, 9, 30, 0, tzinfo=UTC).replace(tzinfo=None),
        status="completed",
        content_metadata={
            "summary_kind": "long_structured",
            "summary_version": 1,
            "show_name": "BG2 Pod",
            "transcript": "Full transcript text from the podcast episode.",
            "summary": {
                "overview": "A summary of the podcast episode.",
                "bullet_points": [
                    {"text": "Systems choices shape downstream reliability."},
                    {"text": "Operational clarity matters more than raw flexibility."},
                ],
                "quotes": [{"text": "Operational clarity beats theoretical flexibility."}],
                "topics": ["Systems", "Operations"],
                "summarization_date": "2026-04-04T11:00:00Z",
            },
        },
    )


def test_sync_personal_markdown_library_writes_source_and_summary_files(
    db_session,
    test_user,
    monkeypatch,
    tmp_path: Path,
) -> None:
    _enable_personal_markdown(monkeypatch, tmp_path)

    content = _make_content()
    _persist_content(db_session, content)

    knowledge_repository.save_to_knowledge(db_session, content.id, test_user.id)

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
    assert "- saved_to_knowledge" in source_text
    assert "Raw body text from the article." in source_text
    assert "variant: summary" in summary_text
    assert "# How Agents Work" in summary_text


def test_sync_personal_markdown_for_content_keeps_files_when_chat_session_exists(
    db_session,
    test_user,
    monkeypatch,
    tmp_path: Path,
) -> None:
    _enable_personal_markdown(monkeypatch, tmp_path)

    content = _make_content()
    _persist_content(db_session, content)

    knowledge_repository.save_to_knowledge(db_session, content.id, test_user.id)
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

    knowledge_repository.remove_from_knowledge(db_session, content.id, test_user.id)
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
    _enable_personal_markdown(monkeypatch, tmp_path)

    content = _make_content()
    _persist_content(db_session, content)

    knowledge_repository.save_to_knowledge(db_session, content.id, test_user.id)
    knowledge_repository.remove_from_knowledge(db_session, content.id, test_user.id)

    result = sync_personal_markdown_for_content(
        db_session,
        user_id=test_user.id,
        content_id=content.id,
    )

    assert result.written_files == []
    assert list(get_personal_markdown_user_root(test_user.id).rglob("*.md")) == []


def test_collect_personal_markdown_documents_skips_missing_source_object(
    db_session,
    test_user,
    monkeypatch,
    tmp_path: Path,
) -> None:
    _enable_personal_markdown(monkeypatch, tmp_path, content_body_root=True)
    monkeypatch.setattr(content_bodies, "_content_body_resolver", None)
    monkeypatch.setattr(object_storage_gateway, "_object_storage_gateway", None)

    content = _make_content()
    content.content_metadata = {
        "summary_kind": "long_structured",
        "summary_version": 1,
        "summary": {
            "overview": "A compact summary of how agents work.",
            "bullet_points": [
                {"text": "Agents coordinate tools."},
                {"text": "Execution needs boundaries."},
            ],
            "quotes": [{"text": "Agents need clear boundaries."}],
            "topics": ["Agents"],
            "summarization_date": "2026-04-03T12:00:00Z",
        }
    }
    _persist_content(db_session, content)

    db_session.add(
        ContentBody(
            content_id=content.id,
            variant=ContentBodyVariant.SOURCE.value,
            storage_provider="local",
            storage_key="content/999/source-missing.txt",
            content_format=ContentBodyFormat.TEXT.value,
            sha256="deadbeef",
            byte_size=0,
            char_count=0,
        )
    )
    db_session.commit()

    knowledge_repository.save_to_knowledge(db_session, content.id, test_user.id)

    documents = collect_personal_markdown_documents_for_user(
        db_session,
        user_id=test_user.id,
        include_source=True,
    )

    assert len(documents) == 1
    assert documents[0].variant == "summary"


def test_collect_personal_markdown_documents_has_stable_checksums_between_calls(
    db_session,
    test_user,
    monkeypatch,
    tmp_path: Path,
) -> None:
    _enable_personal_markdown(monkeypatch, tmp_path)

    content = _make_content()
    _persist_content(db_session, content)

    knowledge_repository.save_to_knowledge(db_session, content.id, test_user.id)

    first_documents = collect_personal_markdown_documents_for_user(
        db_session,
        user_id=test_user.id,
        include_source=True,
    )
    second_documents = collect_personal_markdown_documents_for_user(
        db_session,
        user_id=test_user.id,
        include_source=True,
    )

    assert [document.relative_path for document in first_documents] == [
        document.relative_path for document in second_documents
    ]
    assert [document.checksum_sha256 for document in first_documents] == [
        document.checksum_sha256 for document in second_documents
    ]


def test_collect_personal_markdown_documents_supports_mixed_types_and_reasons(
    db_session,
    test_user,
    monkeypatch,
    tmp_path: Path,
) -> None:
    _enable_personal_markdown(monkeypatch, tmp_path)

    article = _make_content()
    podcast = _make_podcast_content()
    _persist_content(db_session, article, podcast)

    knowledge_repository.save_to_knowledge(db_session, article.id, test_user.id)
    db_session.add(
        ChatSession(
            user_id=test_user.id,
            content_id=podcast.id,
            title=podcast.title,
            session_type="knowledge_chat",
            llm_model="openai:gpt-5.4",
            llm_provider="openai",
        )
    )
    db_session.commit()

    documents = collect_personal_markdown_documents_for_user(
        db_session,
        user_id=test_user.id,
        include_source=False,
    )

    relative_paths = [document.relative_path.as_posix() for document in documents]
    assert relative_paths == [
        f"article/new-york-times/how-agents-work__2026-04-03__summary__c{article.id}.md",
        f"podcast/bg2-pod/episode-42-systems-thinking__2026-04-04__summary__c{podcast.id}.md",
    ]

    article_document, podcast_document = documents
    assert "- saved_to_knowledge" in article_document.text
    assert "- chatted" in podcast_document.text
    assert "source: BG2 Pod" in podcast_document.text
