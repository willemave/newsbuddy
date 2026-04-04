"""Maestro-backed iOS end-to-end tests using shared backend fixtures."""

from __future__ import annotations

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from app.models.schema import ChatSession, ContentFavorites, ContentReadStatus
from app.services.chat_agent import ChatRunResult, save_messages

pytestmark = [pytest.mark.integration, pytest.mark.ios_e2e]


def test_long_form_detail_flow_uses_seeded_fixture_data(
    live_server,
    run_maestro_flow,
    create_sample_content,
    sample_article_long,
    test_user,
) -> None:
    """The seeded long-form content fixture should render in the iOS app."""
    content = create_sample_content(sample_article_long)

    run_maestro_flow(
        "long_form_detail.yaml",
        live_server=live_server,
        user_id=test_user.id,
        extra_env={
            "CONTENT_ID": str(content.id),
            "CONTENT_TITLE": content.title,
        },
    )


def test_long_form_detail_favorite_action_updates_backend_state(
    live_server,
    run_maestro_flow,
    create_sample_content,
    sample_article_long,
    test_user,
    db_session,
) -> None:
    """Favoriting from the detail screen should persist to the shared backend DB."""
    content = create_sample_content(sample_article_long)

    run_maestro_flow(
        "long_form_favorite.yaml",
        live_server=live_server,
        user_id=test_user.id,
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
    live_server,
    run_maestro_flow,
    create_sample_content,
    sample_article_long,
    test_user,
    db_session,
) -> None:
    """Mark-as-read from the long-form list should persist to the shared backend DB."""
    content = create_sample_content(sample_article_long)

    run_maestro_flow(
        "long_form_mark_read.yaml",
        live_server=live_server,
        user_id=test_user.id,
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


def test_council_tabs_switch_between_mocked_branch_replies(
    live_server,
    run_maestro_flow,
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
            "id": "analyst",
            "display_name": "Analyst",
            "instruction_prompt": "Focus on evidence and tradeoffs.",
            "sort_order": 0,
        },
        {
            "id": "strategist",
            "display_name": "Strategist",
            "instruction_prompt": "Focus on implications and action.",
            "sort_order": 1,
        },
        {
            "id": "skeptic",
            "display_name": "Skeptic",
            "instruction_prompt": "Stress weak assumptions.",
            "sort_order": 2,
        },
        {
            "id": "operator",
            "display_name": "Operator",
            "instruction_prompt": "Prefer practical execution details.",
            "sort_order": 3,
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

    run_maestro_flow(
        "long_form_council_mocked.yaml",
        live_server=live_server,
        user_id=test_user.id,
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
