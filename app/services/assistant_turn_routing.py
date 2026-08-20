"""Pure per-turn capability routing for the contextual assistant."""

from __future__ import annotations

from dataclasses import dataclass

from app.models.internal.assistant import AssistantScreenContext
from app.services.prompt_library import load_prompt

ASSISTANT_ACTION_PICK_INTERESTING_UNREAD_NEWS = "pick_interesting_unread_news"
ASSISTANT_WEB_TOOL = "search_web"
ASSISTANT_PERSONAL_LIBRARY_TOOLS = frozenset(
    {
        "SearchMarkdownLibrary",
        "ListMarkdownLibrary",
        "ReadMarkdownFile",
    }
)
ASSISTANT_DEFAULT_TOOL_NAMES = frozenset(
    {
        ASSISTANT_WEB_TOOL,
        "find_feed_options",
        "search_knowledge",
        "search_subscription_feeds",
        "search_content",
        "search_news",
        "list_unread_news_items",
        "add_item_to_feed",
        "subscribe_to_feed",
        "save_to_knowledge",
        "remove_from_knowledge",
        "mark_content_read",
        "mark_content_unread",
        "convert_news_to_article_tool",
        "start_deep_research_handoff",
    }
)

_SMALL_TALK_PHRASES = {
    "hi",
    "hello",
    "hey",
    "yo",
    "thanks",
    "thank you",
    "how are you",
    "good morning",
    "good afternoon",
    "good evening",
    "hi there",
    "hello there",
}
_KNOWLEDGE_HINTS = (
    "my favorite",
    "my favourites",
    "my favorites",
    "my saved",
    "my bookmarked",
    "what did i save",
    "what i saved",
    "my article",
    "my podcast",
    "i read",
    "i listened",
    "favorited",
)
_CONTENT_SEARCH_HINTS = (
    "in my feed",
    "in my inbox",
    "from my feed",
    "from my inbox",
    "my feed",
    "last day's content",
    "recent news items",
    "news items and articles",
    "recent articles",
    "recent posts",
)
_WEB_HINTS = (
    "latest",
    "recent",
    "today",
    "current",
    "news",
    "find",
    "look up",
    "search",
    "who is",
    "what is",
    "what are",
    "how to",
)
_SOURCE_RECOMMENDATION_HINTS = (
    "blogs",
    "blog",
    "publications",
    "publication",
    "newsletters",
    "newsletter",
    "sites",
    "sources",
)
_FEED_DISCOVERY_HINTS = (
    "feed",
    "feeds",
    "rss",
    "atom",
    "blog",
    "blogs",
    "newsletter",
    "newsletters",
    "podcast",
    "podcasts",
)
_FEED_DISCOVERY_ACTION_HINTS = (
    "find",
    "search",
    "look up",
    "discover",
    "recommend",
    "subscribe",
)


@dataclass(frozen=True)
class AssistantTurnProfile:
    """Primary instructions and available capabilities for one assistant turn."""

    route: str
    instructions: str | None
    tool_names: frozenset[str]

    @property
    def uses_personal_library(self) -> bool:
        return bool(self.tool_names & ASSISTANT_PERSONAL_LIBRARY_TOOLS)


def resolve_assistant_turn_profile(
    user_text: str,
    screen_context: AssistantScreenContext | None = None,
) -> AssistantTurnProfile:
    """Resolve one deterministic route without persistence or network work."""

    if (
        screen_context is not None
        and screen_context.assistant_action == ASSISTANT_ACTION_PICK_INTERESTING_UNREAD_NEWS
    ):
        return AssistantTurnProfile(
            route="pick_interesting_unread_news",
            instructions=load_prompt("chat/contextual_assistant#turn_pick_interesting_unread_news"),
            tool_names=frozenset({"list_unread_news_items", ASSISTANT_WEB_TOOL}),
        )
    if _should_route_to_weekly_discovery_action(user_text, screen_context):
        return AssistantTurnProfile(
            route="weekly_discovery_action",
            instructions=load_prompt("chat/contextual_assistant#turn_weekly_discovery_action"),
            tool_names=frozenset({"subscribe_to_feed"}),
        )
    if _is_small_talk(user_text):
        return AssistantTurnProfile(route="small_talk", instructions=None, tool_names=frozenset())
    is_learning_deck = bool(
        screen_context is not None and screen_context.screen_type == "learning_deck"
    )
    if is_learning_deck:
        if _should_route_learning_deck_to_web(user_text):
            return AssistantTurnProfile(
                route="web_search",
                instructions=load_prompt("chat/contextual_assistant#turn_web_search"),
                tool_names=frozenset({ASSISTANT_WEB_TOOL}),
            )
        return AssistantTurnProfile(
            route="learning_deck_grounded",
            instructions=load_prompt("chat/contextual_assistant#turn_learning_deck_grounded"),
            tool_names=frozenset(),
        )
    if _should_route_to_feed_finder(user_text):
        return AssistantTurnProfile(
            route="feed_finder",
            instructions=load_prompt("chat/contextual_assistant#turn_feed_finder"),
            tool_names=ASSISTANT_DEFAULT_TOOL_NAMES,
        )
    if _should_route_to_markdown_library(user_text):
        return AssistantTurnProfile(
            route="markdown_library",
            instructions=load_prompt("chat/contextual_assistant#turn_markdown_library"),
            tool_names=ASSISTANT_PERSONAL_LIBRARY_TOOLS | ASSISTANT_DEFAULT_TOOL_NAMES,
        )
    if _should_route_to_content_search(user_text):
        return AssistantTurnProfile(
            route="content_search",
            instructions=load_prompt("chat/contextual_assistant#turn_content_search"),
            tool_names=ASSISTANT_DEFAULT_TOOL_NAMES,
        )
    if _should_route_to_knowledge(user_text):
        return AssistantTurnProfile(
            route="knowledge_search",
            instructions=load_prompt("chat/contextual_assistant#turn_knowledge_search"),
            tool_names=ASSISTANT_DEFAULT_TOOL_NAMES,
        )

    if _should_route_to_web(user_text):
        normalized = _normalize_turn_text(user_text)
        if any(hint in normalized for hint in _SOURCE_RECOMMENDATION_HINTS):
            return AssistantTurnProfile(
                route="source_recommendation",
                instructions=load_prompt("chat/contextual_assistant#turn_source_recommendation"),
                tool_names=ASSISTANT_DEFAULT_TOOL_NAMES,
            )
        return AssistantTurnProfile(
            route="web_search",
            instructions=load_prompt("chat/contextual_assistant#turn_web_search"),
            tool_names=ASSISTANT_DEFAULT_TOOL_NAMES,
        )
    return AssistantTurnProfile(
        route="default",
        instructions=load_prompt("chat/contextual_assistant#turn_default_tool_preference"),
        tool_names=ASSISTANT_DEFAULT_TOOL_NAMES,
    )


