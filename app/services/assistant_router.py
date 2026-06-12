"""Contextual assistant turns backed by server-side tools."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter

from fastapi.concurrency import run_in_threadpool
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models.openai import ReasoningEffort
from sqlalchemy.orm import Session, sessionmaker

from app.commands import ingest_content as ingest_content_command
from app.core.db import get_session_factory
from app.core.logging import get_logger
from app.core.model_defaults import DEEP_RESEARCH_MODEL_SPEC
from app.core.observability import build_log_extra
from app.core.settings import get_settings
from app.models.api.submissions import SubmitContentRequest
from app.models.contracts import ContentType
from app.models.db import ChatSession, Content, NewsItem
from app.models.db.users import User
from app.models.domain.chat_render import (
    AssistantFeedOption,
    AssistantFeedOptionsResult,
    ChatMessageRenderMetadata,
)
from app.models.internal.assistant import AssistantScreenContext
from app.repositories import read_status_repository
from app.repositories.search_repository import (
    search_content,
    search_news,
    search_subscription_feeds,
)
from app.services import knowledge as knowledge_service
from app.services.assistant_feed_finder import find_feed_options as find_feed_options_service
from app.services.chat_agent import (
    load_message_history,
    save_messages,
    update_message_completed,
    update_message_failed,
)
from app.services.chat_turn_runtime import (
    close_sandbox_session as _close_sandbox_session,
)
from app.services.chat_turn_runtime import get_or_create_cached_agent as _get_or_create_cached_agent
from app.services.chat_turn_runtime import (
    log_chat_usage as _log_chat_usage,
)
from app.services.chat_turn_runtime import (
    personal_library_unavailable_message as _personal_library_unavailable_message,
)
from app.services.chat_turn_runtime import (
    require_session_id as _require_session_id,
)
from app.services.chat_turn_runtime import (
    require_session_user_id as _require_session_user_id,
)
from app.services.chat_turn_runtime import (
    resolve_session_model as _resolve_session_model,
)
from app.services.exa_client import exa_search
from app.services.knowledge_search import search_knowledge as search_knowledge_hits
from app.services.langfuse_tracing import langfuse_trace_context
from app.services.llm_models import (
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
    build_pydantic_model,
    resolve_effective_api_key,
)
from app.services.news_feed import list_unread_visible_news_items
from app.services.personal_markdown_library import sync_personal_markdown_library_for_user
from app.services.prompt_library import load_prompt
from app.services.sandbox_runtime import (
    PersonalLibrarySandboxSession,
    SandboxRuntimeUnavailableError,
    create_personal_library_sandbox_session,
)
from app.utils.news_titles import resolve_news_display_title
from app.utils.title_utils import derive_chat_session_title, resolve_content_display_title

logger = get_logger(__name__)

KNOWLEDGE_SESSION_TYPE = "knowledge_chat"
LEGACY_KNOWLEDGE_SESSION_TYPES = {
    "assistant_quick",
    "article_brain",
    "topic",
}
ASSISTANT_SESSION_TYPES = {
    KNOWLEDGE_SESSION_TYPE,
    *LEGACY_KNOWLEDGE_SESSION_TYPES,
    "weekly_discovery",
}
ASSISTANT_ACTION_PICK_INTERESTING_UNREAD_NEWS = "pick_interesting_unread_news"

ASSISTANT_OPENAI_REASONING_EFFORT: ReasoningEffort = "low"

ASSISTANT_SYSTEM_PROMPT = load_prompt("chat/contextual_assistant#system")

SMALL_TALK_PHRASES = {
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
}
KNOWLEDGE_HINTS = (
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
CONTENT_SEARCH_HINTS = (
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
WEB_HINTS = (
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
SOURCE_RECOMMENDATION_HINTS = (
    "blogs",
    "blog",
    "publications",
    "publication",
    "newsletters",
    "newsletter",
    "sites",
    "sources",
)
FEED_DISCOVERY_HINTS = (
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
FEED_DISCOVERY_ACTION_HINTS = (
    "find",
    "search",
    "look up",
    "discover",
    "recommend",
    "subscribe",
)


@dataclass
class AssistantDeps:
    """Dependencies required to execute an assistant turn."""

    user_id: int
    session_id: int
    screen_context: AssistantScreenContext
    context_snapshot: str
    session_factory: sessionmaker[Session]
    sandbox_session: PersonalLibrarySandboxSession | None = None
    personal_library_error: str | None = None


def _build_submit_content_request(
    *,
    url: str,
    title: str | None = None,
    subscribe_to_feed: bool = False,
) -> SubmitContentRequest:
    return SubmitContentRequest(
        url=url,
        content_type=None,
        title=title,
        platform=None,
        instruction=None,
        crawl_links=False,
        subscribe_to_feed=subscribe_to_feed,
        share_and_chat=False,
        chat_initial_message=None,
        save_to_knowledge_and_mark_read=False,
    )


def _normalize_turn_text(user_text: str) -> str:
    """Normalize turn text for routing heuristics."""

    return " ".join(user_text.strip().lower().split())


def _is_small_talk(user_text: str) -> bool:
    """Detect short conversational turns that do not require tool calls."""

    normalized = _normalize_turn_text(user_text)
    if not normalized:
        return True
    if normalized in SMALL_TALK_PHRASES:
        return True
    return len(normalized.split()) <= 3 and normalized in {
        "hi there",
        "hello there",
        "thank you",
    }


def _should_route_to_knowledge(user_text: str) -> bool:
    """Detect turns that should prioritize saved-content lookup."""

    normalized = _normalize_turn_text(user_text)
    if " my " in f" {normalized} " and any(
        marker in normalized for marker in ("favorite", "saved", "bookmarked", "article", "podcast")
    ):
        return True
    return any(hint in normalized for hint in KNOWLEDGE_HINTS)


def _should_route_to_markdown_library(user_text: str) -> bool:
    """Detect turns that should prioritize the personal markdown library."""

    normalized = _normalize_turn_text(user_text)
    if not normalized:
        return False
    markdown_hints = ("markdown", "file path", "filepath", "source md", "summary md", ".md")
    file_hints = ("saved file", "library file", "raw markdown", "summary markdown")
    if any(hint in normalized for hint in markdown_hints + file_hints):
        return True
    return "path" in normalized and _should_route_to_knowledge(normalized)


def _should_route_to_web(user_text: str) -> bool:
    """Detect turns that should prioritize web search."""

    normalized = _normalize_turn_text(user_text)
    if _should_route_to_knowledge(normalized):
        return False
    if _should_route_to_feed_finder(normalized):
        return False
    if _is_small_talk(normalized):
        return False
    if any(hint in normalized for hint in WEB_HINTS):
        return True
    return "?" in user_text and normalized.startswith(
        ("what ", "who ", "when ", "where ", "why ", "how ")
    )


def _contains_explicit_url(user_text: str) -> bool:
    """Return True when the prompt already contains a direct URL."""

    normalized = _normalize_turn_text(user_text)
    return "http://" in normalized or "https://" in normalized


def _should_route_to_feed_finder(user_text: str) -> bool:
    """Detect turns asking for feeds, blogs, newsletters, or podcast sources."""

    normalized = _normalize_turn_text(user_text)
    if _contains_explicit_url(normalized):
        return False
    if _should_route_to_knowledge(normalized) or _should_route_to_content_search(normalized):
        return False
    has_feed_hint = any(hint in normalized for hint in FEED_DISCOVERY_HINTS)
    has_action_hint = any(hint in normalized for hint in FEED_DISCOVERY_ACTION_HINTS)
    return has_feed_hint and has_action_hint


def _build_turn_instructions(
    user_text: str,
    screen_context: AssistantScreenContext | None = None,
) -> str | None:
    """Build per-turn routing instructions for the assistant agent."""

    if (
        screen_context is not None
        and screen_context.assistant_action == ASSISTANT_ACTION_PICK_INTERESTING_UNREAD_NEWS
    ):
        return load_prompt("chat/contextual_assistant#turn_pick_interesting_unread_news")

    if _is_small_talk(user_text):
        return None

    if _should_route_to_feed_finder(user_text):
        return load_prompt("chat/contextual_assistant#turn_feed_finder")

    if _should_route_to_markdown_library(user_text):
        return load_prompt("chat/contextual_assistant#turn_markdown_library")

    if _should_route_to_content_search(user_text):
        return load_prompt("chat/contextual_assistant#turn_content_search")

    if _should_route_to_knowledge(user_text):
        return load_prompt("chat/contextual_assistant#turn_knowledge_search")

    if _should_route_to_web(user_text):
        normalized = _normalize_turn_text(user_text)
        if any(hint in normalized for hint in SOURCE_RECOMMENDATION_HINTS):
            return load_prompt("chat/contextual_assistant#turn_source_recommendation")
        return load_prompt("chat/contextual_assistant#turn_web_search")

    return load_prompt("chat/contextual_assistant#turn_default_tool_preference")


def _should_route_to_content_search(user_text: str) -> bool:
    """Detect turns that should use in-app content search."""

    normalized = _normalize_turn_text(user_text)
    return any(hint in normalized for hint in CONTENT_SEARCH_HINTS)


def _format_knowledge_hits(hits: Sequence[object], query: str) -> str:
    """Serialize saved-knowledge hits for the assistant tool."""

    if not hits:
        return f'No matching saved knowledge was found for "{query}".'

    lines = [f'Found {len(hits)} saved knowledge items for "{query}":']
    for idx, hit in enumerate(hits, start=1):
        title = getattr(hit, "title", "Untitled")
        source = getattr(hit, "source", None) or "unknown"
        url = getattr(hit, "url", "")
        content_type = getattr(hit, "content_type", "unknown")
        summary = (getattr(hit, "summary", None) or "").strip()
        transcript_excerpt = (getattr(hit, "transcript_excerpt", None) or "").strip()
        lines.append(
            f"{idx}. [{getattr(hit, 'content_id', '?')}] {title} | source={source} "
            f"| type={content_type} | url={url}"
        )
        if summary:
            lines.append(f"   summary: {summary[:320]}")
        if transcript_excerpt:
            lines.append(f"   transcript_excerpt: {transcript_excerpt[:220]}")
    return "\n".join(lines)


def _format_content_hits(
    *,
    query: str,
    content_rows: list[tuple[Content, object, object]],
    total_content_matches: int | None,
    news_item_rows: list[tuple[NewsItem, object]] | None = None,
    total_news_item_matches: int | None = None,
) -> str:
    """Serialize in-app content results for the assistant tool."""

    lines = [f'In-app content results for "{query}":']

    if news_item_rows:
        if total_news_item_matches is not None and total_news_item_matches > 0:
            if total_news_item_matches > len(news_item_rows):
                lines.append(
                    f"News Items ({total_news_item_matches} total matches, "
                    f"showing {len(news_item_rows)}):"
                )
            else:
                lines.append(f"News Items ({total_news_item_matches} total matches):")
        else:
            lines.append("Recent News Items:")

        for idx, (item, is_read) in enumerate(news_item_rows, start=1):
            title = resolve_news_display_title(
                item.raw_metadata,
                summary_text=item.summary_text,
                fallback=f"News item {item.id}",
            )
            source = item.source_label or item.platform or "unknown"
            url = (
                item.article_url
                or item.canonical_story_url
                or item.discussion_url
                or item.canonical_item_url
                or ""
            )
            lines.append(
                f"{idx}. [news:{item.id}] {title} | source={source} "
                f"| read={bool(is_read)} | url={url}"
            )
            if item.summary_text:
                lines.append(f"   summary: {item.summary_text[:240]}")
            if item.summary_key_points:
                key_points = ", ".join(
                    str(point).strip() for point in item.summary_key_points if str(point).strip()
                )
                if key_points:
                    lines.append(f"   key_points: {key_points[:240]}")
            raw_top_comment = (
                item.raw_metadata.get("top_comment")
                if isinstance(item.raw_metadata, dict)
                else None
            )
            if isinstance(raw_top_comment, dict):
                comment_author = (
                    str(raw_top_comment.get("author") or "unknown").strip() or "unknown"
                )
                comment_text = str(raw_top_comment.get("text") or "").strip()
                if comment_text:
                    lines.append(f"   top_comment: {comment_author}: {comment_text[:220]}")

    if content_rows:
        if total_content_matches is not None and total_content_matches > 0:
            if total_content_matches > len(content_rows):
                summary_line = (
                    f"Feed Content ({total_content_matches} total matches, "
                    f"showing {len(content_rows)}):"
                )
                lines.append(summary_line)
            else:
                lines.append(f"Feed Content ({total_content_matches} total matches):")
        else:
            lines.append("Recent Feed Content:")
        for idx, (content, is_read, is_saved_to_knowledge) in enumerate(content_rows, start=1):
            title = resolve_content_display_title(
                title=content.title,
                metadata=content.content_metadata,
                fallback="Untitled",
            )
            lines.append(
                f"{idx}. [{content.id}] {title} "
                f"| type={content.content_type} | source={content.source or 'unknown'} "
                f"| read={bool(is_read)} | saved_to_knowledge={bool(is_saved_to_knowledge)} "
                f"| url={content.url}"
            )
            summary = str(content.short_summary or "").strip()
            if summary:
                lines.append(f"   summary: {summary[:240]}")

    if len(lines) == 1:
        return f'No in-app content matched "{query}".'
    return "\n".join(lines)


def _truncate_tool_text(value: object, *, max_chars: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.strip().split())
    if not text:
        return None
    if len(text) <= max_chars:
        return text
    return f"{text[: max_chars - 3].rstrip()}..."


def _news_item_sort_timestamp_text(item: NewsItem) -> str | None:
    timestamp = item.published_at or item.processed_at or item.ingested_at or item.created_at
    if timestamp is None:
        return None
    return timestamp.isoformat()


def _news_item_top_comment(item: NewsItem) -> dict[str, str] | None:
    raw_metadata = item.raw_metadata if isinstance(item.raw_metadata, dict) else {}
    raw_top_comment = raw_metadata.get("top_comment")
    if not isinstance(raw_top_comment, dict):
        return None

    text = _truncate_tool_text(raw_top_comment.get("text"), max_chars=500)
    if not text:
        return None
    author = _truncate_tool_text(raw_top_comment.get("author"), max_chars=80) or "unknown"
    return {"author": author, "text": text}


def _serialize_unread_news_item(item: NewsItem) -> dict[str, object]:
    title = resolve_news_display_title(
        item.raw_metadata,
        summary_text=item.summary_text,
        fallback=f"News item {item.id}",
    )
    url = (
        item.article_url
        or item.canonical_story_url
        or item.discussion_url
        or item.canonical_item_url
        or ""
    )
    key_points = [
        point
        for point in (
            _truncate_tool_text(raw_point, max_chars=220)
            for raw_point in (item.summary_key_points or [])
        )
        if point
    ]
    payload: dict[str, object] = {
        "id": item.id,
        "title": title,
        "source": item.source_label or item.platform or "unknown",
        "platform": item.platform,
        "url": url,
        "summary": _truncate_tool_text(item.summary_text, max_chars=520),
        "key_points": key_points[:6],
        "sort_timestamp": _news_item_sort_timestamp_text(item),
    }
    top_comment = _news_item_top_comment(item)
    if top_comment is not None:
        payload["top_comment"] = top_comment
    return payload


def _build_unread_news_items_payload(
    db: Session,
    *,
    user_id: int,
    limit: int,
) -> dict[str, object]:
    normalized_limit = max(1, min(limit, 200))
    items, total_count = list_unread_visible_news_items(
        db,
        user_id=user_id,
        limit=normalized_limit,
    )
    return {
        "items": [_serialize_unread_news_item(item) for item in items],
        "total_count": total_count,
        "returned_count": len(items),
        "truncated": total_count > len(items),
        "limit": normalized_limit,
    }


def _get_or_create_agent(
    model_spec: str,
    api_key_override: str | None = None,
) -> Agent[AssistantDeps, str]:
    return _get_or_create_cached_agent(
        "contextual_assistant",
        model_spec,
        api_key_override,
        lambda: _create_assistant_agent(model_spec, api_key_override=api_key_override),
    )


def _create_assistant_agent(
    model_spec: str,
    api_key_override: str | None = None,
) -> Agent[AssistantDeps, str]:
    model, model_settings = build_pydantic_model(
        model_spec,
        api_key_override=api_key_override,
        openai_reasoning_effort=ASSISTANT_OPENAI_REASONING_EFFORT,
    )

    agent: Agent[AssistantDeps, str] = Agent(
        model,
        deps_type=AssistantDeps,
        output_type=str,
        system_prompt=ASSISTANT_SYSTEM_PROMPT,
        model_settings=model_settings,
    )

    @agent.tool
    def search_web(
        ctx: RunContext[AssistantDeps],
        query: str,
        limit: int = 5,
    ) -> str:
        """Search the web for current context or discovery."""
        normalized_limit = max(1, min(limit, 8))
        results = exa_search(
            query=query,
            num_results=normalized_limit,
            telemetry={
                "feature": "assistant_router",
                "operation": "assistant_router.search_web",
                "user_id": ctx.deps.user_id,
            },
        )
        if not results:
            return "No web results found."

        lines = [f'Found {len(results)} web results for "{query}":']
        for idx, result in enumerate(results[:normalized_limit], start=1):
            title = (result.title or "Untitled").strip()
            url = (result.url or "").strip()
            summary = (result.snippet or "").strip().replace("\n", " ")
            if len(summary) > 220:
                summary = f"{summary[:217]}..."
            lines.append(f"{idx}. {title} — {url}")
            if summary:
                lines.append(f"   {summary}")
        return "\n".join(lines)

    @agent.tool
    def find_feed_options(
        ctx: RunContext[AssistantDeps],
        query: str,
        limit: int = 5,
    ) -> dict[str, object]:
        """Find validated blog/newsletter/podcast feeds without subscribing yet."""

        result = find_feed_options_service(query=query, limit=limit, user_id=ctx.deps.user_id)
        return result.model_dump(mode="json")

    @agent.tool(name="search_knowledge")
    def search_knowledge_tool(
        ctx: RunContext[AssistantDeps],
        query: str,
        limit: int = 5,
    ) -> str:
        """Search knowledge-saved user content for the current user."""
        normalized_limit = max(1, min(limit, 10))
        with ctx.deps.session_factory() as db:
            hits = search_knowledge_hits(
                db=db,
                user_id=ctx.deps.user_id,
                query=query,
                limit=normalized_limit,
            )
        return _format_knowledge_hits(hits, query)

    @agent.tool(name="SearchMarkdownLibrary")
    def search_markdown_library(
        ctx: RunContext[AssistantDeps],
        query: str,
        limit: int = 20,
        glob: str = "*.md",
    ) -> str:
        """Search the user's sandbox-mounted personal markdown library."""
        sandbox_session = ctx.deps.sandbox_session
        if sandbox_session is None:
            return _personal_library_unavailable_message(ctx.deps.personal_library_error)

        normalized_limit = max(1, min(limit, 50))
        return sandbox_session.search_files(query=query, glob=glob, limit=normalized_limit)

    @agent.tool(name="ListMarkdownLibrary")
    def list_markdown_library(
        ctx: RunContext[AssistantDeps],
        subpath: str = "",
        limit: int = 200,
    ) -> str:
        """List markdown files in the user's sandbox-mounted personal library."""
        sandbox_session = ctx.deps.sandbox_session
        if sandbox_session is None:
            return _personal_library_unavailable_message(ctx.deps.personal_library_error)

        normalized_limit = max(1, min(limit, 500))
        return sandbox_session.list_files(subpath=subpath, limit=normalized_limit)

    @agent.tool(name="ReadMarkdownFile")
    def read_markdown_file(
        ctx: RunContext[AssistantDeps],
        relative_path: str,
        max_chars: int = 12_000,
    ) -> str:
        """Read one markdown file from the user's sandbox-mounted personal library."""
        sandbox_session = ctx.deps.sandbox_session
        if sandbox_session is None:
            return _personal_library_unavailable_message(ctx.deps.personal_library_error)

        normalized_max_chars = max(500, min(max_chars, 40_000))
        return sandbox_session.read_file(
            relative_path=relative_path,
            max_chars=normalized_max_chars,
        )

    @agent.tool(name="search_subscription_feeds")
    def search_subscription_feeds_tool(
        ctx: RunContext[AssistantDeps],
        query: str,
        limit: int = 5,
    ) -> str:
        """Search content from sources the user already follows."""
        normalized_limit = max(1, min(limit, 10))
        normalized_query = query.strip()
        with ctx.deps.session_factory() as db:
            content_rows, total_content_matches = search_subscription_feeds(
                db,
                user_id=ctx.deps.user_id,
                query_text=normalized_query,
                limit=normalized_limit,
            )

        return _format_content_hits(
            query=query,
            content_rows=content_rows,
            total_content_matches=total_content_matches or 0,
        )

    @agent.tool(name="search_content")
    def search_content_tool(
        ctx: RunContext[AssistantDeps],
        query: str,
        limit: int = 5,
    ) -> str:
        """Search user-visible feed content excluding news-item rows."""
        normalized_limit = max(1, min(limit, 10))
        normalized_query = query.strip()
        with ctx.deps.session_factory() as db:
            content_rows, total_content_matches = search_content(
                db,
                user_id=ctx.deps.user_id,
                query_text=normalized_query,
                limit=normalized_limit,
            )

        return _format_content_hits(
            query=query,
            content_rows=content_rows,
            total_content_matches=total_content_matches,
        )

    @agent.tool(name="search_news")
    def search_news_tool(
        ctx: RunContext[AssistantDeps],
        query: str,
        limit: int = 5,
    ) -> str:
        """Search user-visible news items."""
        normalized_limit = max(1, min(limit, 10))
        normalized_query = query.strip()
        with ctx.deps.session_factory() as db:
            news_item_rows, total_news_item_matches = search_news(
                db,
                user_id=ctx.deps.user_id,
                query_text=normalized_query,
                limit=normalized_limit,
            )

        return _format_content_hits(
            query=query,
            content_rows=[],
            total_content_matches=0,
            news_item_rows=news_item_rows,
            total_news_item_matches=total_news_item_matches,
        )

    @agent.tool(name="list_unread_news_items")
    def list_unread_news_items_tool(
        ctx: RunContext[AssistantDeps],
        limit: int = 100,
    ) -> dict[str, object]:
        """List unread visible fast-news items for the current user."""
        with ctx.deps.session_factory() as db:
            return _build_unread_news_items_payload(
                db,
                user_id=ctx.deps.user_id,
                limit=limit,
            )

    @agent.tool
    def add_item_to_feed(
        ctx: RunContext[AssistantDeps],
        url: str,
        title: str | None = None,
    ) -> str:
        """Submit a single URL into the user's feed."""
        with ctx.deps.session_factory() as db:
            user = db.query(User).filter(User.id == ctx.deps.user_id).first()
            if user is None:
                return "Unable to add to feed: user not found."
            response = ingest_content_command.execute(
                db,
                payload=_build_submit_content_request(url=url, title=title),
                current_user=user,
                submitted_via="assistant",
            ).response
        if response.already_exists:
            return f"That item is already in the feed (content_id={response.content_id})."
        return f"Added the item to the feed (content_id={response.content_id})."

    @agent.tool
    def subscribe_to_feed(
        ctx: RunContext[AssistantDeps],
        url: str,
        title: str | None = None,
    ) -> str:
        """Detect and subscribe to a feed from the provided URL."""
        with ctx.deps.session_factory() as db:
            user = db.query(User).filter(User.id == ctx.deps.user_id).first()
            if user is None:
                return "Unable to subscribe: user not found."
            response = ingest_content_command.execute(
                db,
                payload=_build_submit_content_request(url=url, title=title, subscribe_to_feed=True),
                current_user=user,
                submitted_via="assistant",
            ).response
        return response.message

    @agent.tool
    def save_to_knowledge(
        ctx: RunContext[AssistantDeps],
        content_id: int,
    ) -> str:
        """Save a content item to the user's knowledge library."""
        with ctx.deps.session_factory() as db:
            try:
                knowledge_service.save_to_knowledge(db, content_id, ctx.deps.user_id)
            except Exception:
                db.rollback()
                logger.exception(
                    "Assistant tool failed to save content to knowledge",
                    extra={
                        "component": "assistant_router",
                        "operation": "save_to_knowledge",
                        "item_id": str(content_id),
                    },
                )
                return f"Could not save content {content_id} to knowledge."
        return f"Saved content {content_id} to knowledge."

    @agent.tool
    def remove_from_knowledge(
        ctx: RunContext[AssistantDeps],
        content_id: int,
    ) -> str:
        """Remove a content item from the user's knowledge library."""
        with ctx.deps.session_factory() as db:
            try:
                removed = knowledge_service.remove_from_knowledge(
                    db,
                    content_id,
                    ctx.deps.user_id,
                )
            except Exception:
                db.rollback()
                logger.exception(
                    "Assistant tool failed to remove content from knowledge",
                    extra={
                        "component": "assistant_router",
                        "operation": "remove_from_knowledge",
                        "item_id": str(content_id),
                    },
                )
                return f"Could not remove content {content_id} from knowledge."
        if not removed:
            return f"Content {content_id} was not saved to knowledge."
        return f"Removed content {content_id} from knowledge."

    @agent.tool
    def mark_content_read(
        ctx: RunContext[AssistantDeps],
        content_id: int,
    ) -> str:
        """Mark a content item as read."""
        with ctx.deps.session_factory() as db:
            result = read_status_repository.mark_content_as_read(db, content_id, ctx.deps.user_id)
        if result is None:
            return f"Could not mark content {content_id} as read."
        return f"Marked content {content_id} as read."

    @agent.tool
    def mark_content_unread(
        ctx: RunContext[AssistantDeps],
        content_id: int,
    ) -> str:
        """Mark a content item as unread."""
        with ctx.deps.session_factory() as db:
            removed = read_status_repository.mark_content_as_unread(
                db,
                content_id,
                ctx.deps.user_id,
            )
        if not removed:
            return f"Content {content_id} was already unread."
        return f"Marked content {content_id} as unread."

    @agent.tool
    def convert_news_to_article_tool(
        ctx: RunContext[AssistantDeps],
        content_id: int,
    ) -> str:
        """Convert a news item to an article entry when possible."""
        with ctx.deps.session_factory() as db:
            content = db.query(Content).filter(Content.id == content_id).first()
            if content is None:
                return f"Content {content_id} was not found."
            if content.content_type != ContentType.NEWS.value:
                return f"Content {content_id} is not a news item."
            article_meta = (content.content_metadata or {}).get("article", {})
            article_url = str(article_meta.get("url") or content.url or "").strip()
            if not article_url:
                return f"Content {content_id} has no article URL to convert."
            user = db.query(User).filter(User.id == ctx.deps.user_id).first()
            if user is None:
                return "Unable to convert article: user not found."
            response = ingest_content_command.execute(
                db,
                payload=_build_submit_content_request(
                    url=article_url,
                    title=(
                        article_meta.get("title")
                        if isinstance(article_meta.get("title"), str)
                        else None
                    ),
                ),
                current_user=user,
                submitted_via="assistant",
            ).response
        if response.already_exists:
            return f"Article already exists in the feed (content_id={response.content_id})."
        return f"Queued article extraction (content_id={response.content_id})."

    @agent.tool
    def start_deep_research_handoff(
        ctx: RunContext[AssistantDeps],
        question: str,
    ) -> str:
        """Create a deep research session handoff."""
        with ctx.deps.session_factory() as db:
            session = ChatSession(
                user_id=ctx.deps.user_id,
                content_id=ctx.deps.screen_context.content_id,
                title="Deep Research",
                session_type="deep_research",
                topic=question[:500],
                llm_provider="deep_research",
                llm_model=DEEP_RESEARCH_MODEL_SPEC,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            db.add(session)
            db.commit()
            db.refresh(session)
        return (
            f"Started a deep research handoff in session {session.id}. "
            "Open the full chat thread to continue there."
        )

    return agent


def _parse_feed_options_tool_return(content: object) -> list[AssistantFeedOption]:
    """Parse one `find_feed_options` tool return payload into validated options."""

    payload: object = content
    if isinstance(content, str):
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return []

    if not isinstance(payload, dict):
        return []

    try:
        result = AssistantFeedOptionsResult.model_validate(payload)
    except Exception:  # noqa: BLE001
        return []
    return result.options


def _extract_render_metadata(messages: list[ModelMessage]) -> ChatMessageRenderMetadata | None:
    """Extract structured assistant render metadata from tool return parts."""

    feed_options: list[AssistantFeedOption] = []
    seen_option_ids: set[str] = set()

    for model_message in messages:
        if not isinstance(model_message, ModelRequest):
            continue
        for part in model_message.parts:
            if not isinstance(part, ToolReturnPart):
                continue
            if part.tool_name != "find_feed_options":
                continue
            for option in _parse_feed_options_tool_return(part.content):
                if option.id in seen_option_ids:
                    continue
                seen_option_ids.add(option.id)
                feed_options.append(option)

    if not feed_options:
        return None
    return ChatMessageRenderMetadata(feed_options=feed_options)


def _extract_transcript_excerpt(content: Content, max_length: int = 420) -> str | None:
    """Extract a compact transcript/content excerpt for session grounding."""

    metadata = content.content_metadata if isinstance(content.content_metadata, dict) else {}
    candidates = [
        metadata.get("excerpt"),
        metadata.get("transcript"),
        metadata.get("content"),
    ]
    summary = metadata.get("summary")
    if isinstance(summary, dict):
        candidates.append(summary.get("full_markdown"))

    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        excerpt = " ".join(candidate.strip().split())
        if not excerpt:
            continue
        if len(excerpt) <= max_length:
            return excerpt
        return f"{excerpt[: max_length - 3].rstrip()}..."
    return None


def _news_item_context_label(item: NewsItem) -> str:
    """Return a compact display label for a news item context row."""
    return resolve_news_display_title(
        item.raw_metadata,
        summary_text=item.summary_text,
        fallback="Untitled News Item",
    )


def _news_item_source_label(item: NewsItem) -> str | None:
    candidates = [item.source_label, item.article_domain, item.platform, item.source_type]
    for candidate in candidates:
        if candidate and candidate.strip():
            return candidate.strip()
    return None


def _visible_news_items_by_id(
    db: Session,
    *,
    user_id: int,
    news_item_ids: list[int],
) -> dict[int, NewsItem]:
    """Load visible news items in the same namespace as news_items.id."""
    if not news_item_ids:
        return {}

    from app.services.news_feed import build_visible_news_item_filter

    rows = (
        db.query(NewsItem)
        .filter(NewsItem.id.in_(news_item_ids))
        .filter(build_visible_news_item_filter(db, user_id=user_id))
        .all()
    )
    return {item.id: item for item in rows if item.id is not None}


def build_screen_context_snapshot(
    db: Session,
    *,
    user_id: int,
    screen_context: AssistantScreenContext,
) -> str:
    """Build a compact context snapshot for the assistant."""
    lines = [f"Screen Type: {screen_context.screen_type}"]
    if screen_context.screen_title:
        lines.append(f"Screen Title: {screen_context.screen_title}")
    if screen_context.selected_topic:
        lines.append(f"Selected Topic: {screen_context.selected_topic}")
    if screen_context.query:
        lines.append(f"Query: {screen_context.query}")
    if screen_context.note:
        lines.append(f"Client Note: {screen_context.note}")
    if screen_context.assistant_action:
        lines.append(f"Assistant Action: {screen_context.assistant_action}")

    candidate_ids: list[int] = []
    if screen_context.content_id:
        candidate_ids.append(screen_context.content_id)
    for content_id in screen_context.visible_content_ids:
        if content_id not in candidate_ids:
            candidate_ids.append(content_id)
    if candidate_ids:
        rows = db.query(Content).filter(Content.id.in_(candidate_ids)).all()
        content_by_id = {row.id: row for row in rows}
        lines.append("Visible Content:")
        for content_id in candidate_ids:
            content = content_by_id.get(content_id)
            if content is None:
                continue
            label = resolve_content_display_title(
                title=content.title,
                metadata=content.content_metadata,
                fallback="Untitled",
            )
            source = f" ({content.source})" if content.source else ""
            lines.append(f"- [{content_id}] {label}{source} — {content.url}")
            short_summary = content.short_summary
            if short_summary:
                lines.append(f"  Short Summary: {short_summary}")
            transcript_excerpt = _extract_transcript_excerpt(content)
            if transcript_excerpt:
                lines.append(f"  Transcript Excerpt: {transcript_excerpt}")

    news_item_ids: list[int] = []
    if screen_context.news_item_id:
        news_item_ids.append(screen_context.news_item_id)
    for news_item_id in screen_context.visible_news_item_ids:
        if news_item_id not in news_item_ids:
            news_item_ids.append(news_item_id)
    if news_item_ids:
        news_items_by_id = _visible_news_items_by_id(
            db,
            user_id=user_id,
            news_item_ids=news_item_ids,
        )
        lines.append("Visible News Items:")
        for news_item_id in news_item_ids:
            item = news_items_by_id.get(news_item_id)
            if item is None:
                continue

            label = _news_item_context_label(item)
            news_source = _news_item_source_label(item)
            source_suffix = f" ({news_source})" if news_source else ""
            lines.append(f"- [news:{news_item_id}] {label}{source_suffix}")
            if item.article_url:
                lines.append(f"  Article URL: {item.article_url}")
            if item.canonical_story_url and item.canonical_story_url != item.article_url:
                lines.append(f"  Story URL: {item.canonical_story_url}")
            if item.discussion_url:
                lines.append(f"  Discussion URL: {item.discussion_url}")
            if item.summary_text:
                lines.append(f"  Summary: {item.summary_text}")
            key_points = item.summary_key_points or []
            if key_points:
                rendered_points = "; ".join(str(point) for point in key_points[:5])
                lines.append(f"  Key Points: {rendered_points}")

    return "\n".join(lines)


def create_assistant_session(
    db: Session,
    *,
    user_id: int,
    context_snapshot: str,
    screen_context: AssistantScreenContext,
    initial_message: str | None = None,
) -> ChatSession:
    """Create a new assistant session."""
    title = screen_context.screen_title or "Knowledge Chat"
    if screen_context.content_id:
        content = db.query(Content).filter(Content.id == screen_context.content_id).first()
        if content and content.title:
            title = content.title
    elif screen_context.news_item_id:
        news_item = _visible_news_items_by_id(
            db,
            user_id=user_id,
            news_item_ids=[screen_context.news_item_id],
        ).get(screen_context.news_item_id)
        if news_item is not None:
            title = _news_item_context_label(news_item)
    elif derived_title := derive_chat_session_title(initial_message):
        title = derived_title
    elif screen_context.selected_topic:
        title = screen_context.selected_topic

    session = ChatSession(
        user_id=user_id,
        content_id=screen_context.content_id,
        news_item_id=screen_context.news_item_id,
        title=title[:500],
        session_type=KNOWLEDGE_SESSION_TYPE,
        topic=screen_context.selected_topic,
        context_snapshot=context_snapshot,
        llm_provider=DEFAULT_PROVIDER,
        llm_model=DEFAULT_MODEL,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def run_assistant_turn_sync(
    model_spec: str,
    user_prompt: str,
    deps: AssistantDeps,
    history: list[ModelMessage],
    *,
    provider_api_key: str | None = None,
):
    """Run one assistant turn synchronously and return the raw agent result."""
    agent = _get_or_create_agent(model_spec, api_key_override=provider_api_key)
    turn_instructions = _build_turn_instructions(user_prompt, deps.screen_context)
    prompt_sections: list[str] = []
    if turn_instructions:
        prompt_sections.append(f"Turn instructions:\n{turn_instructions}")
    prompt_sections.append(f"User request:\n{user_prompt.strip()}")
    prompt_sections.append(f"Current context:\n{deps.context_snapshot}")
    prompt = "\n\n".join(prompt_sections)
    with langfuse_trace_context(
        trace_name="assistant.turn.async",
        user_id=deps.user_id,
        session_id=deps.session_id,
        metadata={"model_spec": model_spec, "screen_type": deps.screen_context.screen_type},
        tags=["assistant", "chat"],
    ):
        return agent.run_sync(prompt, deps=deps, message_history=history)


def _build_assistant_personal_library_runtime(
    *,
    db: Session,
    user_id: int,
) -> tuple[PersonalLibrarySandboxSession | None, str | None]:
    """Synchronize and hydrate the personal markdown library for assistant turns."""
    settings = get_settings()
    if not settings.personal_markdown_enabled or settings.chat_sandbox_provider == "disabled":
        return None, None

    try:
        sync_personal_markdown_library_for_user(db, user_id=user_id)
        sandbox_session = create_personal_library_sandbox_session(user_id=user_id)
        return sandbox_session, None
    except SandboxRuntimeUnavailableError as exc:
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to prepare assistant personal markdown library",
            extra=build_log_extra(
                component="assistant_turn",
                operation="build_personal_library_runtime",
                event_name="assistant.turn.personal_library",
                status="degraded",
                user_id=user_id,
                context_data={"failure_class": type(exc).__name__},
            ),
        )
        return None, str(exc)


async def process_assistant_turn_async(
    session_id: int,
    message_id: int,
    user_prompt: str,
    *,
    screen_context: AssistantScreenContext,
    source: str = "assistant",
) -> None:
    """Process an assistant turn asynchronously."""
    total_start = perf_counter()
    SessionLocal = get_session_factory()
    db: Session | None = SessionLocal()
    logger.info(
        "Assistant turn started",
        extra=build_log_extra(
            component="assistant_turn",
            operation="process_turn",
            event_name="assistant.turn",
            status="started",
            session_id=session_id,
            message_id=message_id,
            source=source,
            context_data={
                "screen_type": screen_context.screen_type,
                "prompt_chars": len(user_prompt),
            },
        ),
    )
    deps: AssistantDeps | None = None
    try:
        if db is None:
            raise RuntimeError("Database session was not initialized")
        session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
        if session is None:
            logger.error("Assistant session %s not found", session_id)
            return
        session_row_id = _require_session_id(session)
        session_user_id = _require_session_user_id(session)
        model_spec = _resolve_session_model(session)

        history_start = perf_counter()
        history = load_message_history(
            db,
            session_row_id,
            exclude_message_id=message_id,
            completed_only=True,
        )
        history_ms = (perf_counter() - history_start) * 1000
        logger.info(
            "Assistant history loaded",
            extra=build_log_extra(
                component="assistant_turn",
                operation="load_history",
                event_name="assistant.turn.history_loaded",
                status="completed",
                duration_ms=history_ms,
                session_id=session_row_id,
                message_id=message_id,
                user_id=session_user_id,
                content_id=session.content_id,
                context_data={"history_count": len(history)},
            ),
        )

        context_start = perf_counter()
        context_snapshot = session.context_snapshot or build_screen_context_snapshot(
            db, user_id=session_user_id, screen_context=screen_context
        )
        context_ms = (perf_counter() - context_start) * 1000
        sandbox_session, personal_library_error = _build_assistant_personal_library_runtime(
            db=db,
            user_id=session_user_id,
        )
        deps = AssistantDeps(
            user_id=session_user_id,
            session_id=session_row_id,
            screen_context=screen_context,
            context_snapshot=context_snapshot,
            session_factory=get_session_factory(),
            sandbox_session=sandbox_session,
            personal_library_error=personal_library_error,
        )
        logger.info(
            "Assistant context built",
            extra=build_log_extra(
                component="assistant_turn",
                operation="build_context",
                event_name="assistant.turn.context_built",
                status="completed",
                duration_ms=context_ms,
                session_id=session_row_id,
                message_id=message_id,
                user_id=session_user_id,
                content_id=session.content_id,
                context_data={
                    "screen_type": screen_context.screen_type,
                    "context_chars": len(context_snapshot or ""),
                },
            ),
        )
        provider_api_key = resolve_effective_api_key(
            db=db,
            user_id=session_user_id,
            model_spec=model_spec,
        )
        db.close()
        db = None
        logger.info(
            "Assistant LLM call started",
            extra=build_log_extra(
                component="assistant_turn",
                operation="llm_call",
                event_name="assistant.turn.llm_started",
                status="started",
                session_id=session_row_id,
                message_id=message_id,
                user_id=session_user_id,
                content_id=session.content_id,
                source=source,
                context_data={
                    "model": model_spec,
                    "screen_type": screen_context.screen_type,
                },
            ),
        )
        agent_start = perf_counter()
        result = await run_in_threadpool(
            run_assistant_turn_sync,
            model_spec,
            user_prompt,
            deps,
            history,
            provider_api_key=provider_api_key,
        )
        agent_ms = (perf_counter() - agent_start) * 1000
        render_metadata = _extract_render_metadata(result.new_messages())
        _log_chat_usage(result, session, session_id, message_id, source)
        with SessionLocal() as persist_db:
            update_message_completed(
                persist_db,
                message_id,
                result.new_messages(),
                display_user_prompt=user_prompt,
                render_metadata=render_metadata,
            )
            session_to_update = (
                persist_db.query(ChatSession).filter(ChatSession.id == session_id).first()
            )
            if session_to_update is None:
                raise ValueError(f"Assistant session {session_id} not found")
            session_to_update.last_message_at = datetime.now(UTC)
            session_to_update.updated_at = datetime.now(UTC)
            persist_db.commit()
        tool_calls = getattr(result, "tool_calls", []) or []
        tool_names = [
            getattr(call, "name", None)
            or getattr(call, "tool_name", None)
            or getattr(call, "function_name", None)
            for call in tool_calls
        ]
        logger.info(
            "Assistant turn completed",
            extra=build_log_extra(
                component="assistant_turn",
                operation="process_turn",
                event_name="assistant.turn",
                status="completed",
                duration_ms=(perf_counter() - total_start) * 1000,
                session_id=session_id,
                message_id=message_id,
                user_id=session_user_id,
                content_id=session.content_id,
                source=source,
                context_data={
                    "model": model_spec,
                    "tool_names": tool_names,
                    "tool_count": len([name for name in tool_names if name]),
                    "agent_ms": round(agent_ms, 2),
                },
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Assistant turn failed",
            extra=build_log_extra(
                component="assistant_turn",
                operation="process_turn",
                event_name="assistant.turn",
                status="failed",
                duration_ms=(perf_counter() - total_start) * 1000,
                session_id=session_id,
                message_id=message_id,
                source=source,
                context_data={"failure_class": type(exc).__name__},
            ),
        )
        if db is not None:
            db.rollback()
            update_message_failed(db, message_id, str(exc))
        else:
            with SessionLocal() as fail_db:
                update_message_failed(fail_db, message_id, str(exc))
    finally:
        _close_sandbox_session(deps.sandbox_session if deps is not None else None)
        if db is not None:
            db.close()


def seed_assistant_message(
    db: Session,
    *,
    session_id: int,
    assistant_text: str,
) -> None:
    """Persist an assistant-only seed message into a chat session."""
    from pydantic_ai.messages import ModelResponse, TextPart

    save_messages(
        db,
        session_id,
        [ModelResponse(parts=[TextPart(content=assistant_text)])],
    )
