"""Screen-aware assistant turns backed by server-side tools."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter

from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, field_validator
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from sqlalchemy import Text, cast, or_
from sqlalchemy.orm import Session, sessionmaker

from app.core.db import get_session_factory
from app.core.logging import get_logger
from app.infrastructure.db.search.base import get_search_backend
from app.models.chat_message_metadata import (
    AssistantFeedOption,
    AssistantFeedOptionsResult,
    ChatMessageRenderMetadata,
)
from app.models.content_submission import SubmitContentRequest
from app.models.metadata import ContentType
from app.models.schema import (
    ChatSession,
    Content,
    DailyNewsDigest,
)
from app.models.user import User
from app.repositories import favorites_repository, read_status_repository
from app.repositories.content_feed_query import build_user_feed_query
from app.services.admin_conversational_agent import search_knowledge
from app.services.assistant_feed_finder import find_feed_options as find_feed_options_service
from app.services.chat_agent import (
    _log_chat_usage,
    load_message_history,
    save_messages,
    update_message_completed,
    update_message_failed,
)
from app.services.content_submission import submit_user_content
from app.services.exa_client import exa_search
from app.services.langfuse_tracing import langfuse_trace_context
from app.services.llm_models import (
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
    resolve_effective_api_key,
)

logger = get_logger(__name__)

KNOWLEDGE_SESSION_TYPE = "knowledge_chat"
LEGACY_KNOWLEDGE_SESSION_TYPES = {
    "assistant_quick",
    "article_brain",
    "topic",
    "voice_live",
}
ASSISTANT_SESSION_TYPES = {
    KNOWLEDGE_SESSION_TYPE,
    *LEGACY_KNOWLEDGE_SESSION_TYPES,
    "weekly_discovery",
}
MAX_VISIBLE_CONTENT_IDS = 12
_agents: dict[tuple[str, str], Agent[AssistantDeps, str]] = {}

ASSISTANT_SYSTEM_PROMPT = (
    "You are Newsly's contextual assistant. "
    "You help users understand what they are looking at, discover new content, "
    "and take actions inside the app. "
    "Be concise, action-oriented, and explicit when you changed the user's state.\n\n"
    "Rules:\n"
    "- Use tools when they can directly answer or complete the request.\n"
    "- If the user asks about their saved or favorited content, call SearchKnowledge first.\n"
    "- If the user asks about their in-app feed, inbox, or daily digests, "
    "call SearchContent first.\n"
    "- For broad current-events or recent factual questions, call search_web first.\n"
    "- For blog, newsletter, RSS, or podcast source-finding requests, call "
    "find_feed_options first and let the user choose from the returned options.\n"
    "- For source recommendations, prefer high-signal, widely recognized outlets unless "
    "the user explicitly asks for niche or emerging ones.\n"
    "- Mutations are allowed, but do not subscribe to a discovered feed in the same turn that "
    "you searched for options unless the user provided a direct URL.\n"
    "- Keep tool narration compact. State the outcome, not a verbose audit log.\n"
    "- When a request would take a long time, create the handoff and tell the user where "
    "to continue.\n"
    "- When screen context is provided, treat it as the default frame of reference.\n"
    "- Do not use markdown tables in chat responses. "
    "For comparisons or lists, use headings, bullets, "
    "or one-item-per-line formatting that reads well on mobile.\n"
)

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
    "daily digest",
    "daily digests",
    "digest",
    "digests",
    "in my feed",
    "in my inbox",
    "from my feed",
    "from my inbox",
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


class AssistantScreenContext(BaseModel):
    """Compact client-provided screen context for an assistant turn."""

    screen_type: str = Field(default="unknown", max_length=64)
    screen_title: str | None = Field(default=None, max_length=200)
    content_id: int | None = Field(default=None, ge=1)
    visible_content_ids: list[int] = Field(
        default_factory=list,
        max_length=MAX_VISIBLE_CONTENT_IDS,
    )
    selected_topic: str | None = Field(default=None, max_length=200)
    query: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=500)

    @field_validator("visible_content_ids", mode="before")
    @classmethod
    def truncate_visible_content_ids(cls, value: object) -> object:
        """Bound visible content IDs before validation rejects oversized payloads."""

        if isinstance(value, list):
            return value[:MAX_VISIBLE_CONTENT_IDS]
        return value


@dataclass
class AssistantDeps:
    """Dependencies required to execute an assistant turn."""

    user_id: int
    session_id: int
    screen_context: AssistantScreenContext
    context_snapshot: str
    session_factory: sessionmaker[Session]


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


def _build_turn_instructions(user_text: str) -> str | None:
    """Build per-turn routing instructions for the assistant agent."""

    if _is_small_talk(user_text):
        return None

    if _should_route_to_feed_finder(user_text):
        return (
            "For this turn, call find_feed_options before answering. "
            "Summarize the best validated matches you found and tell the user they can tap the "
            "feed options below to subscribe. Do not call subscribe_to_feed in this turn unless "
            "the user supplied a specific URL."
        )

    if _should_route_to_knowledge(user_text):
        return (
            "For this turn, call SearchKnowledge before answering. "
            "Use a concise query derived from the user's request. "
            "If SearchKnowledge has no relevant matches, say so plainly instead of guessing."
        )

    if _should_route_to_web(user_text):
        normalized = _normalize_turn_text(user_text)
        if any(hint in normalized for hint in SOURCE_RECOMMENDATION_HINTS):
            return (
                "For this turn, call search_web before answering. "
                "When recommending blogs, publications, or sources, prefer high-signal, "
                "widely recognized outlets over obscure results unless the user asks for "
                "niche options."
            )
        return (
            "For this turn, call search_web before answering. "
            "Use a concise web query derived from the user's request. "
            "If the request is actually about saved or favorited user content, "
            "call SearchKnowledge first."
        )

    return (
        "For this turn, if the user is asking for specific factual information, "
        "prefer tools over assumptions. Use SearchKnowledge for saved/favorited context "
        "and search_web for current external facts."
    )


def _should_route_to_content_search(user_text: str) -> bool:
    """Detect turns that should use in-app content search."""

    normalized = _normalize_turn_text(user_text)
    if _should_route_to_knowledge(normalized):
        return False
    return any(hint in normalized for hint in CONTENT_SEARCH_HINTS)


def _screen_context_mentions_daily_digests(screen_context: AssistantScreenContext) -> bool:
    """Return True when the current screen context is about daily digests."""

    haystacks = [
        screen_context.screen_type,
        screen_context.screen_title or "",
        screen_context.note or "",
    ]
    return any("digest" in value.lower() for value in haystacks if value)


def _build_screen_aware_turn_instructions(
    user_text: str,
    screen_context: AssistantScreenContext,
) -> str | None:
    """Build per-turn instructions with screen-context overrides."""

    if _should_route_to_content_search(user_text):
        return (
            "For this turn, you must call SearchContent before answering. "
            "Use it for in-app feed items, inbox content, and daily digests. "
            "Only call search_web if SearchContent is insufficient."
        )

    if _screen_context_mentions_daily_digests(screen_context):
        normalized = _normalize_turn_text(user_text)
        if any(marker in normalized for marker in ("digest", "digests", "summarize", "summary")):
            return (
                "For this turn, you must call SearchContent before answering. "
                "Use it to summarize the user's recent daily digests. "
                "Do not call search_web unless SearchContent is insufficient."
            )

    return _build_turn_instructions(user_text)


def _format_knowledge_hits(hits: list[object], query: str) -> str:
    """Serialize favorited-content hits for the assistant tool."""

    if not hits:
        return f'No matching favorited content was found for "{query}".'

    lines = [f'Found {len(hits)} favorited items for "{query}":']
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
    digest_rows: list[DailyNewsDigest],
) -> str:
    """Serialize mixed in-app content results for the assistant tool."""

    lines = [f'In-app content results for "{query}":']

    if content_rows:
        lines.append("Feed Content:")
        for idx, (content, is_read, is_favorited) in enumerate(content_rows, start=1):
            lines.append(
                f"{idx}. [{content.id}] {(content.title or 'Untitled').strip()} "
                f"| type={content.content_type} | source={content.source or 'unknown'} "
                f"| read={bool(is_read)} | favorited={bool(is_favorited)} | url={content.url}"
            )
            summary = str((content.content_metadata or {}).get("summary") or "").strip()
            if summary:
                lines.append(f"   summary: {summary[:240]}")

    if digest_rows:
        lines.append("Daily Digests:")
        for digest in digest_rows:
            lines.append(f"{digest.local_date.isoformat()} | {digest.title}")
            summary = (digest.summary or "").strip()
            if summary:
                lines.append(f"   summary: {summary[:240]}")
            key_points = digest.key_points if isinstance(digest.key_points, list) else []
            for point in key_points[:3]:
                point_text = str(point).strip()
                if point_text:
                    lines.append(f"   point: {point_text[:200]}")

    if len(lines) == 1:
        return f'No in-app content matched "{query}".'
    return "\n".join(lines)


def _build_agent_cache_key(model_spec: str, api_key_override: str | None) -> tuple[str, str]:
    if not api_key_override:
        return model_spec, ""
    return model_spec, hashlib.sha256(api_key_override.encode("utf-8")).hexdigest()


def _get_or_create_agent(
    model_spec: str,
    api_key_override: str | None = None,
) -> Agent[AssistantDeps, str]:
    cache_key = _build_agent_cache_key(model_spec, api_key_override)
    existing = _agents.get(cache_key)
    if existing is not None:
        return existing

    agent: Agent[AssistantDeps, str] = Agent(
        model_spec,
        deps_type=AssistantDeps,
        output_type=str,
        system_prompt=ASSISTANT_SYSTEM_PROMPT,
    )

    @agent.tool
    def search_web(
        ctx: RunContext[AssistantDeps],
        query: str,
        limit: int = 5,
    ) -> str:
        """Search the web for current context or discovery."""
        normalized_limit = max(1, min(limit, 8))
        results = exa_search(query=query, num_results=normalized_limit)
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

        del ctx
        result = find_feed_options_service(query=query, limit=limit)
        return result.model_dump(mode="json")

    @agent.tool(name="SearchKnowledge")
    def search_saved_content(
        ctx: RunContext[AssistantDeps],
        query: str,
        limit: int = 5,
    ) -> str:
        """Search favorited user content for the current user."""
        normalized_limit = max(1, min(limit, 10))
        with ctx.deps.session_factory() as db:
            hits = search_knowledge(
                db=db,
                user_id=ctx.deps.user_id,
                query=query,
                limit=normalized_limit,
            )
        return _format_knowledge_hits(hits, query)

    @agent.tool(name="SearchContent")
    def search_user_content(
        ctx: RunContext[AssistantDeps],
        query: str,
        limit: int = 5,
    ) -> str:
        """Search user-visible app content and recent daily digests."""
        normalized_limit = max(1, min(limit, 10))
        normalized_query = query.strip()
        with ctx.deps.session_factory() as db:
            feed_query = build_user_feed_query(db, ctx.deps.user_id, mode="inbox")
            search_backend = get_search_backend(db)
            content_query = search_backend.apply_search(feed_query, normalized_query)
            content_rows = (
                content_query
                .order_by(Content.created_at.desc(), Content.id.desc())
                .limit(normalized_limit)
                .all()
            )
            if not content_rows:
                content_rows = (
                    feed_query
                    .order_by(Content.created_at.desc(), Content.id.desc())
                    .limit(normalized_limit)
                    .all()
                )

            digest_query = db.query(DailyNewsDigest).filter(
                DailyNewsDigest.user_id == ctx.deps.user_id
            )
            if normalized_query:
                pattern = f"%{normalized_query}%"
                digest_query = digest_query.filter(
                    or_(
                        DailyNewsDigest.title.ilike(pattern),
                        DailyNewsDigest.summary.ilike(pattern),
                        cast(DailyNewsDigest.key_points, Text).ilike(pattern),
                    )
                )
            digest_rows = (
                digest_query
                .order_by(DailyNewsDigest.local_date.desc(), DailyNewsDigest.id.desc())
                .limit(3)
                .all()
            )
            if not digest_rows:
                digest_rows = (
                    db.query(DailyNewsDigest)
                    .filter(DailyNewsDigest.user_id == ctx.deps.user_id)
                    .order_by(DailyNewsDigest.local_date.desc(), DailyNewsDigest.id.desc())
                    .limit(3)
                    .all()
                )
        return _format_content_hits(
            query=query,
            content_rows=content_rows,
            digest_rows=digest_rows,
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
            response = submit_user_content(
                db,
                SubmitContentRequest(url=url, title=title),
                user,
                submitted_via="assistant",
            )
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
            response = submit_user_content(
                db,
                SubmitContentRequest(url=url, title=title, subscribe_to_feed=True),
                user,
                submitted_via="assistant",
            )
        return response.message

    @agent.tool
    def favorite_content(
        ctx: RunContext[AssistantDeps],
        content_id: int,
    ) -> str:
        """Favorite a content item."""
        with ctx.deps.session_factory() as db:
            favorite = favorites_repository.add_favorite(db, content_id, ctx.deps.user_id)
        if favorite is None:
            return f"Could not favorite content {content_id}."
        return f"Favorited content {content_id}."

    @agent.tool
    def unfavorite_content(
        ctx: RunContext[AssistantDeps],
        content_id: int,
    ) -> str:
        """Remove a content item from favorites."""
        with ctx.deps.session_factory() as db:
            removed = favorites_repository.remove_favorite(db, content_id, ctx.deps.user_id)
        if not removed:
            return f"Content {content_id} was not favorited."
        return f"Removed content {content_id} from favorites."

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
            response = submit_user_content(
                db,
                SubmitContentRequest(url=article_url, title=article_meta.get("title")),
                user,
                submitted_via="assistant",
            )
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
                llm_model="openai:o4-mini-deep-research-2025-06-26",
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

    _agents[cache_key] = agent
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

    candidate_ids: list[int] = []
    if screen_context.content_id:
        candidate_ids.append(screen_context.content_id)
    for content_id in screen_context.visible_content_ids:
        if content_id not in candidate_ids:
            candidate_ids.append(content_id)
    if candidate_ids:
        rows = db.query(Content).filter(Content.id.in_(candidate_ids)).all()
        by_id = {row.id: row for row in rows}
        lines.append("Visible Content:")
        for content_id in candidate_ids:
            content = by_id.get(content_id)
            if content is None:
                continue
            label = (content.title or "Untitled").strip()
            source = f" ({content.source})" if content.source else ""
            lines.append(f"- [{content_id}] {label}{source} — {content.url}")
            short_summary = content.short_summary
            if short_summary:
                lines.append(f"  Short Summary: {short_summary}")
            transcript_excerpt = _extract_transcript_excerpt(content)
            if transcript_excerpt:
                lines.append(f"  Transcript Excerpt: {transcript_excerpt}")

    lines.append(f"User ID: {user_id}")
    return "\n".join(lines)


def create_assistant_session(
    db: Session,
    *,
    user_id: int,
    context_snapshot: str,
    screen_context: AssistantScreenContext,
) -> ChatSession:
    """Create a new assistant session."""
    title = screen_context.screen_title or "Knowledge Chat"
    if screen_context.content_id:
        content = db.query(Content).filter(Content.id == screen_context.content_id).first()
        if content and content.title:
            title = content.title

    session = ChatSession(
        user_id=user_id,
        content_id=screen_context.content_id,
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
    turn_instructions = _build_screen_aware_turn_instructions(user_prompt, deps.screen_context)
    prompt_sections: list[str] = []
    if turn_instructions:
        prompt_sections.append(f"Turn instructions:\n{turn_instructions}")
    prompt_sections.append(f"User request:\n{user_prompt.strip()}")
    prompt_sections.append(f"Current screen context:\n{deps.context_snapshot}")
    prompt = "\n\n".join(prompt_sections)
    with langfuse_trace_context(
        trace_name="assistant.turn.async",
        user_id=deps.user_id,
        session_id=deps.session_id,
        metadata={"model_spec": model_spec, "screen_type": deps.screen_context.screen_type},
        tags=["assistant", "chat"],
    ):
        return agent.run_sync(prompt, deps=deps, message_history=history)


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
    db = SessionLocal()
    try:
        session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
        if session is None:
            logger.error("Assistant session %s not found", session_id)
            return

        history = load_message_history(db, session.id)
        context_snapshot = session.context_snapshot or build_screen_context_snapshot(
            db, user_id=session.user_id, screen_context=screen_context
        )
        deps = AssistantDeps(
            user_id=session.user_id,
            session_id=session.id,
            screen_context=screen_context,
            context_snapshot=context_snapshot,
            session_factory=get_session_factory(),
        )
        provider_api_key = resolve_effective_api_key(
            db=db,
            user_id=session.user_id,
            model_spec=session.llm_model,
        )
        result = await run_in_threadpool(
            run_assistant_turn_sync,
            session.llm_model,
            user_prompt,
            deps,
            history,
            provider_api_key=provider_api_key,
        )
        render_metadata = _extract_render_metadata(result.new_messages())
        _log_chat_usage(result, session_id, message_id, source)
        update_message_completed(
            db,
            message_id,
            result.new_messages(),
            display_user_prompt=user_prompt,
            render_metadata=render_metadata,
        )
        session.last_message_at = datetime.now(UTC)
        session.updated_at = datetime.now(UTC)
        db.commit()
        tool_calls = getattr(result, "tool_calls", []) or []
        tool_names = [
            getattr(call, "name", None)
            or getattr(call, "tool_name", None)
            or getattr(call, "function_name", None)
            for call in tool_calls
        ]
        logger.info(
            "Assistant turn completed | sid=%s mid=%s total_ms=%.1f tools=%s",
            session_id,
            message_id,
            (perf_counter() - total_start) * 1000,
            tool_names,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Assistant turn failed",
            extra={
                "component": "assistant_router",
                "operation": "process_turn",
                "item_id": str(session_id),
                "context_data": {"error": str(exc)},
            },
        )
        db.rollback()
        update_message_failed(db, message_id, str(exc))
    finally:
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
