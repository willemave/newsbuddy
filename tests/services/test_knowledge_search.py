"""Tests for saved-knowledge search helpers."""

from app.models.schema import Content, ContentKnowledgeSave
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
    assert hits[0].summary is not None

    fallback_hits = search_knowledge(db_session, test_user.id, "private note", limit=5)
    assert len(fallback_hits) == 2
    assert {hit.url for hit in fallback_hits} == {
        "https://example.com/ai",
        "https://example.com/sports",
    }
