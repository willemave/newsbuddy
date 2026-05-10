"""Tests for contextual assistant routing heuristics."""

from datetime import UTC, datetime, timedelta

from pydantic_ai.models.test import TestModel

from app.core.settings import get_settings
from app.models.db import Content, ContentStatusEntry, NewsItemReadStatus, UserScraperConfig
from app.models.internal.assistant import AssistantScreenContext
from app.repositories.search_repository import (
    search_news,
    search_subscription_feeds,
)
from app.services import assistant_router


def test_build_turn_instructions_prefers_knowledge_for_saved_content_prompts() -> None:
    """Favorite/saved prompts should route to search_knowledge."""

    instructions = assistant_router._build_turn_instructions("What is my favorite article?")

    assert instructions is not None
    assert "search_knowledge" in instructions


def test_build_turn_instructions_prefers_web_for_recent_questions() -> None:
    """Recent factual prompts should route to web search."""

    instructions = assistant_router._build_turn_instructions("What is the latest Rust release?")

    assert instructions is not None
    assert "search_web" in instructions


def test_build_turn_instructions_prefers_feed_finder_for_blog_subscription() -> None:
    """Feed/blog discovery prompts should route to the feed finder tool."""

    instructions = assistant_router._build_turn_instructions(
        "please find a blog by Armin Ronacher and subscribe to it"
    )

    assert instructions is not None
    assert "find_feed_options" in instructions
    assert "subscribe_to_feed" in instructions
    assert "recommendation mode" in instructions


def test_build_turn_instructions_keeps_feed_recommendations_non_mutating() -> None:
    """Feed recommendation prompts should stay in recommendation mode."""

    instructions = assistant_router._build_turn_instructions(
        "Recommend a few feeds, newsletters, or podcasts I should add "
        "based on what I've been reading."
    )

    assert instructions is not None
    assert "find_feed_options" in instructions
    assert "recommendation mode" in instructions
    assert "attached below for review" in instructions


def test_build_turn_instructions_prefers_content_search_for_feed_summary() -> None:
    """Feed summary prompts should route to in-app search tools before web search."""

    instructions = assistant_router._build_turn_instructions(
        "Give me a summary of the last day's content from my feed, "
        "including recent news items and articles."
    )

    assert instructions is not None
    assert "search_content" in instructions
    assert "search_news" in instructions


def test_build_turn_instructions_requires_unread_news_tool_for_action() -> None:
    """The unread-news quick action should force the dedicated news tool."""

    instructions = assistant_router._build_turn_instructions(
        "Pick the best stories.",
        AssistantScreenContext(
            screen_type="short_news_feed",
            assistant_action=assistant_router.ASSISTANT_ACTION_PICK_INTERESTING_UNREAD_NEWS,
        ),
    )

    assert instructions is not None
    assert "list_unread_news_items" in instructions
    assert "Do not mark items read" in instructions


def test_build_turn_instructions_skips_small_talk() -> None:
    """Small talk should not force a tool route."""

    assert assistant_router._build_turn_instructions("hello") is None


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

    assistant_router._agents.clear()
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
        "openai:gpt-5.5",
        api_key_override="user-key",
    )

    assert calls == [("openai:gpt-5.5", "user-key", "low")]
    assert agent.model is sentinel_model

    assistant_router._agents.clear()


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


def test_build_assistant_personal_library_runtime_skips_sync_when_sandbox_disabled(
    db_session,
    monkeypatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "personal_markdown_enabled", True)
    monkeypatch.setattr(settings, "chat_sandbox_provider", "disabled")

    sync_calls: list[int] = []

    def _unexpected_sync(_db, *, user_id: int):  # noqa: ANN001
        sync_calls.append(user_id)
        raise AssertionError(
            "assistant personal markdown sync should not run when sandbox is disabled"
        )

    monkeypatch.setattr(
        assistant_router,
        "sync_personal_markdown_library_for_user",
        _unexpected_sync,
    )

    sandbox_session, personal_library_error = (
        assistant_router._build_assistant_personal_library_runtime(
            db=db_session,
            user_id=42,
        )
    )

    assert sandbox_session is None
    assert personal_library_error is None
    assert sync_calls == []
