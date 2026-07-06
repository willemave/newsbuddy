from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.models.contracts import ContentClassification, ContentType
from app.models.db import NewsItemDiscussion
from app.services.briefing.sources import (
    BRIEFING_CONTEXT_MAX_CHARS,
    _truncate_discussion_overview,
    list_unread_longform_sources,
    read_source_keys_for,
    sources_for_keys,
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


def test_sources_for_keys_attaches_completed_discussion_payload(
    db_session: Session,
    test_user,
    news_item_factory,
    monkeypatch,
) -> None:
    assert test_user.id is not None
    settings = get_settings()
    monkeypatch.setattr(settings, "briefing_discussion_overview_max_chars", 80)
    item = news_item_factory(
        visibility_scope="user",
        owner_user_id=test_user.id,
        summary_title="Discussed story",
    )
    db_session.add(
        NewsItemDiscussion(
            news_item_id=item.id,
            platform="hackernews",
            discussion_url="https://news.ycombinator.com/item?id=123",
            comment_count=214,
            summary_status="completed",
            last_refresh_status="completed",
            summary={
                "overview": (
                    "Commenters focused on the deployment risks. "
                    "They also debated whether the benchmark was representative."
                ),
                "topics": [{"title": "Risk", "summary": "Deployment risk dominated."}],
                "representative_comments": [
                    {"author": "alice", "text": "The rollout plan is the hard part."}
                ],
                "external_discussion_url": "https://news.ycombinator.com/item?id=123",
            },
        )
    )
    db_session.commit()

    source = sources_for_keys(
        db_session,
        user_id=test_user.id,
        source_keys=[f"news:{item.id}"],
    )[f"news:{item.id}"]
    payload = source.dto(read=False)["discussion"]

    assert payload == {
        "platform": "hackernews",
        "comment_count": 214,
        "summary_status": "completed",
        "overview": "Commenters focused on the deployment risks.",
        "top_comment_author": "alice",
        "top_comment_text": "The rollout plan is the hard part.",
        "external_url": "https://news.ycombinator.com/item?id=123",
        "updated_at": None,
    }


def test_sources_for_keys_attaches_count_only_discussion_and_skips_terminal_rows(
    db_session: Session,
    test_user,
    news_item_factory,
) -> None:
    assert test_user.id is not None
    count_only = news_item_factory(
        visibility_scope="user",
        owner_user_id=test_user.id,
        summary_title="Count only story",
    )
    gone = news_item_factory(
        visibility_scope="user",
        owner_user_id=test_user.id,
        summary_title="Gone story",
    )
    db_session.add_all(
        [
            NewsItemDiscussion(
                news_item_id=count_only.id,
                platform="reddit",
                discussion_url="https://reddit.com/r/test/comments/abc/thread/",
                comment_count=48,
                summary_status="not_ready",
                last_refresh_status="pending",
            ),
            NewsItemDiscussion(
                news_item_id=gone.id,
                platform="hackernews",
                discussion_url="https://news.ycombinator.com/item?id=456",
                comment_count=12,
                summary_status="failed",
                last_refresh_status="gone",
            ),
        ]
    )
    db_session.commit()

    sources = sources_for_keys(
        db_session,
        user_id=test_user.id,
        source_keys=[f"news:{count_only.id}", f"news:{gone.id}"],
    )

    count_payload = sources[f"news:{count_only.id}"].dto(read=False)["discussion"]
    assert isinstance(count_payload, dict)
    assert count_payload["summary_status"] == "not_ready"
    assert count_payload["comment_count"] == 48
    assert count_payload["overview"] is None
    assert sources[f"news:{gone.id}"].dto(read=False)["discussion"] is None


def test_truncate_discussion_overview_uses_sentence_boundary_when_available() -> None:
    overview = (
        "The first sentence is useful and complete. "
        "The second sentence should not leak into the strip when the cap is small."
    )

    assert _truncate_discussion_overview(overview, max_chars=58) == (
        "The first sentence is useful and complete."
    )
    assert _truncate_discussion_overview("one two three four five six", max_chars=18) == (
        "one two three..."
    )