def _normalize_turn_text(user_text: str) -> str:
    return " ".join(user_text.strip().lower().split())


def _is_small_talk(user_text: str) -> bool:
    normalized = _normalize_turn_text(user_text)
    return not normalized or normalized in _SMALL_TALK_PHRASES


def _should_route_to_knowledge(user_text: str) -> bool:
    normalized = _normalize_turn_text(user_text)
    if " my " in f" {normalized} " and any(
        marker in normalized for marker in ("favorite", "saved", "bookmarked", "article", "podcast")
    ):
        return True
    return any(hint in normalized for hint in _KNOWLEDGE_HINTS)


def _should_route_to_markdown_library(user_text: str) -> bool:
    normalized = _normalize_turn_text(user_text)
    if not normalized:
        return False
    markdown_hints = ("markdown", "file path", "filepath", "source md", "summary md", ".md")
    file_hints = ("saved file", "library file", "raw markdown", "summary markdown")
    if any(hint in normalized for hint in markdown_hints + file_hints):
        return True
    return "path" in normalized and _should_route_to_knowledge(normalized)


def _should_route_to_web(user_text: str) -> bool:
    normalized = _normalize_turn_text(user_text)
    if _should_route_to_knowledge(normalized):
        return False
    if _should_route_to_feed_finder(normalized):
        return False
    if _is_small_talk(normalized):
        return False
    if any(hint in normalized for hint in _WEB_HINTS):
        return True
    return "?" in user_text and normalized.startswith(
        ("what ", "who ", "when ", "where ", "why ", "how ")
    )


def _should_route_learning_deck_to_web(user_text: str) -> bool:
    normalized = _normalize_turn_text(user_text)
    explicit_web_hints = (
        "search the web",
        "search online",
        "search the internet",
        "look up",
        "online sources",
        "external sources",
        "browse the web",
        "up to date",
        "current developments",
        "current events",
        "current research",
        "current best practice",
        "current status",
        "current version",
        "latest news",
        "recent news",
        "news today",
        "verify online",
        "fact check",
    )
    if any(hint in normalized for hint in explicit_web_hints):
        return True

    temporal_text = normalized
    for phrase in ("current slide", "current deck", "current card"):
        temporal_text = temporal_text.replace(phrase, "")
    return any(hint in temporal_text for hint in ("latest", "recent", "today", "currently"))


def _contains_explicit_url(user_text: str) -> bool:
    normalized = _normalize_turn_text(user_text)
    return "http://" in normalized or "https://" in normalized


def _should_route_to_feed_finder(user_text: str) -> bool:
    normalized = _normalize_turn_text(user_text)
    if _contains_explicit_url(normalized):
        return False
    if _should_route_to_knowledge(normalized) or _should_route_to_content_search(normalized):
        return False
    has_feed_hint = any(hint in normalized for hint in _FEED_DISCOVERY_HINTS)
    has_action_hint = any(hint in normalized for hint in _FEED_DISCOVERY_ACTION_HINTS)
    return has_feed_hint and has_action_hint


def _should_route_to_weekly_discovery_action(
    user_text: str,
    screen_context: AssistantScreenContext | None,
) -> bool:
    if screen_context is None or screen_context.screen_type != "weekly_discovery":
        return False
    normalized = f" {_normalize_turn_text(user_text)} "
    return any(marker in normalized for marker in (" add ", " subscribe ", " follow "))


def _should_route_to_content_search(user_text: str) -> bool:
    normalized = _normalize_turn_text(user_text)
    return any(hint in normalized for hint in _CONTENT_SEARCH_HINTS)
