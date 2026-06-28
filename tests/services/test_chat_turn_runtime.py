"""Tests for shared chat turn runtime helpers."""

from app.models.db import ChatSession
from app.services import chat_turn_runtime
from app.services.llm_models import DEFAULT_MODEL


def test_get_or_create_cached_agent_scopes_by_namespace_model_and_credential() -> None:
    chat_turn_runtime.clear_agent_cache_for_tests()
    calls: list[str] = []

    def _factory(label: str) -> object:
        calls.append(label)
        return object()

    first = chat_turn_runtime.get_or_create_cached_agent(
        "article_chat",
        "openai:gpt-5.5",
        "user-key",
        lambda: _factory("first"),
    )
    second = chat_turn_runtime.get_or_create_cached_agent(
        "article_chat",
        "openai:gpt-5.5",
        "user-key",
        lambda: _factory("second"),
    )
    other_model = chat_turn_runtime.get_or_create_cached_agent(
        "article_chat",
        "openai:gpt-5-mini",
        "user-key",
        lambda: _factory("other_model"),
    )
    other_namespace = chat_turn_runtime.get_or_create_cached_agent(
        "contextual_assistant",
        "openai:gpt-5.5",
        "user-key",
        lambda: _factory("other_namespace"),
    )

    assert first is second
    assert other_model is not first
    assert other_namespace is not first
    assert calls == ["first", "other_model", "other_namespace"]
    chat_turn_runtime.clear_agent_cache_for_tests()


def test_chat_usage_snapshot_captures_validated_session_fields() -> None:
    session = ChatSession(
        user_id=42,
        llm_model="",
        content_id=99,
        session_type="article",
    )

    snapshot = chat_turn_runtime.ChatUsageSnapshot.from_session(session)

    assert snapshot.user_id == 42
    assert snapshot.model == DEFAULT_MODEL
    assert snapshot.content_id == 99
    assert snapshot.session_type == "article"
