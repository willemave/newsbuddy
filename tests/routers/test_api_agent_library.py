"""Tests for agent markdown library sync endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

from app.models.schema import Content
from app.repositories import knowledge_repository


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
                    {"text": "Agents coordinate tools."},
                    {"text": "Execution needs boundaries."},
                ],
                "quotes": [{"text": "Agents need clear boundaries."}],
                "topics": ["Agents"],
                "summarization_date": "2026-04-03T12:00:00Z",
            },
        },
    )


def _seed_favorited_content(db_session, test_user) -> Content:
    content = _make_content()
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)
    knowledge_repository.save_to_knowledge(db_session, content.id, test_user.id)
    return content


def test_agent_library_manifest_defaults_to_source_and_summary(
    client,
    db_session,
    test_user,
) -> None:
    """Manifest should include both summary and source markdown by default."""
    _seed_favorited_content(db_session, test_user)

    response = client.get("/api/agent/library/manifest")

    assert response.status_code == 200
    payload = response.json()
    assert payload["documents"]
    assert payload["include_source"] is True
    assert len(payload["documents"]) == 2
    variants = {document["variant"] for document in payload["documents"]}
    assert variants == {"source", "summary"}


def test_agent_library_manifest_can_exclude_source_when_requested(
    client,
    db_session,
    test_user,
) -> None:
    """Manifest should still support summary-only export when requested."""
    content = _seed_favorited_content(db_session, test_user)

    response = client.get(
        "/api/agent/library/manifest",
        params={"include_source": "false"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["include_source"] is False
    assert len(payload["documents"]) == 1
    document = payload["documents"][0]
    assert document["variant"] == "summary"
    assert document["content_id"] == content.id
    assert document["relative_path"].endswith(f"__summary__c{content.id}.md")
    assert document["checksum_sha256"]


def test_agent_library_manifest_can_include_source_and_download_document(
    client,
    db_session,
    test_user,
) -> None:
    """Library sync should expose both manifest metadata and file contents."""
    _seed_favorited_content(db_session, test_user)

    manifest_response = client.get(
        "/api/agent/library/manifest",
        params={"include_source": "true"},
    )

    assert manifest_response.status_code == 200
    documents = manifest_response.json()["documents"]
    assert len(documents) == 2

    source_document = next(document for document in documents if document["variant"] == "source")
    file_response = client.get(
        "/api/agent/library/file",
        params={"path": source_document["relative_path"]},
    )

    assert file_response.status_code == 200
    payload = file_response.json()
    assert payload["relative_path"] == source_document["relative_path"]
    assert payload["variant"] == "source"
    assert "Raw body text from the article." in payload["text"]
