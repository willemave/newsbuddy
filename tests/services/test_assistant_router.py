"""Tests for Quick Assistant routing heuristics."""

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
