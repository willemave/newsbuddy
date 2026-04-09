"""Maestro-backed iOS end-to-end tests using shared backend fixtures."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from app.models.schema import (
    ChatMessage,
    ChatSession,
    ContentFavorites,
    ContentReadStatus,
    NewsItem,
)
from app.services.chat_agent import ChatRunResult, save_messages

pytestmark = [pytest.mark.integration, pytest.mark.ios_e2e]


def test_long_form_detail_flow_uses_seeded_fixture_data(
    run_ios_flow,
    create_sample_content,
    sample_article_long,
) -> None:
    """The seeded long-form content fixture should render in the iOS app."""
    content = create_sample_content(sample_article_long)

    run_ios_flow(
        "long_form_detail.yaml",
        extra_env={
            "CONTENT_ID": str(content.id),
            "CONTENT_TITLE": content.title,
        },
    )


def test_long_form_detail_favorite_action_updates_backend_state(
    run_ios_flow,
    create_sample_content,
    sample_article_long,
    test_user,
    db_session,
) -> None:
    """Favoriting from the detail screen should persist to the shared backend DB."""
    content = create_sample_content(sample_article_long)

    run_ios_flow(
        "long_form_favorite.yaml",
        extra_env={
            "CONTENT_ID": str(content.id),
            "CONTENT_TITLE": content.title,
        },
    )

    favorite = (
        db_session.query(ContentFavorites)
        .filter(
            ContentFavorites.user_id == test_user.id,
            ContentFavorites.content_id == content.id,
        )
        .one_or_none()
    )
    assert favorite is not None


def test_long_form_list_mark_read_action_updates_backend_state(
    run_ios_flow,
    create_sample_content,
    sample_article_long,
    test_user,
    db_session,
) -> None:
    """Mark-as-read from the long-form list should persist to the shared backend DB."""
    content = create_sample_content(sample_article_long)

    run_ios_flow(
        "long_form_mark_read.yaml",
        extra_env={"CONTENT_ID": str(content.id)},
    )

    read_status = (
        db_session.query(ContentReadStatus)
        .filter(
            ContentReadStatus.user_id == test_user.id,
            ContentReadStatus.content_id == content.id,
        )
        .one_or_none()
    )
    assert read_status is not None


def test_short_form_detail_discussion_sheet_renders_embedded_comments(
    run_ios_flow,
    db_session,
) -> None:
    """Comments button should open the in-app discussion sheet for news items."""
    comment_id = "comment-1"
    news_item = NewsItem(
        ingest_key="ios-e2e-discussion",
        visibility_scope="global",
        platform="hackernews",
        source_type="hackernews",
        source_label="Hacker News",
        source_external_id="ios-e2e-discussion",
        canonical_item_url="https://news.ycombinator.com/item?id=424242",
        canonical_story_url="https://example.com/herbie-floating-point",
        article_url="https://example.com/herbie-floating-point",
        article_title="Herbie Automatically Optimizes Code to Fix Floating-Point Precision Errors",
        article_domain="example.com",
        discussion_url="https://news.ycombinator.com/item?id=424242",
        summary_title="Herbie Automatically Optimizes Code to Fix Floating-Point Precision Errors",
        summary_key_points=[
            "Herbie suggests numerically stable rewrites for floating-point expressions."
        ],
        summary_text="Herbie improves floating-point expressions by proposing stable alternatives.",
        raw_metadata={
            "discussion_url": "https://news.ycombinator.com/item?id=424242",
            "summary": {
                "article_url": "https://example.com/herbie-floating-point",
                "summary": (
                    "Herbie improves floating-point expressions by proposing stable alternatives."
                ),
                "key_points": [
                    "Herbie suggests numerically stable rewrites for floating-point expressions."
                ],
            },
            "discussion_payload": {
                "mode": "comments",
                "source_url": "https://news.ycombinator.com/item?id=424242",
                "comments": [
                    {
                        "comment_id": comment_id,
                        "author": "alice",
                        "text": "This kind of numerical tooling saves real debugging time.",
                        "compact_text": "This kind of numerical tooling saves real debugging time.",
                        "depth": 0,
                    }
                ],
                "discussion_groups": [],
                "links": [],
                "stats": {"fetched_count": 1},
            },
        },
        status="ready",
        published_at=datetime.now(UTC).replace(tzinfo=None),
        ingested_at=datetime.now(UTC).replace(tzinfo=None),
        processed_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db_session.add(news_item)
    db_session.commit()

    run_ios_flow(
        "short_form_discussion.yaml",
        extra_env={
            "CONTENT_ID": str(news_item.id),
            "COMMENT_ID": comment_id,
        },
    )


def test_council_tabs_switch_between_mocked_branch_replies(
    run_ios_flow,
    create_sample_content,
    sample_article_long,
    test_user,
    chat_session_factory,
    db_session,
    monkeypatch,
) -> None:
    """Council mode should switch visible branch replies using deterministic mocked backend data."""
    content = create_sample_content(sample_article_long)
    test_user.council_personas = [
        {
            "id": "paul_graham",
            "display_name": "Paul Graham",
            "instruction_prompt": "",
            "sort_order": 0,
        },
        {
            "id": "ben_thompson",
            "display_name": "Ben Thompson",
            "instruction_prompt": "",
            "sort_order": 1,
        },
        {
            "id": "byrne_hobart",
            "display_name": "Byrne Hobart",
            "instruction_prompt": "",
            "sort_order": 2,
        },
    ]
    db_session.commit()
    db_session.refresh(test_user)

    session = chat_session_factory(
        user=test_user,
        content=content,
        title="Mocked Council Session",
        session_type="knowledge_chat",
    )
    save_messages(
        db_session,
        session.id,
        [
            ModelRequest(parts=[UserPromptPart(content="Summarize the article.")]),
            ModelResponse(parts=[TextPart(content="Initial mocked assistant reply.")]),
        ],
        display_user_prompt="Summarize the article.",
    )

    async def _fake_run_chat_turn(db, branch_session, user_prompt, source="chat"):
        del source
        assistant_text = f"{branch_session.council_persona_name} mocked council reply"
        messages = [
            ModelRequest(parts=[UserPromptPart(content=user_prompt)]),
            ModelResponse(parts=[TextPart(content=assistant_text)]),
        ]
        save_messages(db, branch_session.id, messages, display_user_prompt=user_prompt)
        return ChatRunResult(
            output_text=assistant_text,
            new_messages=messages,
            all_messages=messages,
            tool_calls=[],
        )

    monkeypatch.setattr("app.services.council_chat.run_chat_turn", _fake_run_chat_turn)

    run_ios_flow(
        "long_form_council_mocked.yaml",
        extra_env={
            "CONTENT_ID": str(content.id),
            "ANALYST_REPLY": "Analyst mocked council reply",
            "STRATEGIST_REPLY": "Strategist mocked council reply",
        },
    )

    db_session.expire_all()
    parent_session = db_session.query(ChatSession).filter(ChatSession.id == session.id).one()
    assert parent_session.council_mode is True
    assert parent_session.active_child_session_id is not None


def test_chat_mic_toggle_flow_uses_mocked_speech_and_sends_message(
    run_ios_flow,
    test_user,
    chat_session_factory,
    db_session,
    completed_chat_processors_factory,
    monkeypatch,
) -> None:
    """The chat mic should toggle recording, surface the transcript, and send it."""
    transcript = "Mocked mic transcript for chat UI"
    assistant_reply = "Mocked assistant reply for chat UI"
    session = chat_session_factory(
        user=test_user,
        title="Mocked Mic Session",
        session_type="knowledge_chat",
    )
    _fake_process_message_async, _fake_process_assistant_turn_async = (
        completed_chat_processors_factory(assistant_reply=assistant_reply)
    )

    monkeypatch.setattr("app.routers.api.chat.process_message_async", _fake_process_message_async)
    monkeypatch.setattr(
        "app.routers.api.chat.process_assistant_turn_async",
        _fake_process_assistant_turn_async,
    )

    run_ios_flow(
        "chat_mic_toggle.yaml",
        extra_env={
            "CHAT_SESSION_ID": str(session.id),
            "TRANSCRIPT": transcript,
            "ASSISTANT_REPLY": assistant_reply,
        },
    )

    db_session.expire_all()
    message = (
        db_session.query(ChatMessage)
        .filter(ChatMessage.session_id == session.id)
        .order_by(ChatMessage.id.desc())
        .first()
    )
    assert message is not None
    assert message.status == "completed"
    assert transcript in (message.message_list or "")
    assert assistant_reply in (message.message_list or "")


def test_personalized_onboarding_flow_uses_seeded_fixture_data(
    run_ios_flow,
    db_session,
    ios_onboarding_personalized_fixture,
    test_user,
) -> None:
    """Personalized onboarding should be Maestro-testable via deterministic launch fixtures."""
    run_ios_flow(
        "onboarding_personalized.yaml",
        extra_env={"ONBOARDING_FIXTURE": ios_onboarding_personalized_fixture},
    )

    db_session.refresh(test_user)
    assert test_user.has_completed_onboarding is True
    assert test_user.has_completed_new_user_tutorial is True
