"""Tests for contextual assistant routing heuristics."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

from pydantic_ai import RunContext
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import ToolDefinition

from app.core.settings import get_settings
from app.models.contracts import LlmTaskStatus, MessageProcessingStatus, TaskType
from app.models.db import (
    ChatMessage,
    ChatSession,
    Content,
    ContentStatusEntry,
    LlmTask,
    NewsItemReadStatus,
    ProcessingTask,
    UserScraperConfig,
)
from app.models.internal.assistant import AssistantScreenContext
from app.repositories.search_repository import (
    search_news,
    search_subscription_feeds,
)
from app.services import assistant_router, assistant_turn_routing, chat_turn_runtime
from app.services.chat_agent import create_processing_message
from app.services.chat_turn_queue import build_chat_turn_context
from tests.support.feed_subscription_test_helpers import stub_feed_validator


def test_turn_profile_prefers_knowledge_for_saved_content_prompts() -> None:
    """Favorite/saved prompts should route to search_knowledge."""

    profile = assistant_turn_routing.resolve_assistant_turn_profile("What is my favorite article?")

    assert profile.route == "knowledge_search"
    assert profile.tool_names == assistant_turn_routing.ASSISTANT_DEFAULT_TOOL_NAMES


def test_turn_profile_prefers_web_for_recent_questions() -> None:
    """Recent factual prompts should route to web search."""

    profile = assistant_turn_routing.resolve_assistant_turn_profile(
        "What is the latest Rust release?"
    )

    assert profile.route == "web_search"
    assert profile.tool_names == assistant_turn_routing.ASSISTANT_DEFAULT_TOOL_NAMES


def test_turn_profile_prefers_feed_finder_for_blog_subscription() -> None:
    """Feed/blog discovery prompts should route to the feed finder tool."""

    profile = assistant_turn_routing.resolve_assistant_turn_profile(
        "please find a blog by Armin Ronacher and subscribe to it"
    )

    assert profile.route == "feed_finder"
    assert profile.tool_names == assistant_turn_routing.ASSISTANT_DEFAULT_TOOL_NAMES
    assert profile.instructions is not None
    assert "recommendation mode" in profile.instructions


def test_turn_profile_keeps_feed_recommendations_non_mutating() -> None:
    """Feed recommendation prompts should stay in recommendation mode."""

    profile = assistant_turn_routing.resolve_assistant_turn_profile(
        "Recommend a few feeds, newsletters, or podcasts I should add "
        "based on what I've been reading."
    )

    assert profile.route == "feed_finder"
    assert profile.tool_names == assistant_turn_routing.ASSISTANT_DEFAULT_TOOL_NAMES
    assert profile.instructions is not None
    assert "recommendation mode" in profile.instructions
    assert "attached below for review" in profile.instructions


def test_turn_profile_resolves_weekly_ordinal_subscription_actions() -> None:
    """Weekly ordinal mutations should use frozen identities without rediscovery."""

    profile = assistant_turn_routing.resolve_assistant_turn_profile(
        "add first two",
        AssistantScreenContext(
            screen_type="weekly_discovery",
            screen_title="Weekly Discovery",
        ),
    )

    assert profile.route == "weekly_discovery_action"
    assert profile.tool_names == frozenset({"subscribe_to_feed"})
    assert profile.instructions is not None
    assert "canonical numbered weekly discovery identities" in profile.instructions
    assert "exact feed_url as url" in profile.instructions
    assert "exact suggestion_type as feed_type" in profile.instructions
    assert "Do not search for or re-detect" in profile.instructions


def test_seed_assistant_message_enqueues_chat_corpus_sync(
    db_session,
    test_user,
) -> None:
    session = ChatSession(
        user_id=test_user.id,
        title="Weekly seed",
        session_type="weekly_discovery",
        llm_provider="openai",
        llm_model="openai:gpt-5.6-terra",
    )
    db_session.add(session)
    db_session.flush()
    assert session.id is not None

    assistant_router.seed_assistant_message(
        db_session,
        session_id=session.id,
        assistant_text="A durable seed",
    )

    task = (
        db_session.query(ProcessingTask)
        .filter(ProcessingTask.task_type == TaskType.SYNC_AGENT_DATA.value)
        .one()
    )
    assert task.owner_user_id == test_user.id
    assert task.payload["chat_session_ids"] == [session.id]


def test_turn_profile_prefers_content_search_for_feed_summary() -> None:
    """Feed summary prompts should route to in-app search tools before web search."""

    profile = assistant_turn_routing.resolve_assistant_turn_profile(
        "Give me a summary of the last day's content from my feed, "
        "including recent news items and articles."
    )

    assert profile.route == "content_search"
    assert profile.tool_names == assistant_turn_routing.ASSISTANT_DEFAULT_TOOL_NAMES


def test_compound_turn_keeps_search_and_mutation_capabilities() -> None:
    profile = assistant_turn_routing.resolve_assistant_turn_profile(
        "Search my feed for climate articles and save the best one to Knowledge."
    )

    assert profile.route == "content_search"
    assert {"search_content", "save_to_knowledge"} <= profile.tool_names


def test_compound_feed_discovery_turn_can_subscribe_after_discovery() -> None:
    profile = assistant_turn_routing.resolve_assistant_turn_profile(
        "Search the web for a good Python newsletter and subscribe me to it."
    )

    assert profile.route == "feed_finder"
    assert {"find_feed_options", "subscribe_to_feed"} <= profile.tool_names


def test_compound_saved_and_current_turn_keeps_knowledge_and_web_search() -> None:
    profile = assistant_turn_routing.resolve_assistant_turn_profile(
        "Compare my saved article about Rust with the latest Rust release."
    )

    assert profile.route == "knowledge_search"
    assert {"search_knowledge", "search_web"} <= profile.tool_names


def test_turn_profile_requires_unread_news_tool_for_action() -> None:
    """The unread-news quick action should force the dedicated news tool."""

    profile = assistant_turn_routing.resolve_assistant_turn_profile(
        "Pick the best stories.",
        AssistantScreenContext(
            screen_type="short_news_feed",
            assistant_action=(assistant_turn_routing.ASSISTANT_ACTION_PICK_INTERESTING_UNREAD_NEWS),
        ),
    )

    assert profile.route == "pick_interesting_unread_news"
    assert profile.tool_names == frozenset({"list_unread_news_items", "search_web"})
    assert profile.instructions is not None
    assert "Do not mark items read" in profile.instructions


def test_turn_profile_skips_small_talk_tools_and_instructions() -> None:
    """Small talk should not force a tool route."""

    profile = assistant_turn_routing.resolve_assistant_turn_profile("hello")

    assert profile.route == "small_talk"
    assert profile.instructions is None
    assert profile.tool_names == frozenset()


def test_learning_deck_explanation_stays_grounded_without_tool_schemas() -> None:
    screen_context = AssistantScreenContext(
        screen_type="learning_deck",
        screen_title="Distributed systems",
        note="Slide text: Leases establish temporary ownership.",
    )

    profile = assistant_turn_routing.resolve_assistant_turn_profile(
        "Why does this matter?",
        screen_context,
    )
    tool_defs = [
        ToolDefinition(name="search_web"),
        ToolDefinition(name="search_knowledge"),
    ]
    context = cast(
        RunContext[assistant_router.AssistantDeps],
        SimpleNamespace(
            deps=SimpleNamespace(
                turn_profile=profile,
                session_id=1,
                user_id=1,
            )
        ),
    )
    selected = asyncio.run(assistant_router._prepare_assistant_tools(context, tool_defs))

    assert profile.route == "learning_deck_grounded"
    assert profile.tool_names == frozenset()
    assert selected == []


def test_learning_deck_explicit_current_request_exposes_only_exa_web_search() -> None:
    profile = assistant_turn_routing.resolve_assistant_turn_profile(
        "Search online for the latest developments",
        AssistantScreenContext(screen_type="learning_deck"),
    )

    assert profile.route == "web_search"
    assert profile.tool_names == frozenset({"search_web"})


def test_learning_deck_current_slide_reference_does_not_trigger_web_search() -> None:
    profile = assistant_turn_routing.resolve_assistant_turn_profile(
        "Can you explain the current slide?",
        AssistantScreenContext(screen_type="learning_deck"),
    )

    assert profile.route == "learning_deck_grounded"
    assert profile.tool_names == frozenset()


def test_learning_deck_grounding_precedes_general_feed_and_library_routes() -> None:
    screen_context = AssistantScreenContext(screen_type="learning_deck")

    feed_profile = assistant_turn_routing.resolve_assistant_turn_profile(
        "Explain this in the context of my feed",
        screen_context,
    )
    library_profile = assistant_turn_routing.resolve_assistant_turn_profile(
        "How does this relate to my saved markdown?",
        screen_context,
    )

    assert feed_profile.route == "learning_deck_grounded"
    assert feed_profile.tool_names == frozenset()
    assert library_profile.route == "learning_deck_grounded"
    assert library_profile.tool_names == frozenset()


def test_learning_deck_current_practice_request_exposes_exa_web_search() -> None:
    profile = assistant_turn_routing.resolve_assistant_turn_profile(
        "How does this compare with current best practices?",
        AssistantScreenContext(screen_type="learning_deck"),
    )

    assert profile.route == "web_search"
    assert profile.tool_names == frozenset({"search_web"})


def test_default_route_does_not_start_the_agent_vm() -> None:
    profile = assistant_turn_routing.resolve_assistant_turn_profile(
        "Help me think this through",
        AssistantScreenContext(screen_type="assistant_quick"),
    )

    assert profile.route == "default"
    assert profile.uses_agent_vm is False


def test_learning_deck_route_does_not_start_the_agent_vm(
    db_session,
    test_user,
    monkeypatch,
) -> None:
    session = ChatSession(
        user_id=test_user.id,
        title="Deck chat",
        session_type="knowledge_chat",
        context_snapshot="Current slide: Leases establish temporary ownership.",
        llm_provider="openai",
        llm_model="openai:gpt-5.6-terra",
    )
    db_session.add(session)
    db_session.commit()
    assert session.id is not None
    turn_context = build_chat_turn_context(
        session,
        visible_session_id=session.id,
        user_prompt="Explain this simply",
        kind="assistant",
        source="assistant",
        screen_context=AssistantScreenContext(screen_type="learning_deck"),
    )
    turn = chat_turn_runtime.snapshot_detached_chat_turn_from_snapshot(
        turn_context.session,
        message_id=10,
        source="assistant",
        task_id=20,
    )
    monkeypatch.setattr(assistant_router, "load_message_history", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(assistant_router, "resolve_effective_api_key", lambda **_kwargs: None)
    monkeypatch.setattr(
        assistant_router,
        "_build_assistant_vm_runtime",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("sandbox must stay lazy")),
    )

    prepared = assistant_router._prepare_assistant_background_turn(
        db_session,
        turn_context.session,
        turn,
        screen_context=AssistantScreenContext(screen_type="learning_deck"),
        user_prompt="Explain this simply",
    )

    assert prepared.deps.turn_profile.route == "learning_deck_grounded"
    assert prepared.deps.vm_runtime is None


def test_markdown_route_builds_one_lazy_vm_runtime(
    db_session,
    test_user,
    monkeypatch,
) -> None:
    session = ChatSession(
        user_id=test_user.id,
        title="Markdown library chat",
        session_type="knowledge_chat",
        context_snapshot="Knowledge hub",
        llm_provider="openai",
        llm_model="openai:gpt-5.6-terra",
    )
    db_session.add(session)
    db_session.commit()
    assert session.id is not None
    screen_context = AssistantScreenContext(screen_type="assistant_quick")
    prompt = "Read my saved markdown file"
    turn_context = build_chat_turn_context(
        session,
        visible_session_id=session.id,
        user_prompt=prompt,
        kind="assistant",
        source="assistant",
        screen_context=screen_context,
    )
    turn = chat_turn_runtime.snapshot_detached_chat_turn_from_snapshot(
        turn_context.session,
        message_id=10,
        source="assistant",
        task_id=20,
    )
    sandbox = SimpleNamespace(acquired=False)
    runtime_calls: list[int] = []
    monkeypatch.setattr(assistant_router, "load_message_history", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(assistant_router, "resolve_effective_api_key", lambda **_kwargs: None)

    def build_runtime(*, user_id, session_id, llm_task_id):
        runtime_calls.append(user_id)
        assert session_id == session.id
        assert llm_task_id == turn.llm_task_id
        return sandbox

    monkeypatch.setattr(
        assistant_router,
        "_build_assistant_vm_runtime",
        build_runtime,
    )

    prepared = assistant_router._prepare_assistant_background_turn(
        db_session,
        turn_context.session,
        turn,
        screen_context=screen_context,
        user_prompt=prompt,
    )

    assert prepared.deps.turn_profile.route == "markdown_library"
    assert prepared.deps.vm_runtime is sandbox
    assert runtime_calls == [test_user.id]


def test_build_screen_context_snapshot_omits_user_id(db_session, test_user) -> None:
    """Screen snapshots should not expose backend user IDs to model-visible text."""

    snapshot = assistant_router.build_screen_context_snapshot(
        db_session,
        user_id=test_user.id,
        screen_context=AssistantScreenContext(
            screen_type="knowledge_hub",
            screen_title="Knowledge",
        ),
    )

    assert "Screen Type: knowledge_hub" in snapshot
    assert "User ID:" not in snapshot


def test_get_or_create_agent_uses_shared_model_builder(monkeypatch) -> None:
    """Assistant agent construction should use the shared model factory."""

    chat_turn_runtime.clear_agent_cache_for_tests()
    calls: list[tuple[str, str | None, str | None]] = []
    sentinel_model = TestModel(custom_output_text="ok")

    def _fake_build(
        model_spec: str,
        *,
        api_key_override: str | None = None,
        openai_reasoning_effort: str | None = None,
    ):
        calls.append((model_spec, api_key_override, openai_reasoning_effort))
        return sentinel_model, {"timeout": 5}

    monkeypatch.setattr(assistant_router, "build_pydantic_model", _fake_build)

    agent = assistant_router._get_or_create_agent(
        "openai:gpt-5.6-terra",
        api_key_override="user-key",
    )

    assert calls == [("openai:gpt-5.6-terra", "user-key", "low")]
    assert agent.model is sentinel_model

    chat_turn_runtime.clear_agent_cache_for_tests()


def test_known_feed_subscription_tool_persists_once_and_is_idempotent(
    db_session,
    db_session_factory,
    test_user,
    monkeypatch,
) -> None:
    """Known weekly options should subscribe directly with a durable backfill."""
    stub_feed_validator(monkeypatch, title="Example Feed")
    monkeypatch.setattr(
        assistant_router,
        "build_pydantic_model",
        lambda *_args, **_kwargs: (TestModel(custom_output_text="ok"), {}),
    )
    agent = assistant_router._create_assistant_agent("test:model")
    subscribe = agent._function_toolset.tools["subscribe_to_feed"].function
    context = cast(
        RunContext[Any],
        SimpleNamespace(
            deps=SimpleNamespace(
                user_id=test_user.id,
                session_factory=db_session_factory,
            )
        ),
    )

    first_result = subscribe(
        context,
        "HTTPS://Example.COM/feed.xml/",
        "Example Feed",
        "atom",
    )
    second_result = subscribe(
        context,
        "https://example.com/feed.xml",
        "Example Feed",
        "atom",
    )

    db_session.expire_all()
    configs = (
        db_session.query(UserScraperConfig).filter(UserScraperConfig.user_id == test_user.id).all()
    )
    assert first_result == "Subscribed to Example Feed."
    assert second_result == "Already subscribed to Example Feed."
    assert len(configs) == 1
    assert configs[0].scraper_type == "atom"
    assert configs[0].feed_url == "https://example.com/feed.xml"
    backfill_task = (
        db_session.query(ProcessingTask)
        .filter(ProcessingTask.task_type == TaskType.BACKFILL_FEEDS.value)
        .one()
    )
    assert backfill_task.owner_user_id == test_user.id
    assert backfill_task.payload["config_ids"] == [configs[0].id]


def test_known_feed_subscription_tool_rejects_bad_identity_and_rolls_back_queue_failure(
    db_session,
    db_session_factory,
    test_user,
    monkeypatch,
) -> None:
    """Invalid options and failed durable work must not persist subscriptions."""
    stub_feed_validator(monkeypatch, title="Rollback Feed")
    monkeypatch.setattr(
        assistant_router,
        "build_pydantic_model",
        lambda *_args, **_kwargs: (TestModel(custom_output_text="ok"), {}),
    )
    agent = assistant_router._create_assistant_agent("test:model")
    subscribe = agent._function_toolset.tools["subscribe_to_feed"].function
    context = cast(
        RunContext[Any],
        SimpleNamespace(
            deps=SimpleNamespace(
                user_id=test_user.id,
                session_factory=db_session_factory,
            )
        ),
    )

    assert (
        subscribe(
            context,
            "https://example.com/feed.xml",
            "Future Feed",
            "future_feed_type",
        )
        == "Unable to subscribe: unsupported feed type future_feed_type."
    )
    assert (
        subscribe(
            context,
            "not a URL",
            "Malformed Feed",
            "atom",
        )
        == "Unable to subscribe: invalid feed URL."
    )

    class _FailingQueueService:
        def enqueue_many_in_session(self, _db, _requests):
            raise RuntimeError("queue unavailable")

    monkeypatch.setattr(
        "app.commands.subscribe_feed.get_queue_service",
        lambda: _FailingQueueService(),
    )

    assert (
        subscribe(
            context,
            "https://example.com/rollback.xml",
            "Rollback Feed",
            "atom",
        )
        == "Unable to subscribe to Rollback Feed (temporary failure)."
    )

    db_session.expire_all()
    assert db_session.query(UserScraperConfig).count() == 0
    assert (
        db_session.query(ProcessingTask)
        .filter(ProcessingTask.task_type == TaskType.BACKFILL_FEEDS.value)
        .count()
        == 0
    )


def test_find_subscription_content_matches_uses_active_feed_names(
    db_session,
    test_user,
) -> None:
    """Subscription-aware search should find feed items beyond the stored source label."""

    config = UserScraperConfig(
        user_id=test_user.id,
        scraper_type="podcast_rss",
        display_name="BG2 Pod",
        feed_url="https://anchor.fm/s/f06c2370/podcast/rss",
        config={"feed_url": "https://anchor.fm/s/f06c2370/podcast/rss", "limit": 10},
        is_active=True,
    )
    db_session.add(config)
    db_session.flush()

    rows: list[Content] = []
    for idx, (title, source) in enumerate(
        [
            (
                "ChatGPT – The Super Assistant Era | BG2 Guest Interview",
                "BG2 Pod",
            ),
            (
                "Inside OpenAI Enterprise: Forward Deployed Engineering, GPT-5, "
                "and More | BG2 Guest Interview",
                "podcasters.spotify.com",
            ),
            (
                "China, China, China. Breaking Down China’s Tech Surge | BG2 "
                "w/ Bill Gurley and Brad Gerstner",
                "podcasters.spotify.com",
            ),
        ],
        start=1,
    ):
        content = Content(
            content_type="podcast",
            url=f"https://podcasters.spotify.com/pod/show/bg2pod/episodes/test-{idx}",
            title=title,
            source=source,
            status="completed",
            content_metadata={},
        )
        db_session.add(content)
        db_session.flush()
        db_session.add(
            ContentStatusEntry(
                user_id=test_user.id,
                content_id=content.id,
                status="inbox",
            )
        )
        rows.append(content)

    unrelated = Content(
        content_type="podcast",
        url="https://example.com/other-show",
        title="An unrelated podcast episode",
        source="Other Show",
        status="completed",
        content_metadata={},
    )
    db_session.add(unrelated)
    db_session.flush()
    db_session.add(
        ContentStatusEntry(
            user_id=test_user.id,
            content_id=unrelated.id,
            status="inbox",
        )
    )
    db_session.commit()

    matches, total_matches = search_subscription_feeds(
        db_session,
        user_id=test_user.id,
        query_text="How many BG2 pods do I have in my feed?",
        limit=10,
    )

    assert total_matches == 3
    assert [content.id for content, _, _ in matches] == [rows[2].id, rows[1].id, rows[0].id]


def test_format_content_hits_reports_total_matches() -> None:
    """Formatted search_content responses should include the total match count."""

    content = Content(
        id=42,
        content_type="podcast",
        url="https://example.com/bg2",
        title="BG2 episode",
        source="BG2 Pod",
        status="completed",
        content_metadata={},
    )

    formatted = assistant_router._format_content_hits(
        query="BG2 pods",
        content_rows=[(content, object(), None)],
        total_content_matches=13,
    )

    assert "Feed Content (13 total matches, showing 1):" in formatted


def test_format_content_hits_prefers_summary_display_title() -> None:
    """Formatted search_content responses should use the canonical summary display title."""

    content = Content(
        id=42,
        content_type="article",
        url="https://example.com/bg2",
        title="Stored page title",
        source="BG2 Pod",
        status="completed",
        content_metadata={
            "summary": {
                "title": "Canonical summary title",
                "overview": "Short summary",
            }
        },
    )

    formatted = assistant_router._format_content_hits(
        query="summary title",
        content_rows=[(content, object(), None)],
        total_content_matches=1,
    )

    assert "Canonical summary title" in formatted
    assert "Stored page title" not in formatted


def test_format_content_hits_includes_news_item_section(visible_news_item) -> None:
    """Formatted search_news responses should include recent news items."""

    formatted = assistant_router._format_content_hits(
        query="recent news items from my feed",
        content_rows=[],
        total_content_matches=0,
        news_item_rows=[(visible_news_item, None)],
        total_news_item_matches=0,
    )

    assert "Recent News Items:" in formatted
    assert f"[news:{visible_news_item.id}]" in formatted
    assert "summary:" in formatted


def test_unread_news_items_payload_filters_read_items_and_reports_truncation(
    db_session,
    test_user,
    news_item_factory,
) -> None:
    """Unread-news tool payload should expose unread visible rows with counts."""

    older_unread = news_item_factory(
        summary_title="Older unread story",
        summary_text="Older unread summary",
        visibility_scope="user",
        owner_user_id=test_user.id,
        ingested_at=(datetime.now(UTC) - timedelta(hours=3)).replace(tzinfo=None),
    )
    read_item = news_item_factory(
        summary_title="Already read story",
        summary_text="Already read summary",
        visibility_scope="user",
        owner_user_id=test_user.id,
        ingested_at=(datetime.now(UTC) - timedelta(hours=2)).replace(tzinfo=None),
    )
    newer_unread = news_item_factory(
        summary_title="Newer unread story",
        summary_text="Newer unread summary",
        summary_key_points=["Newer unread point"],
        raw_metadata={"top_comment": {"author": "Reader", "text": "Sharp comment"}},
        visibility_scope="user",
        owner_user_id=test_user.id,
        ingested_at=(datetime.now(UTC) - timedelta(hours=1)).replace(tzinfo=None),
    )
    db_session.add(NewsItemReadStatus(user_id=test_user.id, news_item_id=read_item.id))
    db_session.commit()

    payload = assistant_router._build_unread_news_items_payload(
        db_session,
        user_id=test_user.id,
        limit=1,
    )

    assert payload["total_count"] == 2
    assert payload["returned_count"] == 1
    assert payload["truncated"] is True
    items = payload["items"]
    assert isinstance(items, list)
    assert all(isinstance(item, dict) for item in items)
    assert [item["id"] for item in items] == [newer_unread.id]
    assert items[0]["title"] == "Newer unread story"
    assert items[0]["summary"] == "Newer unread summary"
    assert items[0]["key_points"] == ["Newer unread point"]
    assert items[0]["top_comment"] == {
        "author": "Reader",
        "text": "Sharp comment",
    }
    assert read_item.id not in {item["id"] for item in items}
    assert older_unread.id not in {item["id"] for item in items}


def test_search_news_returns_recent_visible_rows(
    db_session,
    test_user,
    news_item_factory,
) -> None:
    """Generic feed-summary prompts should fall back to recent visible news items."""

    older_item = news_item_factory(
        summary_title="Older policy story",
        summary_text="Older policy summary",
        visibility_scope="user",
        owner_user_id=test_user.id,
        ingested_at=(datetime.now(UTC) - timedelta(hours=3)).replace(tzinfo=None),
    )
    newer_item = news_item_factory(
        summary_title="Newer chip story",
        summary_text="Newer chip summary",
        visibility_scope="user",
        owner_user_id=test_user.id,
        ingested_at=(datetime.now(UTC) - timedelta(hours=1)).replace(tzinfo=None),
    )

    rows, total_matches = search_news(
        db_session,
        user_id=test_user.id,
        query_text="Give me a summary of the last day's content from my feed.",
        limit=5,
    )

    assert total_matches == 0
    assert [item.id for item, _is_read in rows[:2]] == [newer_item.id, older_item.id]


def test_search_news_uses_metadata_titles(
    db_session,
    test_user,
    news_item_factory,
) -> None:
    """News-item search should match canonical titles stored in raw metadata."""

    matched_item = news_item_factory(
        article_title="This is a great discussion.",
        summary_title="This is a great discussion.",
        raw_metadata={
            "summary": {
                "title": (
                    "Jeremy Howard Launches SolveIt Method to Promote AI-Assisted Craftsmanship"
                )
            }
        },
        summary_text="Summary about SolveIt and AI-assisted craftsmanship.",
        visibility_scope="user",
        owner_user_id=test_user.id,
    )
    news_item_factory(
        summary_title="Completely unrelated story",
        summary_text="Summary about semiconductors.",
        visibility_scope="user",
        owner_user_id=test_user.id,
    )
    db_session.commit()

    rows, total_matches = search_news(
        db_session,
        user_id=test_user.id,
        query_text="SolveIt craftsmanship",
        limit=5,
    )

    assert total_matches == 1
    assert [item.id for item, _is_read in rows] == [matched_item.id]


def test_build_assistant_vm_runtime_is_absent_when_sandbox_disabled(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_task_sandbox_provider", "disabled")

    vm_runtime = assistant_router._build_assistant_vm_runtime(
        user_id=42,
        session_id=7,
    )

    assert vm_runtime is None


def test_process_assistant_turn_persists_completion_usage_and_ledger(
    db_session,
    test_user,
    monkeypatch,
) -> None:
    session = ChatSession(
        user_id=test_user.id,
        title="Detached assistant chat",
        session_type="knowledge_chat",
        context_snapshot="Knowledge snapshot",
        llm_provider="openai",
        llm_model="openai:gpt-5.6-terra",
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)
    assert session.id is not None
    session_id = int(session.id)
    message = create_processing_message(db_session, session_id, "Find my saved article")
    assert message.id is not None
    message_id = int(message.id)
    usage_calls: list[tuple[int, int | None, str]] = []

    monkeypatch.setattr(assistant_router, "load_message_history", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        assistant_router,
        "_build_assistant_vm_runtime",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(assistant_router, "resolve_effective_api_key", lambda **_kwargs: None)
    monkeypatch.setattr(
        assistant_router,
        "_log_chat_usage",
        lambda _result, _snapshot, sid, mid, context: usage_calls.append((sid, mid, context)),
    )

    async def _fake_run_in_threadpool(_func, _model, prompt, deps, history, **_kwargs):
        assert prompt == "Find my saved article"
        assert deps.session_id == session_id
        assert deps.user_id == test_user.id
        assert deps.context_snapshot == "Knowledge snapshot"
        assert history == []
        messages = [
            ModelRequest(parts=[UserPromptPart(content="model-facing assistant prompt")]),
            ModelResponse(parts=[TextPart(content="Here is the saved article")]),
        ]
        return SimpleNamespace(
            output="Here is the saved article",
            new_messages=lambda: messages,
        )

    monkeypatch.setattr(assistant_router, "run_in_threadpool", _fake_run_in_threadpool)

    screen_context = AssistantScreenContext(
        screen_type="knowledge_hub",
        screen_title="Knowledge",
    )
    turn_context = build_chat_turn_context(
        session,
        visible_session_id=session_id,
        user_prompt="Find my saved article",
        kind="assistant",
        source="queue",
        screen_context=screen_context,
    )
    asyncio.run(
        assistant_router.process_assistant_turn_async(
            session_id,
            message_id,
            "Find my saved article",
            screen_context=screen_context,
            turn_context=turn_context,
            stream_generation=0,
            ensure_lease=lambda: True,
        )
    )

    db_session.expire_all()
    persisted_message = db_session.query(ChatMessage).filter_by(id=message_id).one()
    ledger = (
        db_session.query(LlmTask)
        .filter(LlmTask.workflow_key == "chat.contextual_assistant.v1")
        .order_by(LlmTask.id.desc())
        .first()
    )
    assert persisted_message.status == MessageProcessingStatus.COMPLETED.value
    assert json.loads(persisted_message.message_list)[0]["parts"][0]["content"] == (
        "Find my saved article"
    )
    assert usage_calls == [(session_id, message_id, "assistant")]
    assert ledger is not None
    assert ledger.status == LlmTaskStatus.COMPLETED.value
    assert ledger.input_json["screen_type"] == "knowledge_hub"
    assert ledger.output_json["message_id"] == message_id
