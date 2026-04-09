"""Tests for admin conversational streaming service."""

from __future__ import annotations

from enum import StrEnum

import pytest

from app.core.settings import get_settings
from app.models.schema import Content, ContentFavorites
from app.services import admin_conversational_agent as service


@pytest.fixture(autouse=True)
def clear_session_state() -> None:
    """Reset in-memory session state between tests."""
    service.clear_session_store()


def test_create_or_get_session_state_reuses_existing() -> None:
    """Session creation should be idempotent for same session_id/user_id pair."""
    state_one = service.create_or_get_session_state("abc123", 7)
    state_two = service.create_or_get_session_state("abc123", 7)

    assert state_one.session_id == "abc123"
    assert state_two.session_id == "abc123"
    assert state_two.user_id == 7


def test_create_or_get_session_state_rejects_mismatched_user() -> None:
    """Session IDs cannot be reused across users."""
    service.create_or_get_session_state("shared", 7)

    with pytest.raises(ValueError, match="does not belong"):
        service.create_or_get_session_state("shared", 8)


def test_append_turn_trims_history_to_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turn appends should keep only the latest bounded number of messages."""
    settings = get_settings()
    monkeypatch.setattr(settings, "admin_conversational_max_turns", 2)

    state = service.create_or_get_session_state("trim-test", 5)
    for idx in range(8):
        role = "user" if idx % 2 == 0 else "assistant"
        service.append_turn(state.session_id, role, f"message-{idx}")

    turns = service.get_turn_history(state.session_id)
    assert len(turns) == 4
    assert turns[0].text == "message-4"
    assert turns[-1].text == "message-7"


def test_stream_agent_turn_emits_expected_events(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful turn should emit delta/final/audio events and persist turns."""

    class FakePartType(StrEnum):
        START = "start"
        DELTA = "delta"
        STOP = "stop"

    class FakeClient:
        def __init__(self, api_key: str | None = None) -> None:
            self.api_key = api_key

    class FakeInitiationData:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            self.kwargs = kwargs

    class FakeConversation:
        def __init__(  # noqa: PLR0913
            self,
            client,
            agent_id,
            user_id,
            *,
            requires_auth,
            audio_interface,
            config,
            callback_agent_response,
            callback_agent_chat_response_part,
        ) -> None:
            del client, agent_id, user_id, requires_auth, config
            self.audio_interface = audio_interface
            self.callback_agent_response = callback_agent_response
            self.callback_agent_chat_response_part = callback_agent_chat_response_part
            self.start_count = 0
            self.end_count = 0

        def start_session(self) -> None:
            self.start_count += 1
            return

        def send_contextual_update(self, _text: str) -> None:
            return

        def send_user_message(self, _text: str) -> None:
            self.callback_agent_chat_response_part("Hi", FakePartType.DELTA)
            self.audio_interface.output(b"\x00\x01")
            self.callback_agent_response("Hello world")

        def end_session(self) -> None:
            self.end_count += 1
            return

        def wait_for_session_end(self) -> str:
            return "conversation_1"

    monkeypatch.setattr(
        service,
        "_import_sdk_symbols",
        lambda: (FakeClient, FakeConversation, FakeInitiationData),
    )
    monkeypatch.setattr(service, "build_health_flags", lambda: {"ready": True})

    state = service.create_or_get_session_state("turn-test", 3)
    runtime = service.start_agent_session(session_id=state.session_id, user_id=3)
    events: list[dict] = []
    service.stream_agent_turn(
        runtime=runtime,
        user_text="hello",
        turn_id="turn_1",
        emit_event=events.append,
    )
    service.close_agent_session(runtime)

    event_types = [event["type"] for event in events]
    assert "assistant_delta" in event_types
    assert "assistant_final" in event_types
    assert "audio_chunk_raw" in event_types
    assert "audio_end" in event_types

    turns = service.get_turn_history(state.session_id)
    assert turns[-1].role == "assistant"
    assert turns[-1].text == "Hello world"


