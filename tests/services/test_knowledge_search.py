"""Tests for saved-knowledge search helpers."""

from app.models.db import AgentDataFile, Content, ContentKnowledgeSave
from app.services.knowledge_search import search_knowledge


def test_search_knowledge_returns_only_matching_saved_items(db_session, test_user) -> None:
    """Knowledge search should include only knowledge-saved matching content."""
    c1 = Content(
        content_type="article",
        url="https://example.com/ai",
        title="AI policy landscape",
        source="Example",
        status="completed",
        content_metadata={"summary": {"overview": "Policy and regulation updates"}},
    )
    c2 = Content(
        content_type="article",
        url="https://example.com/sports",
        title="Sports recap",
        source="Example",
        status="completed",
        content_metadata={"summary": {"overview": "Weekly sports roundup"}},
    )
    c3 = Content(
        content_type="article",
        url="https://example.com/unfav",
        title="AI private note",
        source="Example",
        status="completed",
        content_metadata={"summary": {"overview": "Should not be returned"}},
    )
    db_session.add_all([c1, c2, c3])
    db_session.commit()

    db_session.add_all(
        [
            ContentKnowledgeSave(user_id=test_user.id, content_id=c1.id),
            ContentKnowledgeSave(user_id=test_user.id, content_id=c2.id),
        ]
    )
    db_session.commit()

    hits = search_knowledge(db_session, test_user.id, "policy", limit=5)
    assert len(hits) == 1
    assert hits[0].url == "https://example.com/ai"
    assert hits[0].snippet is not None

    fallback_hits = search_knowledge(db_session, test_user.id, "private note", limit=5)
    assert fallback_hits == []


def test_search_knowledge_matches_body_text_and_returns_ranked_snippet(
    db_session, test_user
) -> None:
    content = Content(
        content_type="article",
        url="https://example.com/storage",
        title="Grid storage notes",
        source="Example",
        status="completed",
        search_text="Lithium iron phosphate cathodes make stationary batteries durable.",
        content_metadata={"summary": {"overview": "Battery chemistry overview"}},
    )
    db_session.add(content)
    db_session.flush()
    db_session.add(ContentKnowledgeSave(user_id=test_user.id, content_id=content.id))
    db_session.commit()

    hits = search_knowledge(db_session, test_user.id, "phosphate cathodes")

    assert [hit.content_id for hit in hits] == [content.id]
    assert hits[0].snippet is not None
    assert "phosphate" in hits[0].snippet.lower()


def test_search_knowledge_returns_the_targeted_vm_corpus_path(db_session, test_user) -> None:
    content = Content(
        content_type="article",
        url="https://example.com/leases",
        title="Distributed leases",
        source="Example",
        status="completed",
        search_text="Leases establish temporary ownership.",
    )
    db_session.add(content)
    db_session.flush()
    db_session.add_all(
        [
            ContentKnowledgeSave(user_id=test_user.id, content_id=content.id),
            AgentDataFile(
                user_id=test_user.id,
                document_kind="content",
                document_key=str(content.id),
                path=f"knowledge/{content.id}-distributed-leases.md",
                stale_paths=[],
                checksum_sha256="0" * 64,
                index_record={},
                byte_size=10,
                revision=1,
            ),
        ]
    )
    db_session.commit()

    hits = search_knowledge(db_session, test_user.id, "temporary ownership")

    assert hits[0].corpus_path == f"/data/knowledge/{content.id}-distributed-leases.md"
