import json

from app.models.metadata import ContentType
from app.models.schema import ChatSession, Content
from app.services.chat_agent import (
    ChatDeps,
    _build_chat_deps,
    _build_context_prompt_parts,
    _build_run_user_prompt,
    _dump_messages_json,
    build_article_context,
    create_processing_message,
    load_message_history,
)


def test_build_article_context_includes_full_transcript_with_budget() -> None:
    transcript = "a" * 5000
    content = Content(content_type=ContentType.PODCAST.value, url="https://example.com")
    content.content_metadata = {"transcript": transcript}

    context = build_article_context(content, include_full_text=True, max_tokens=5000)

    assert context is not None
    assert transcript in context


def test_build_article_context_prefers_summary_over_full_text_when_requested() -> None:
    content_text = "b" * 5000
    content = Content(content_type=ContentType.ARTICLE.value, url="https://example.com")
    content.content_metadata = {
        "content": content_text,
        "summary": {
            "overview": "Overview text",
            "bullet_points": [
                {"text": "Point one", "category": "key_finding"},
                {"text": "Point two", "category": "methodology"},
                {"text": "Point three", "category": "conclusion"},
            ],
            "quotes": [{"text": "Quote text", "context": "Author"}],
            "topics": ["AI", "Productivity"],
            "questions": ["What changes next?"],
            "counter_arguments": ["Skeptics argue this is premature."],
            "classification": "to_read",
        },
        "summary_kind": "long_structured",
        "summary_version": 1,
    }

    context = build_article_context(content, include_full_text=False, max_tokens=5000)

    assert context is not None
    assert "Overview text" in context
    assert "Point one" in context
    assert "Quote text" in context
    assert "Skeptics argue this is premature." in context
    assert content_text not in context


def test_build_article_context_falls_back_to_summary_when_budget_exceeded() -> None:
    content_text = "c" * 5000
    content = Content(content_type=ContentType.ARTICLE.value, url="https://example.com")
    content.content_metadata = {
        "content": content_text,
        "summary": {
            "overview": "Short overview",
            "bullet_points": [
                {"text": "Point one", "category": "key_finding"},
                {"text": "Point two", "category": "methodology"},
                {"text": "Point three", "category": "conclusion"},
            ],
            "quotes": [],
            "topics": ["AI"],
        },
        "summary_kind": "long_structured",
        "summary_version": 1,
    }

    context = build_article_context(content, include_full_text=True, max_tokens=50)

    assert context is not None
    assert "Short overview" in context
    assert content_text not in context


def test_build_chat_deps_prefers_session_context_snapshot(db_session) -> None:
    content = Content(
        content_type=ContentType.ARTICLE.value,
        url="https://example.com/article",
        title="Article title",
    )
    content.content_metadata = {
        "content": "full article body",
        "summary": {"overview": "Overview text", "bullet_points": [{"text": "Point one"}]},
    }
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    session = ChatSession(
        user_id=123,
        content_id=content.id,
        title="Digest chat",
        session_type="daily_digest_brain",
        context_snapshot="Digest bullets:\n- Bullet A\n- Bullet B",
        llm_provider="anthropic",
        llm_model="anthropic:claude-opus-4-5-20251101",
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    deps = _build_chat_deps(db_session, session, include_full_text=True)

    assert deps.article_context == "Digest bullets:\n- Bullet A\n- Bullet B"
    assert deps.context_label == "Session Context"
    assert deps.content is None
    assert "Overview text" not in deps.article_context
    assert "full article body" not in deps.article_context


def test_build_context_prompt_parts_marks_snapshot_as_reference_material() -> None:
    session = ChatSession(
        user_id=123,
        title="Digest chat",
        session_type="daily_digest_brain",
        context_snapshot="Digest bullets:\n- Bullet A\n- Bullet B",
        llm_provider="anthropic",
        llm_model="anthropic:claude-opus-4-5-20251101",
    )

    parts = _build_context_prompt_parts(
        None,
        session,
        "Digest bullets:\n- Bullet A\n- Bullet B",
        "Session Context",
    )

    rendered = "\n".join(parts)
    assert "Provided reference context is available below." in rendered
    assert "do not ask the user to paste it again" in rendered
    assert "Session Context:\nDigest bullets:\n- Bullet A\n- Bullet B" in rendered


def test_build_run_user_prompt_includes_snapshot_context() -> None:
    session = ChatSession(
        user_id=123,
        title="Digest chat",
        session_type="daily_digest_brain",
        context_snapshot="Digest bullets:\n- Bullet A\n- Bullet B",
        llm_provider="anthropic",
        llm_model="anthropic:claude-opus-4-5-20251101",
    )

    deps = ChatDeps(
        session=session,
        content=None,
        article_context="Digest bullets:\n- Bullet A\n- Bullet B",
        context_label="Session Context",
    )

    prompt = _build_run_user_prompt("Dig deeper into these digest bullets.", deps)

    assert "Use the provided session context below as the source material" in prompt
    assert "Session Context:\nDigest bullets:\n- Bullet A\n- Bullet B" in prompt
    assert prompt.endswith("User request:\nDig deeper into these digest bullets.")


def test_dump_messages_json_restores_user_visible_prompt(db_session) -> None:
    session = ChatSession(
        user_id=123,
        title="Digest chat",
        session_type="daily_digest_brain",
        llm_provider="anthropic",
        llm_model="anthropic:claude-opus-4-5-20251101",
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    create_processing_message(
        db_session,
        session.id,
        (
            "Use the provided session context below as the source material.\n\n"
            "Session Context:\n- Bullet A"
        ),
    )
    messages = load_message_history(db_session, session.id)

    stored_json = _dump_messages_json(
        messages,
        display_user_prompt="Dig deeper into these digest bullets.",
    )
    payload = json.loads(stored_json)

    assert payload[0]["parts"][0]["content"] == "Dig deeper into these digest bullets."
    assert "Session Context" not in payload[0]["parts"][0]["content"]