def test_stream_agent_turn_reuses_one_runtime_for_multiple_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multiple turns should run on one started conversation runtime."""

    counters = {"start": 0, "end": 0, "sent": []}

    class FakeClient:
        def __init__(self, api_key: str | None = None) -> None:
            self.api_key = api_key

    class FakeInitiationData:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            self.kwargs = kwargs

    class FakeConversation:
        def __init__(  # noqa: PLR0913
            self,
            client,
            agent_id,
            user_id,
            *,
            requires_auth,
            audio_interface,
            config,
            callback_agent_response,
            callback_agent_chat_response_part,
        ) -> None:
            del client, agent_id, user_id, requires_auth, audio_interface, config
            self.callback_agent_response = callback_agent_response
            self.callback_agent_chat_response_part = callback_agent_chat_response_part

        def start_session(self) -> None:
            counters["start"] += 1

        def send_contextual_update(self, _text: str) -> None:
            return

        def send_user_message(self, text: str) -> None:
            counters["sent"].append(text)
            self.callback_agent_chat_response_part("ok", "stop")
            self.callback_agent_response(f"reply-{len(counters['sent'])}")

        def end_session(self) -> None:
            counters["end"] += 1

        def wait_for_session_end(self) -> str:
            return "conversation_1"

    monkeypatch.setattr(
        service,
        "_import_sdk_symbols",
        lambda: (FakeClient, FakeConversation, FakeInitiationData),
    )
    monkeypatch.setattr(service, "build_health_flags", lambda: {"ready": True})

    state = service.create_or_get_session_state("multi-turn", 11)
    runtime = service.start_agent_session(session_id=state.session_id, user_id=11)
    try:
        service.stream_agent_turn(
            runtime=runtime,
            user_text="first",
            turn_id="turn_1",
            emit_event=lambda _: None,
        )
        service.stream_agent_turn(
            runtime=runtime,
            user_text="second",
            turn_id="turn_2",
            emit_event=lambda _: None,
        )
    finally:
        service.close_agent_session(runtime)

    assert counters["start"] == 1
    assert counters["sent"] == ["first", "second"]
    assert counters["end"] == 1


def test_search_knowledge_returns_only_matching_favorites(db_session, test_user) -> None:
    """Knowledge search should include only favorited matching content."""

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
            ContentFavorites(user_id=test_user.id, content_id=c1.id),
            ContentFavorites(user_id=test_user.id, content_id=c2.id),
        ]
    )
    db_session.commit()

    hits = service.search_knowledge(db_session, test_user.id, "policy", limit=5)
    assert len(hits) == 1
    assert hits[0].url == "https://example.com/ai"
    assert hits[0].summary is not None

    fallback_hits = service.search_knowledge(db_session, test_user.id, "private note", limit=5)
    assert len(fallback_hits) == 2
    assert {hit.url for hit in fallback_hits} == {
        "https://example.com/ai",
        "https://example.com/sports",
    }


def test_search_web_returns_empty_when_exa_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Web search should gracefully return empty results without raising."""

    monkeypatch.setattr(service, "exa_search", lambda query, num_results=5: [])
    hits = service.search_web("latest ai", limit=3)
    assert hits == []


def test_build_available_knowledge_context_lists_favorites(db_session, test_user) -> None:
    """Bootstrap knowledge context should enumerate favorited titles."""

    content = Content(
        content_type="podcast",
        url="https://example.com/pod-1",
        title="Podcast title",
        source="Podcast source",
        status="completed",
        content_metadata={},
    )
    db_session.add(content)
    db_session.commit()
    db_session.add(ContentFavorites(user_id=test_user.id, content_id=content.id))
    db_session.commit()

    context = service.build_available_knowledge_context(db_session, test_user.id, limit=100)
    assert "Known favorited user knowledge catalog" in context
    assert "Podcast title" in context


def test_stream_agent_turn_uses_local_favorites_response() -> None:
    """Favorites/history questions should return deterministic local knowledge responses."""

    state = service.create_or_get_session_state("local-favorites", 42)
    runtime = service.AgentConversationRuntime(
        session_id=state.session_id,
        user_id=42,
        conversation=object(),
    )
    knowledge_hits = [
        service.KnowledgeHit(
            content_id=1,
            title="Anthropic launches MCP Apps",
            url="https://example.com/anthropic-mcp",
            source="AINews",
            content_type="article",
            summary="Anthropic introduced MCP Apps in Claude.ai.",
            transcript_excerpt=None,
        )
    ]
    events: list[dict] = []

    service.stream_agent_turn(
        runtime=runtime,
        user_text="tell me about my favorite articles",
        turn_id="turn_local",
        emit_event=events.append,
        knowledge_hits=knowledge_hits,
        web_hits=[],
    )

    event_types = [event["type"] for event in events]
    assert event_types == ["assistant_final", "audio_end"]
    assert "Anthropic launches MCP Apps" in events[0]["text"]
