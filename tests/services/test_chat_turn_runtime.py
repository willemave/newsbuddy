"""Tests for shared chat turn runtime helpers."""

from app.services import chat_turn_runtime


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
