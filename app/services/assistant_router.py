"""Contextual assistant turns backed by server-side tools."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter

from fastapi.concurrency import run_in_threadpool
from pydantic_ai import Agent, RunContext, UsageLimits
from pydantic_ai.capabilities import PrepareTools
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models.openai import ReasoningEffort
from pydantic_ai.tools import ToolDefinition
from sqlalchemy.orm import Session, sessionmaker

from app.commands import ingest_content as ingest_content_command
from app.core.db import get_session_factory
from app.core.logging import get_logger
from app.core.model_defaults import DEEP_RESEARCH_MODEL_SPEC
from app.core.observability import build_log_extra
from app.core.settings import get_settings
from app.models.api.submissions import SubmitContentRequest
from app.models.contracts import (
    ContentType,
    LlmTaskApprovalPolicy,
    LlmTaskKind,
    LlmTaskMode,
)
from app.models.db import ChatSession, Content, NewsItem
from app.models.db.users import User
from app.models.domain.chat_render import (
    AssistantFeedOption,
    AssistantFeedOptionsResult,
    ChatMessageRenderMetadata,
)
from app.models.domain.chat_sessions import (
    KNOWLEDGE_SESSION_TYPE,
    LEGACY_KNOWLEDGE_SESSION_TYPES,
)
from app.models.internal.assistant import AssistantScreenContext
from app.models.internal.chat_turn import ChatTurnProcessingContext, ChatTurnSessionSnapshot
from app.repositories import read_status_repository
from app.repositories.search_repository import (
    search_content,
    search_news,
    search_subscription_feeds,
)
from app.services import knowledge as knowledge_service
from app.services.agent_data_events import enqueue_agent_data_sync
from app.services.agent_toolset import (
    AGENT_VM_SYSTEM_INSTRUCTIONS,
    AgentToolPolicy,
    AgentToolsetConfig,
    register_agent_vm_tools,
)
from app.services.agent_vm_runtime import AgentVmError
from app.services.assistant_feed_finder import find_feed_options as find_feed_options_service
from app.services.assistant_feed_subscription import subscribe_known_feed
from app.services.assistant_turn_routing import AssistantTurnProfile
from app.services.assistant_turn_routing import (
    resolve_assistant_turn_profile as _resolve_assistant_turn_profile,
)
from app.services.chat_agent import (
    load_message_history,
    save_messages,
    update_message_completed,
    update_message_failed,
)
from app.services.chat_partial_stream import (
    DurableChatPartialWriter as _DurableChatPartialWriter,
)
from app.services.chat_partial_stream import DurableChatToolProgressWriter
from app.services.chat_partial_stream import (
    build_final_text_event_stream_handler as _build_final_text_event_stream_handler,
)
from app.services.chat_tool_progress import (
    agent_vm_tool_log_context,
    numeric_tool_payload_value,
    publish_tool_progress,
    tool_event_status,
)
from app.services.chat_turn_runtime import DetachedChatTurn as _DetachedChatTurn
from app.services.chat_turn_runtime import (
    DetachedChatTurnLifecycle as _DetachedChatTurnLifecycle,
)
from app.services.chat_turn_runtime import QueuedChatTurnOutcome
from app.services.chat_turn_runtime import (
    close_agent_vm_runtime as _close_agent_vm_runtime,
)
from app.services.chat_turn_runtime import extract_tool_names as _extract_tool_names
from app.services.chat_turn_runtime import get_or_create_cached_agent as _get_or_create_cached_agent
from app.services.chat_turn_runtime import log_chat_usage as _log_chat_usage
from app.services.exa_client import exa_search
from app.services.knowledge_search import search_knowledge as search_knowledge_hits
from app.services.lazy_agent_vm import LazyAgentVmRuntime
from app.services.llm_models import (
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
    build_pydantic_model,
    resolve_effective_api_key,
)
from app.services.llm_task_turn_tracker import LlmTaskTurnSpec
from app.services.news_feed import list_unread_visible_news_items
from app.services.prompt_library import load_prompt
from app.services.queued_chat_turn import execute_queued_chat_turn as _execute_queued_chat_turn
from app.utils.news_titles import resolve_news_display_title
from app.utils.title_utils import derive_chat_session_title, resolve_content_display_title

logger = get_logger(__name__)

ASSISTANT_SESSION_TYPES = {
    KNOWLEDGE_SESSION_TYPE,
    *LEGACY_KNOWLEDGE_SESSION_TYPES,
    "weekly_discovery",
}

ASSISTANT_OPENAI_REASONING_EFFORT: ReasoningEffort = "low"

ASSISTANT_SYSTEM_PROMPT = load_prompt("chat/contextual_assistant#system")
CONTEXTUAL_ASSISTANT_TURN_SPEC = LlmTaskTurnSpec(
    task_kind=LlmTaskKind.ASSISTANT_CHAT,
    mode=LlmTaskMode.CONTEXTUAL_ASSISTANT,
    workflow_key="chat.contextual_assistant.v1",
    approval_policy={"default": LlmTaskApprovalPolicy.APPROVAL_REQUIRED.value},
    allowed_actions=[
        "subscribe_to_feed",
        "save_to_knowledge",
        "remove_from_knowledge",
        "mark_content_read",
        "mark_content_unread",
        "create_learning_deck",
    ],
    tool_policy={
        "execute_bash": True,
        "web_search": True,
        "files": "read_write",
        "app_tools": "host_managed",
    },
    prompt_pack="chat.contextual_assistant",
)


@dataclass
class AssistantDeps:
    """Dependencies required to execute an assistant turn."""

    user_id: int
    session_id: int
    screen_context: AssistantScreenContext
    context_snapshot: str
    turn_profile: AssistantTurnProfile
    session_factory: sessionmaker[Session]
    llm_task_id: int | None = None
    vm_runtime: LazyAgentVmRuntime | None = None
    tool_progress_writer: DurableChatToolProgressWriter | None = None


def _assistant_vm_session(deps: AssistantDeps):
    runtime = deps.vm_runtime
    if runtime is None:
        raise AgentVmError("Assistant VM is unavailable")
    return runtime.get_session()


def _log_assistant_vm_tool(
    deps: AssistantDeps,
    event: str,
    payload: dict[str, object],
) -> None:
    publish_tool_progress(deps.tool_progress_writer, event=event, payload=payload)
    status = tool_event_status(event, payload)
    logger.info(
        "Assistant VM tool event",
        extra=build_log_extra(
            component="assistant_turn",
            operation=event,
            event_name=f"assistant.tool.{event}",
            status=status,
            session_id=deps.session_id,
            user_id=deps.user_id,
            duration_ms=numeric_tool_payload_value(payload, "duration_ms"),
            context_data=agent_vm_tool_log_context(
                payload,
                sandbox_acquired=bool(deps.vm_runtime and deps.vm_runtime.acquired),
            ),
        ),
    )


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
        system_prompt=f"{ASSISTANT_SYSTEM_PROMPT}\n\n{AGENT_VM_SYSTEM_INSTRUCTIONS}",
        model_settings=model_settings,
        capabilities=[PrepareTools(_prepare_assistant_tools)],
    )

    @agent.system_prompt
    def add_screen_context(ctx: RunContext[AssistantDeps]) -> str:
        """Keep stable screen context in the system side of the prompt."""
        return f"Current context:\n{ctx.deps.context_snapshot}"

    @agent.tool
    def search_web(
        ctx: RunContext[AssistantDeps],
        query: str,
        limit: int = 5,
    ) -> str:
        """Search the web through Exa for current context or discovery."""
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
            escaped_title = title.replace("[", r"\[").replace("]", r"\]")
            lines.append(f"{idx}. [{escaped_title}](<{url}>)")
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

        result = find_feed_options_service(
            query=query,
            limit=limit,
            user_id=ctx.deps.user_id,
            execution_id=ctx.deps.llm_task_id,
        )
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

    register_agent_vm_tools(
        agent,
        session_getter=_assistant_vm_session,
        log_event=_log_assistant_vm_tool,
        config=AgentToolsetConfig(
            feature="assistant_chat",
            operation_prefix="assistant.tool",
            source="assistant",
            tool_policy=AgentToolPolicy(web_search=False),
            stream_command_progress=True,
        ),
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
        feed_type: str | None = None,
    ) -> str:
        """Subscribe to a feed, using its known type or detecting it from the URL."""
        if feed_type is not None:
            return subscribe_known_feed(
                ctx.deps.session_factory,
                user_id=ctx.deps.user_id,
                url=url,
                title=title,
                feed_type=feed_type,
            )

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


async def _prepare_assistant_tools(
    ctx: RunContext[AssistantDeps],
    tool_defs: list[ToolDefinition],
) -> list[ToolDefinition]:
    """Expose only the exact schemas selected by the pure turn profile."""

    allowed = ctx.deps.turn_profile.tool_names
    selected = [tool_def for tool_def in tool_defs if tool_def.name in allowed]
    logger.info(
        "Assistant tool schemas prepared",
        extra=build_log_extra(
            component="assistant_turn",
            operation="prepare_tools",
            event_name="assistant.turn.tools_prepared",
            status="completed",
            session_id=ctx.deps.session_id,
            user_id=ctx.deps.user_id,
            context_data={
                "route": ctx.deps.turn_profile.route,
                "tool_schema_count": len(selected),
                "available_tool_schema_count": len(tool_defs),
            },
        ),
    )
    return selected


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
            excerpt_limit = (
                1800
                if screen_context.screen_type == "learning_deck"
                and content_id == screen_context.content_id
                else 420
            )
            transcript_excerpt = _extract_transcript_excerpt(content, max_length=excerpt_limit)
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
    commit: bool = True,
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
    db.flush()
    if commit:
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
    partial_writer: _DurableChatPartialWriter | None = None,
):
    """Run one assistant turn synchronously and return the raw agent result."""
    agent = _get_or_create_agent(model_spec, api_key_override=provider_api_key)
    turn_instructions = deps.turn_profile.instructions
    prompt_sections: list[str] = []
    if turn_instructions:
        prompt_sections.append(f"Turn instructions:\n{turn_instructions}")
    prompt_sections.append(f"User request:\n{user_prompt.strip()}")
    prompt = "\n\n".join(prompt_sections)
    event_stream_handler = (
        _build_final_text_event_stream_handler(partial_writer)
        if partial_writer is not None
        else None
    )
    return agent.run_sync(
        prompt,
        deps=deps,
        message_history=history,
        event_stream_handler=event_stream_handler,
        usage_limits=UsageLimits(request_limit=get_settings().llm_task_sandbox_request_limit),
    )


def _build_assistant_vm_runtime(
    *,
    user_id: int,
    session_id: int = 0,
    llm_task_id: int | None = None,
) -> LazyAgentVmRuntime | None:
    """Build a lazy VM handle without performing E2B or corpus work."""
    settings = get_settings()
    if settings.llm_task_sandbox_provider == "disabled":
        return None
    return LazyAgentVmRuntime(
        user_id=user_id,
        session_id=session_id,
        llm_task_id=llm_task_id,
        feature="assistant",
    )


@dataclass(frozen=True)
class _PreparedAssistantTurn:
    deps: AssistantDeps
    history: list[ModelMessage]
    provider_api_key: str | None
    history_ms: float
    context_ms: float


@dataclass(frozen=True)
class _ExecutedAssistantTurn:
    raw_result: object
    new_messages: list[ModelMessage]
    render_metadata: ChatMessageRenderMetadata | None
    output_chars: int
    tool_names: list[str]


def _prepare_assistant_background_turn(
    db: Session,
    session: ChatTurnSessionSnapshot,
    turn: _DetachedChatTurn,
    *,
    screen_context: AssistantScreenContext,
    user_prompt: str,
    turn_profile: AssistantTurnProfile | None = None,
) -> _PreparedAssistantTurn:
    del db
    session_factory = get_session_factory()
    resolved_turn_profile = turn_profile or _resolve_assistant_turn_profile(
        user_prompt,
        screen_context,
    )

    def build_history() -> tuple[list[ModelMessage], float]:
        started_at = perf_counter()
        with session_factory() as history_db:
            value = load_message_history(
                history_db,
                turn.session_id,
                exclude_message_id=turn.message_id,
                completed_only=True,
            )
        return value, (perf_counter() - started_at) * 1000

    def build_context() -> tuple[str, float]:
        started_at = perf_counter()
        if session.context_snapshot:
            value = session.context_snapshot
        else:
            with session_factory() as context_db:
                value = build_screen_context_snapshot(
                    context_db,
                    user_id=turn.user_id,
                    screen_context=screen_context,
                )
        return value, (perf_counter() - started_at) * 1000

    def resolve_key() -> str | None:
        with session_factory() as key_db:
            return resolve_effective_api_key(
                db=key_db,
                user_id=turn.user_id,
                model_spec=turn.model,
            )

    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="assistant-prepare") as executor:
        history_future = executor.submit(build_history)
        context_future = executor.submit(build_context)
        key_future = executor.submit(resolve_key)
        history, history_ms = history_future.result()
        context_snapshot, context_ms = context_future.result()
        provider_api_key = key_future.result()
    logger.info(
        "Assistant history loaded",
        extra=build_log_extra(
            component="assistant_turn",
            operation="load_history",
            event_name="assistant.turn.history_loaded",
            status="completed",
            duration_ms=history_ms,
            session_id=turn.session_id,
            message_id=turn.message_id,
            user_id=turn.user_id,
            content_id=turn.content_id,
            context_data={
                "history_count": len(history),
                "llm_task_id": turn.llm_task_id,
            },
        ),
    )

    vm_runtime: LazyAgentVmRuntime | None = None
    if resolved_turn_profile.uses_agent_vm:
        vm_runtime = _build_assistant_vm_runtime(
            user_id=turn.user_id,
            session_id=turn.session_id,
            llm_task_id=turn.llm_task_id,
        )
    deps = AssistantDeps(
        user_id=turn.user_id,
        session_id=turn.session_id,
        screen_context=screen_context,
        context_snapshot=context_snapshot,
        turn_profile=resolved_turn_profile,
        session_factory=session_factory,
        llm_task_id=turn.llm_task_id,
        vm_runtime=vm_runtime,
    )
    logger.info(
        "Assistant context built",
        extra=build_log_extra(
            component="assistant_turn",
            operation="build_context",
            event_name="assistant.turn.context_built",
            status="completed",
            duration_ms=context_ms,
            session_id=turn.session_id,
            message_id=turn.message_id,
            user_id=turn.user_id,
            content_id=turn.content_id,
            context_data={
                "screen_type": screen_context.screen_type,
                "route": resolved_turn_profile.route,
                "context_chars": len(context_snapshot or ""),
                "tool_schema_count": len(resolved_turn_profile.tool_names),
                "uses_agent_vm": resolved_turn_profile.uses_agent_vm,
                "sandbox_acquired": bool(vm_runtime and vm_runtime.acquired),
                "sandbox_acquisition_ms": 0.0,
                "sandbox_hydration_ms": 0.0,
                "llm_task_id": turn.llm_task_id,
            },
        ),
    )
    return _PreparedAssistantTurn(
        deps=deps,
        history=history,
        provider_api_key=provider_api_key,
        history_ms=history_ms,
        context_ms=context_ms,
    )


async def _execute_assistant_background_turn(
    prepared: _PreparedAssistantTurn,
    turn: _DetachedChatTurn,
    *,
    user_prompt: str,
    partial_writer: _DurableChatPartialWriter,
) -> _ExecutedAssistantTurn:
    if turn.message_id is not None:
        prepared.deps.tool_progress_writer = DurableChatToolProgressWriter(
            session_factory=get_session_factory(),
            message_id=turn.message_id,
            stream_generation=turn.stream_generation,
        )
    logger.info(
        "Assistant LLM call started",
        extra=build_log_extra(
            component="assistant_turn",
            operation="llm_call",
            event_name="assistant.turn.llm_started",
            status="started",
            session_id=turn.session_id,
            message_id=turn.message_id,
            user_id=turn.user_id,
            content_id=turn.content_id,
            source=turn.source,
            context_data={
                "model": turn.model,
                "screen_type": prepared.deps.screen_context.screen_type,
                "llm_task_id": turn.llm_task_id,
            },
        ),
    )
    result = await run_in_threadpool(
        run_assistant_turn_sync,
        turn.model,
        user_prompt,
        prepared.deps,
        prepared.history,
        provider_api_key=prepared.provider_api_key,
        partial_writer=partial_writer,
    )
    new_messages = result.new_messages()
    tool_names = _extract_tool_names(result)
    return _ExecutedAssistantTurn(
        raw_result=result,
        new_messages=new_messages,
        render_metadata=_extract_render_metadata(new_messages),
        output_chars=len(str(getattr(result, "output", "") or "")),
        tool_names=tool_names,
    )


def _persist_assistant_background_turn(
    db: Session,
    executed: _ExecutedAssistantTurn,
    turn: _DetachedChatTurn,
    *,
    user_prompt: str,
) -> dict[str, object]:
    if turn.message_id is None:
        raise ValueError("Assistant turn is missing a message id")
    update_message_completed(
        db,
        turn.message_id,
        executed.new_messages,
        display_user_prompt=user_prompt,
        render_metadata=executed.render_metadata,
        expected_stream_generation=turn.stream_generation,
        commit=False,
    )
    enqueue_agent_data_sync(
        db,
        user_id=turn.user_id,
        chat_session_ids=(turn.session_id,),
    )
    return {
        "chat_session_id": turn.session_id,
        "message_id": turn.message_id,
        "content_id": turn.content_id,
        "news_item_id": turn.news_item_id,
        "output_chars": executed.output_chars,
    }


def _stage_assistant_message_failed(
    db: Session,
    message_id: int,
    error: str,
    stream_generation: int,
) -> object:
    return update_message_failed(
        db,
        message_id,
        error,
        expected_stream_generation=stream_generation,
        commit=False,
    )


async def process_assistant_turn_async(
    session_id: int,
    message_id: int,
    user_prompt: str,
    *,
    screen_context: AssistantScreenContext,
    turn_context: ChatTurnProcessingContext,
    stream_generation: int,
    ensure_lease: Callable[[], bool],
    source: str = "assistant",
    task_id: int | None = None,
) -> QueuedChatTurnOutcome:
    """Process one assistant turn without retaining a DB session across I/O."""
    total_start = perf_counter()
    session_factory = get_session_factory()
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

    lifecycle = _DetachedChatTurnLifecycle(
        task_spec=CONTEXTUAL_ASSISTANT_TURN_SPEC,
        running_note="Running contextual assistant agent",
        completed_note="Contextual assistant turn completed",
        failed_note="Contextual assistant turn failed",
        usage_context=source,
    )

    session_snapshot = turn_context.session
    turn_profile = _resolve_assistant_turn_profile(user_prompt, screen_context)
    result = await _execute_queued_chat_turn(
        session_factory=session_factory,
        session_snapshot=session_snapshot,
        session_id=session_id,
        message_id=message_id,
        source=source,
        task_id=task_id,
        stream_generation=stream_generation,
        lifecycle=lifecycle,
        input_json=lambda turn: {
            "chat_session_id": turn.session_id,
            "content_id": turn.content_id,
            "news_item_id": turn.news_item_id,
            "source": turn.source,
            "screen_type": screen_context.screen_type,
            "assistant_action": screen_context.assistant_action,
            "route": turn_profile.route,
            "tool_names": sorted(turn_profile.tool_names),
            "queue_task_id": task_id,
            "prompt_chars": len(user_prompt),
            "model": turn.model,
            "stream_generation": turn.stream_generation,
        },
        prepare=lambda db, turn: _prepare_assistant_background_turn(
            db,
            session_snapshot,
            turn,
            screen_context=screen_context,
            user_prompt=user_prompt,
            turn_profile=turn_profile,
        ),
        execute=lambda prepared, turn, partial_writer: _execute_assistant_background_turn(
            prepared,
            turn,
            user_prompt=user_prompt,
            partial_writer=partial_writer,
        ),
        persist=lambda db, executed, turn: _persist_assistant_background_turn(
            db,
            executed,
            turn,
            user_prompt=user_prompt,
        ),
        mark_message_failed=_stage_assistant_message_failed,
        raw_result=lambda executed: executed.raw_result,
        record_usage=_log_chat_usage,
        ensure_lease=ensure_lease,
        cleanup=lambda prepared: _close_agent_vm_runtime(prepared.deps.vm_runtime),
    )
    if result.outcome != QueuedChatTurnOutcome.COMPLETED:
        return result.outcome

    turn = result.turn
    prepared = result.prepared
    executed = result.executed
    if turn is None or prepared is None or executed is None:
        raise RuntimeError("Assistant turn completed without runtime state")

    logger.info(
        "Assistant turn completed",
        extra=build_log_extra(
            component="assistant_turn",
            operation="process_turn",
            event_name="assistant.turn",
            status="completed",
            duration_ms=(perf_counter() - total_start) * 1000,
            session_id=turn.session_id,
            message_id=turn.message_id,
            user_id=turn.user_id,
            content_id=turn.content_id,
            source=turn.source,
            context_data={
                "model": turn.model,
                "tool_names": executed.tool_names,
                "tool_count": len(executed.tool_names),
                "agent_ms": round(result.external_ms, 2),
                "history_ms": round(prepared.history_ms, 2),
                "context_ms": round(prepared.context_ms, 2),
                "partial_write_count": result.partial_write_count,
                "first_partial_ms": result.first_partial_ms,
                "llm_task_id": turn.llm_task_id,
            },
        ),
    )
    return result.outcome


def seed_assistant_message(
    db: Session,
    *,
    session_id: int,
    assistant_text: str,
    render_metadata: ChatMessageRenderMetadata | None = None,
    commit: bool = True,
) -> None:
    """Persist an assistant-only seed message into a chat session."""
    from pydantic_ai.messages import ModelResponse, TextPart

    user_id = db.query(ChatSession.user_id).filter(ChatSession.id == session_id).scalar()
    if user_id is None:
        raise ValueError(f"Chat session {session_id} not found")
    save_messages(
        db,
        session_id,
        [ModelResponse(parts=[TextPart(content=assistant_text)])],
        render_metadata=render_metadata,
        commit=False,
    )
    enqueue_agent_data_sync(
        db,
        user_id=int(user_id),
        chat_session_ids=(session_id,),
    )
    if commit:
        db.commit()
