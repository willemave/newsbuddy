"""Tests for Quick Assistant routing heuristics."""

from pydantic_ai.models.test import TestModel

from app.core.settings import get_settings
from app.models.schema import Content, ContentStatusEntry, UserScraperConfig
from app.services import assistant_router


def test_build_turn_instructions_prefers_knowledge_for_favorites() -> None:
    """Favorite/saved prompts should route to SearchKnowledge."""

    instructions = assistant_router._build_turn_instructions("What is my favorite article?")

    assert instructions is not None
    assert "SearchKnowledge" in instructions


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


def test_build_screen_aware_turn_instructions_prefers_content_search_for_digests() -> None:
    """Digest prompts should route to SearchContent before web search."""

    instructions = assistant_router._build_screen_aware_turn_instructions(
        "Can you summarize my recent daily news digests?",
        assistant_router.AssistantScreenContext(
            screen_type="daily_digest_list",
            screen_title="Daily News Digests",
        ),
    )

    assert instructions is not None
    assert "SearchContent" in instructions


def test_build_turn_instructions_skips_small_talk() -> None:
    """Small talk should not force a tool route."""

    assert assistant_router._build_turn_instructions("hello") is None


def test_get_or_create_agent_uses_shared_model_builder(monkeypatch) -> None:
    """Assistant agent construction should use the shared model factory."""

    assistant_router._agents.clear()
    calls: list[tuple[str, str | None]] = []
    sentinel_model = TestModel(custom_output_text="ok")

    def _fake_build(model_spec: str, *, api_key_override: str | None = None):
        calls.append((model_spec, api_key_override))
        return sentinel_model, {"timeout": 5}

    monkeypatch.setattr(assistant_router, "build_pydantic_model", _fake_build)

    agent = assistant_router._get_or_create_agent(
        "openai:gpt-5.4",
        api_key_override="user-key",
    )

    assert calls == [("openai:gpt-5.4", "user-key")]
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

    matches, total_matches = assistant_router._find_subscription_content_matches(
        db_session,
        user_id=test_user.id,
        query="How many BG2 pods do I have in my feed?",
        limit=10,
    )

    assert total_matches == 3
    assert [content.id for content, _, _ in matches] == [rows[2].id, rows[1].id, rows[0].id]


def test_format_content_hits_reports_total_matches() -> None:
    """Formatted SearchContent responses should include the total match count."""

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
        digest_rows=[],
        digest_bullets_by_digest_id={},
    )

    assert "Feed Content (13 total matches, showing 1):" in formatted


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
