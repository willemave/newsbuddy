from sqlalchemy.orm import Session

from app.models.contracts import ContentClassification, ContentType
from app.services.briefing.sources import (
    BRIEFING_CONTEXT_MAX_CHARS,
    list_unread_longform_sources,
    read_source_keys_for,
)


def test_list_unread_longform_sources_includes_null_classification_and_excludes_skip(
    db_session: Session,
    test_user,
    content_factory,
    status_entry_factory,
    read_status_factory,
) -> None:
    assert test_user.id is not None
    null_article = content_factory(
        content_type=ContentType.ARTICLE,
        title="Unclassified article",
        classification=None,
    )
    to_read_article = content_factory(
        content_type=ContentType.ARTICLE,
        title="Explicit to-read article",
        classification=ContentClassification.TO_READ.value,
    )
    skipped_article = content_factory(
        content_type=ContentType.ARTICLE,
        title="Skipped article",
        classification=ContentClassification.SKIP.value,
    )
    read_article = content_factory(
        content_type=ContentType.ARTICLE,
        title="Already read article",
        classification=None,
    )
    for content in (null_article, to_read_article, skipped_article, read_article):
        status_entry_factory(user=test_user, content=content, status="inbox")
    read_status_factory(user=test_user, content=read_article)

    sources = list_unread_longform_sources(
        db_session,
        user_id=test_user.id,
        content_type=ContentType.ARTICLE,
        limit=10,
    )

    source_ids = {source.id for source in sources}
    assert source_ids == {null_article.id, to_read_article.id}


def test_read_source_keys_for_returns_only_requested_read_keys(
    db_session: Session,
    test_user,
    content_factory,
    read_status_factory,
) -> None:
    assert test_user.id is not None
    read_article = content_factory(content_type=ContentType.ARTICLE, title="Read article")
    unrelated_read_article = content_factory(
        content_type=ContentType.ARTICLE,
        title="Unrelated read article",
    )
    unread_article = content_factory(content_type=ContentType.ARTICLE, title="Unread article")
    read_status_factory(user=test_user, content=read_article)
    read_status_factory(user=test_user, content=unrelated_read_article)

    keys = read_source_keys_for(
        db_session,
        user_id=test_user.id,
        source_keys=[
            f"content:{read_article.id}",
            f"content:{unread_article.id}",
            f"content:{unrelated_read_article.id + 10000}",
            "not-a-source-key",
        ],
    )

    assert keys == {f"content:{read_article.id}"}


def test_list_unread_longform_sources_builds_rich_briefing_context(
    db_session: Session,
    test_user,
    content_factory,
    status_entry_factory,
) -> None:
    assert test_user.id is not None
    article = content_factory(
        content_type=ContentType.ARTICLE,
        title="Deep article",
        content_metadata={
            "summary": {
                "title": "Deep article",
                "editorial_narrative": (
                    "The article argues that agent workflows are becoming durable "
                    "software infrastructure, not just one-off chat prompts."
                ),
                "key_points": [
                    {"point": "Teams are moving core review and deploy loops into agents."},
                    {"point": "The operational risk is now around evaluation and rollback."},
                ],
                "source_details": {
                    "template": "substack",
                    "thesis": "Agents are shifting from tools to operating model.",
                    "supporting_arguments": [
                        "Workflow ownership is moving from individuals to shared harnesses."
                    ],
                    "evidence": ["Several teams now maintain repo-specific agent instructions."],
                    "implications": ["Documentation quality becomes production leverage."],
                },
                "quotes": [{"text": "Documentation is the durable artifact.", "context": "Author"}],
            },
            "content": "Full article body with concrete implementation detail. " * 80,
        },
    )
    status_entry_factory(user=test_user, content=article, status="inbox")

    sources = list_unread_longform_sources(
        db_session,
        user_id=test_user.id,
        content_type=ContentType.ARTICLE,
        limit=10,
    )

    assert len(sources) == 1
    context = sources[0].briefing_context
    assert context is not None
    assert "Narrative: The article argues" in context
    assert "Teams are moving core review and deploy loops into agents." in context
    assert "Thesis: Agents are shifting from tools to operating model." in context
    assert "Source excerpt: Full article body" in context
    assert len(context) <= BRIEFING_CONTEXT_MAX_CHARS
