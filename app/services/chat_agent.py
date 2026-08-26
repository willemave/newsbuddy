"""Chat agent service using pydantic-ai for deep-dive conversations."""

import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter

from fastapi.concurrency import run_in_threadpool
from pydantic_ai import Agent, RunContext, UsageLimits
from pydantic_ai.capabilities import PrepareTools
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter
from pydantic_ai.models.openai import ReasoningEffort
from pydantic_ai.tools import ToolDefinition
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.observability import build_log_extra
from app.core.settings import get_settings
from app.models.contracts import (
    LlmTaskApprovalPolicy,
    LlmTaskKind,
    LlmTaskMode,
    MessageProcessingStatus,
)
from app.models.db import ChatMessage, ChatSession, Content
from app.models.domain.chat_render import ChatMessageRenderMetadata
from app.models.domain.chat_sessions import KNOWLEDGE_SESSION_TYPE
from app.models.internal.chat_turn import ChatTurnProcessingContext, ChatTurnSessionSnapshot
from app.services.agent_data_events import enqueue_agent_data_sync
from app.services.agent_toolset import (
    AGENT_VM_SYSTEM_INSTRUCTIONS,
    AgentToolPolicy,
    AgentToolsetConfig,
    register_agent_knowledge_search_tool,
    register_agent_vm_tools,
)
from app.services.agent_vm_runtime import AGENT_VM_TOOL_NAMES, AgentVmError
from app.services.chat_context_budget import (
    CHAT_HISTORY_MAX_TOKENS,
    CHAT_OUTPUT_RESERVE_TOKENS,
    CHAT_TOOL_SCHEMA_RESERVE_TOKENS,
    CONTEXT_WINDOW_TOKENS,
    SYSTEM_AND_ARTICLE_BUDGET_RATIO,
    available_chat_history_tokens,
    trim_message_history_to_token_budget,
)
from app.services.chat_context_budget import (
    estimate_tokens as _estimate_tokens,
)
from app.services.chat_context_budget import (
    truncate_to_token_budget as _truncate_to_token_budget,
)
from app.services.chat_history import load_message_history
from app.services.chat_partial_stream import (
    DurableChatPartialWriter as _DurableChatPartialWriter,
)
from app.services.chat_partial_stream import DurableChatToolProgressWriter
from app.services.chat_partial_stream import (
    build_final_text_event_stream_handler as _build_final_text_event_stream_handler,
)
from app.services.chat_tool_progress import (
    agent_tool_log_context,
    numeric_tool_payload_value,
    publish_tool_progress,
    tool_event_status,
)
from app.services.chat_turn_runtime import ChatTurnOwnershipLost, QueuedChatTurnOutcome
from app.services.chat_turn_runtime import (
    ChatUsageSnapshot as _ChatUsageSnapshot,
)
from app.services.chat_turn_runtime import DetachedChatTurn as _DetachedChatTurn
from app.services.chat_turn_runtime import (
    DetachedChatTurnLifecycle as _DetachedChatTurnLifecycle,
)
from app.services.chat_turn_runtime import (
    close_agent_vm_runtime as _close_agent_vm_runtime,
)
from app.services.chat_turn_runtime import (
    complete_detached_chat_turn as _complete_detached_chat_turn,
)
from app.services.chat_turn_runtime import extract_tool_names as _extract_tool_names
from app.services.chat_turn_runtime import get_or_create_cached_agent as _get_or_create_cached_agent
from app.services.chat_turn_runtime import (
    log_chat_usage as _log_chat_usage,
)
from app.services.chat_turn_runtime import (
    mark_detached_chat_turn_running as _mark_detached_chat_turn_running,
)
from app.services.chat_turn_runtime import (
    persist_detached_turn_failure as _persist_detached_turn_failure,
)
from app.services.chat_turn_runtime import (
    require_session_id as _require_session_id,
)
from app.services.chat_turn_runtime import (
    require_session_user_id as _require_session_user_id,
)
from app.services.chat_turn_runtime import (
    snapshot_detached_chat_turn as _snapshot_detached_chat_turn,
)
from app.services.chat_turn_runtime import (
    start_detached_chat_turn as _start_detached_chat_turn,
)
from app.services.exa_client import exa_search, get_exa_client
from app.services.lazy_agent_vm import LazyAgentVmRuntime
from app.services.llm_models import (
    build_pydantic_model,
    resolve_effective_api_key,
    resolve_model_provider,
)
from app.services.llm_task_turn_tracker import LlmTaskTurnSpec, LlmTaskTurnTracker
from app.services.prompt_library import load_prompt
from app.services.queued_chat_turn import execute_queued_chat_turn as _execute_queued_chat_turn

logger = get_logger(__name__)

CHAT_OPENAI_REASONING_EFFORT: ReasoningEffort = "low"

SYSTEM_PROMPT_TEXT = load_prompt("chat/article#system")
ARTICLE_CHAT_TURN_SPEC = LlmTaskTurnSpec(
    task_kind=LlmTaskKind.ARTICLE_CHAT,
    mode=LlmTaskMode.ARTICLE_CHAT,
    workflow_key="chat.article.v1",
    approval_policy={"default": LlmTaskApprovalPolicy.APPROVAL_REQUIRED.value},
    allowed_actions=[],
    tool_policy={
        "execute_bash": True,
        "web_search": True,
        "files": "read_write",
    },
    prompt_pack="chat.article",
)
ARTICLE_BACKGROUND_TURN_LIFECYCLE = _DetachedChatTurnLifecycle(
    task_spec=ARTICLE_CHAT_TURN_SPEC,
    running_note="Running async article chat agent",
    completed_note="Async article chat turn completed",
    failed_note="Async article chat turn failed",
    usage_context="async",
)


def _extract_summary_insights(summary: dict[str, object]) -> list[dict[str, str]]:
    insights = summary.get("insights", [])
    if not isinstance(insights, list):
        return []
    extracted: list[dict[str, str]] = []
    for item in insights:
        if not isinstance(item, dict):
            continue
        insight = str(item.get("insight", "")).strip()
        topic = str(item.get("topic", "")).strip()
        quote = str(item.get("supporting_quote", "")).strip()
        attribution = str(item.get("quote_attribution", "")).strip()
        extracted.append(
            {
                "insight": insight,
                "topic": topic,
                "quote": quote,
                "attribution": attribution,
            }
        )
    return extracted


def _build_summary_lines(summary: dict[str, object]) -> list[str]:
    lines: list[str] = []
    title = summary.get("title")
    if isinstance(title, str) and title.strip():
        lines.append(f"Summary Title: {title.strip()}")

    overview = (
        summary.get("summary")
        or summary.get("overview")
        or summary.get("hook")
        or summary.get("takeaway")
    )
    if isinstance(overview, str) and overview.strip():
        lines.append(f"Overview: {overview.strip()}")

    insights = _extract_summary_insights(summary)

    bullet_points = summary.get("key_points") or summary.get("bullet_points")
    if isinstance(bullet_points, list) and bullet_points:
        points = [
            bp.get("text", "") if isinstance(bp, dict) else str(bp)
            for bp in bullet_points
            if isinstance(bp, (dict, str))
        ]
        cleaned = [point.strip() for point in points if point and str(point).strip()]
        if cleaned:
            lines.append("Key Points:")
            for point in cleaned:
                lines.append(f"  - {point}")
    elif insights:
        lines.append("Insights:")
        for ins in insights:
            if not ins["insight"]:
                continue
            if ins["topic"]:
                entry = f"  - {ins['topic']}: {ins['insight']}"
            else:
                entry = f"  - {ins['insight']}"
            if ins["quote"]:
                attribution = f" — {ins['attribution']}" if ins["attribution"] else ""
                entry = f'{entry} (Quote: "{ins["quote"]}"{attribution})'
            lines.append(entry)

    quotes = summary.get("quotes")
    if isinstance(quotes, list) and quotes:
        rendered_quotes = []
        for quote in quotes:
            if not isinstance(quote, dict):
                continue
            quote_text = str(quote.get("text", "")).strip()
            if not quote_text:
                continue
            context = str(quote.get("context", "")).strip()
            rendered_quotes.append((quote_text, context))
        if rendered_quotes:
            lines.append("Quotes:")
            for quote_text, context in rendered_quotes:
                lines.append(f'  - "{quote_text}"')
                if context:
                    lines.append(f"    — {context}")

    topics = summary.get("topics")
    cleaned_topics: list[str] = []
    if isinstance(topics, list) and topics:
        cleaned_topics = [str(topic).strip() for topic in topics if str(topic).strip()]
    elif insights:
        seen: set[str] = set()
        for ins in insights:
            topic = ins["topic"]
            if topic and topic not in seen:
                seen.add(topic)
                cleaned_topics.append(topic)
    if cleaned_topics:
        lines.append(f"Topics: {', '.join(cleaned_topics)}")

    questions = summary.get("questions")
    if isinstance(questions, list) and questions:
        cleaned_questions = [str(q).strip() for q in questions if str(q).strip()]
        if cleaned_questions:
            lines.append("Questions:")
            for question in cleaned_questions:
                lines.append(f"  - {question}")

    counter_arguments = summary.get("counter_arguments")
    if isinstance(counter_arguments, list) and counter_arguments:
        cleaned_counters = [str(c).strip() for c in counter_arguments if str(c).strip()]
        if cleaned_counters:
            lines.append("Counter-Arguments:")
            for counter in cleaned_counters:
                lines.append(f"  - {counter}")

    classification = summary.get("classification")
    if isinstance(classification, str) and classification.strip():
        lines.append(f"Classification: {classification.strip()}")

    return lines


@dataclass
class ChatDeps:
    """Detached-safe dependencies passed to the chat agent."""

    session_id: int
    user_id: int
    session_factory: Callable[[], Session]
    content_id: int | None = None
    has_content: bool = False
    article_context: str | None = None
    system_context: str = ""
    vm_runtime: LazyAgentVmRuntime | None = None
    tool_progress_writer: DurableChatToolProgressWriter | None = None


def _build_article_header(content: Content | None, topic: str | None) -> list[str]:
    parts: list[str] = []
    if content:
        parts.append(f"Article Title: {content.title or 'Untitled'}")
        parts.append(f"Source: {content.source or 'Unknown'}")
        parts.append(f"URL: {content.url}")
    if topic:
        parts.append(f"\nFocus Topic: {topic}")
    return parts


def _build_context_prompt_parts(
    content: Content | None,
    topic: str | None,
    article_context: str | None,
    context_label: str,
) -> list[str]:
    """Build dynamic prompt sections that expose reference context to the model."""
    parts = _build_article_header(content, topic)

    if article_context:
        parts.append(f"\n{load_prompt('chat/article#context_notice')}")
        parts.append(f"\n{context_label}:\n{article_context}")

    return parts


def _chat_vm_session(deps: ChatDeps):
    runtime = deps.vm_runtime
    if runtime is None:
        raise AgentVmError("Chat VM is unavailable")
    return runtime.get_session()


def _log_chat_tool(deps: ChatDeps, event: str, payload: dict[str, object]) -> None:
    publish_tool_progress(deps.tool_progress_writer, event=event, payload=payload)
    status = tool_event_status(event, payload)
    logger.info(
        "Chat agent tool event",
        extra=build_log_extra(
            component="chat",
            operation=event,
            event_name=f"chat.tool.{event}",
            status=status,
            session_id=deps.session_id,
            user_id=deps.user_id,
            content_id=deps.content_id,
            duration_ms=numeric_tool_payload_value(payload, "duration_ms"),
            context_data=agent_tool_log_context(
                payload,
                sandbox_acquired=bool(deps.vm_runtime and deps.vm_runtime.acquired),
            ),
        ),
    )


def get_chat_agent(
    model_spec: str,
    *,
    api_key_override: str | None = None,
) -> Agent[ChatDeps, str]:
    """Get or create a chat agent for the given model spec."""
    return _get_or_create_cached_agent(
        "article_chat",
        model_spec,
        api_key_override,
        lambda: _create_chat_agent(model_spec, api_key_override=api_key_override),
    )


def _create_chat_agent(
    model_spec: str,
    *,
    api_key_override: str | None = None,
) -> Agent[ChatDeps, str]:
    """Get or create a chat agent for the given model spec.

    Args:
        model_spec: Full pydantic-ai model specification.

    Returns:
        Configured Agent instance.
    """
    # Build model with explicit API key if needed
    model, model_settings = build_pydantic_model(
        model_spec,
        api_key_override=api_key_override,
        openai_reasoning_effort=CHAT_OPENAI_REASONING_EFFORT,
    )

    agent: Agent[ChatDeps, str] = Agent(
        model,
        deps_type=ChatDeps,
        output_type=str,
        system_prompt=f"{SYSTEM_PROMPT_TEXT}\n\n{AGENT_VM_SYSTEM_INSTRUCTIONS}",
        model_settings=model_settings,
        capabilities=[PrepareTools(_prepare_chat_tools)],
    )

    @agent.system_prompt
    def add_article_context(ctx: RunContext[ChatDeps]) -> str:
        """Add article context to the system prompt."""
        return ctx.deps.system_context

    register_agent_vm_tools(
        agent,
        session_getter=_chat_vm_session,
        log_event=_log_chat_tool,
        config=AgentToolsetConfig(
            feature="article_chat",
            operation_prefix="chat.tool",
            source="chat",
            tool_policy=AgentToolPolicy(web_search=False),
            stream_command_progress=True,
        ),
    )

    register_agent_knowledge_search_tool(
        agent,
        session_factory_getter=lambda deps: deps.session_factory,
        user_id_getter=lambda deps: deps.user_id,
        log_event=_log_chat_tool,
    )

    @agent.tool
    def exa_web_search(
        ctx: RunContext[ChatDeps],
        query: str,
        num_results: int = 8,
        category: str | None = None,
    ) -> str:
        """Search the web using Exa for additional context and research.

        Use this tool proactively when you need more information beyond what's
        in the article, or when the user asks about related topics, recent
        developments, or wants to verify claims.

        Args:
            query: Natural language search query. Be specific and descriptive.
                   Good: "MIT study AI productivity enterprise workers 2024"
                   Bad: "AI productivity"
            num_results: Number of results to return (1-10). Default 8.
            category: Optional filter to focus results. Options:
                      - "news" - Recent news articles
                      - "research paper" - Academic papers
                      - "company" - Company websites and info
                      - "pdf" - PDF documents
                      - "github" - GitHub repos and docs
                      - None - All content types (default)

        Returns:
            Formatted search results with content to synthesize into your response.
            You MUST use this content - summarize findings, quote key insights,
            and include source links in your response.
        """
        session_id = ctx.deps.session_id
        logger.info(
            f"[Tool:exa_web_search] Called | session_id={session_id} "
            f"query='{query[:100]}' num_results={num_results} category={category}"
        )

        # Check if Exa is available
        if get_exa_client() is None:
            logger.warning(f"[Tool:exa_web_search] Exa unavailable | sid={session_id}")
            return "Web search unavailable. Please answer based on your knowledge."

        # Clamp num_results
        num_results = max(1, min(10, num_results))

        # Execute search with enhanced options
        tool_start = perf_counter()
        try:
            results = exa_search(
                query,
                num_results=num_results,
                category=category,
                telemetry={
                    "feature": "chat_agent",
                    "operation": "chat_agent.search_web",
                    "session_id": session_id,
                    "user_id": ctx.deps.user_id,
                },
            )
            logger.info(
                f"[Tool:exa_web_search] Success | session_id={session_id} "
                f"results_count={len(results)}"
            )
            for i, r in enumerate(results):
                logger.debug(
                    f"[Tool:exa_web_search] Result {i + 1} | "
                    f"title='{r.title[:50] if r.title else 'N/A'}' url={r.url}"
                )
        except Exception as e:
            logger.error(f"[Tool:exa_web_search] Error | session_id={session_id} error={e}")
            return "Search failed. Please answer based on your knowledge."

        if not results:
            return "No relevant results found. Please answer based on your knowledge."

        duration_ms = (perf_counter() - tool_start) * 1000
        logger.info(
            "[Tool:exa_web_search] Completed | sid=%s ms=%.1f results=%d",
            session_id,
            duration_ms,
            len(results),
        )

        # Format results as structured text for the LLM to synthesize
        output_parts = [
            f"Found {len(results)} relevant sources. "
            "Synthesize these into your response with citations:\n"
        ]

        for i, r in enumerate(results, 1):
            output_parts.append(f"\n---\n**Source {i}: [{r.title}]({r.url})**\n")
            if r.snippet:
                # Truncate very long snippets
                snippet = r.snippet[:1500] if len(r.snippet) > 1500 else r.snippet
                output_parts.append(f"{snippet}\n")

        output_parts.append(
            "\n---\n"
            "INSTRUCTION: Use the above sources to provide a comprehensive response. "
            "Include specific facts, quotes, and [linked citations](url) from the sources."
        )

        return "".join(output_parts)

    logger.info(f"Created chat agent for model: {model_spec}")
    return agent


async def _prepare_chat_tools(
    ctx: RunContext[ChatDeps],
    tool_defs: list[ToolDefinition],
) -> list[ToolDefinition]:
    """Hide VM schemas when this turn has no lazy VM capability."""
    if ctx.deps.vm_runtime is not None:
        return tool_defs
    return [tool_def for tool_def in tool_defs if tool_def.name not in AGENT_VM_TOOL_NAMES]


def build_article_context(
    db: Session,
    content: Content,
    include_full_text: bool = False,
    max_tokens: int | None = None,
) -> str | None:
    """Build context string from article content and metadata.

    Args:
        content: Content database record.
        include_full_text: Whether to include full transcript/content when it fits the budget.
        max_tokens: Optional token budget for the article context string.

    Returns:
        Formatted context string or None if no content available.
    """
    if not content.content_metadata:
        return None

    metadata = content.content_metadata
    summary = metadata.get("summary", {})
    summary_lines: list[str] = []
    if isinstance(summary, dict) and summary:
        summary_lines = _build_summary_lines(summary)

    from app.services.content_bodies import ContentBodyVariant, get_content_body_resolver

    transcript = metadata.get("transcript")
    content_text = metadata.get("content")
    full_markdown = None
    if not content_text and isinstance(summary, dict):
        full_markdown = summary.get("full_markdown")

    resolved_body = get_content_body_resolver().resolve(
        db,
        content=content,
        variant=ContentBodyVariant.SOURCE,
    )

    full_text_label = None
    full_text = None
    if resolved_body and resolved_body.text.strip():
        full_text_label = "Transcript" if resolved_body.kind == "transcript" else "Full Content"
        full_text = resolved_body.text.strip()
    elif isinstance(transcript, str) and transcript.strip():
        full_text_label = "Transcript"
        full_text = transcript.strip()
    elif isinstance(content_text, str) and content_text.strip():
        full_text_label = "Full Content"
        full_text = content_text.strip()
    elif isinstance(full_markdown, str) and full_markdown.strip():
        full_text_label = "Full Content"
        full_text = full_markdown.strip()

    summary_context = "\n".join(summary_lines).strip() if summary_lines else ""
    full_context_parts = summary_lines.copy()
    if full_text and include_full_text:
        full_context_parts.append(f"\n{full_text_label}:\n{full_text}")
    full_context = "\n".join(full_context_parts).strip() if full_context_parts else ""

    if max_tokens is None:
        if full_context:
            return full_context
        if summary_context:
            return summary_context
        if full_text:
            return f"{full_text_label}:\n{full_text}"
        return None

    if include_full_text and full_context and _estimate_tokens(full_context) <= max_tokens:
        return full_context

    if summary_context:
        if _estimate_tokens(summary_context) <= max_tokens:
            return summary_context
        return _truncate_to_token_budget(summary_context, max_tokens)

    if full_text:
        truncated_text = _truncate_to_token_budget(full_text, max_tokens)
        return f"{full_text_label}:\n{truncated_text}"

    return None


def _dump_messages_json(
    messages: list[ModelMessage],
    *,
    display_user_prompt: str | None = None,
) -> str:
    """Serialize messages for storage, preserving the user-visible prompt text."""
    message_json = ModelMessagesTypeAdapter.dump_json(messages).decode("utf-8")
    if display_user_prompt is None:
        return message_json

    payload = json.loads(message_json)
    for message in payload:
        if message.get("kind") != "request":
            continue
        parts = message.get("parts") or []
        for part in parts:
            if (
                isinstance(part, dict)
                and part.get("part_kind") == "user-prompt"
                and "content" in part
            ):
                part["content"] = display_user_prompt
                return json.dumps(payload, separators=(",", ":"))
    return json.dumps(payload, separators=(",", ":"))


def save_messages(
    db: Session,
    session_id: int,
    messages: list[ModelMessage],
    status: MessageProcessingStatus = MessageProcessingStatus.COMPLETED,
    *,
    display_user_prompt: str | None = None,
    render_metadata: ChatMessageRenderMetadata | dict[str, object] | None = None,
    commit: bool = True,
) -> ChatMessage:
    """Save new messages to the database.

    Args:
        db: Database session.
        session_id: Chat session ID.
        messages: List of ModelMessage objects to save.
        status: Processing status for the message.
        display_user_prompt: Optional user-visible prompt text to persist
            instead of the model-facing request content.
        commit: Commit immediately when true; otherwise leave the row staged in
            the caller's transaction.

    Returns:
        The created ChatMessage record.
    """
    try:
        # Serialize messages to JSON (empty list if no messages)
        message_json = _dump_messages_json(
            messages,
            display_user_prompt=display_user_prompt,
        )

        # Create new ChatMessage record
        db_message = ChatMessage(
            session_id=session_id,
            message_list=message_json,
            render_metadata=_serialize_render_metadata(render_metadata),
            created_at=datetime.now(UTC),
            status=status.value,
        )
        db.add(db_message)
        db.flush()
        if commit:
            db.commit()
            db.refresh(db_message)
        logger.debug(f"Saved {len(messages)} messages for session {session_id}")
        return db_message
    except Exception as e:
        logger.error(f"Failed to save messages: {e}")
        db.rollback()
        raise


def create_processing_message(
    db: Session,
    session_id: int,
    user_prompt: str,
    *,
    processing_context: dict[str, object] | None = None,
    commit: bool = True,
) -> ChatMessage:
    """Create a placeholder message record with processing status.

    This is called immediately when a user sends a message, before LLM processing.
    The user_prompt is stored as a UserPromptPart so it can be displayed immediately.

    Args:
        db: Database session.
        session_id: Chat session ID.
        user_prompt: The user's message text.

    Returns:
        The created ChatMessage record with status=processing.
    """
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    # Create a ModelRequest with just the user prompt
    user_message = ModelRequest(parts=[UserPromptPart(content=user_prompt)])
    db_message = save_messages(
        db,
        session_id,
        [user_message],
        status=MessageProcessingStatus.PROCESSING,
        commit=False,
    )
    db_message.processing_context = processing_context
    db.flush()
    if commit:
        db.commit()
        db.refresh(db_message)
    return db_message


def update_message_completed(
    db: Session,
    message_id: int,
    messages: list[ModelMessage],
    *,
    display_user_prompt: str | None = None,
    render_metadata: ChatMessageRenderMetadata | dict[str, object] | None = None,
    expected_stream_generation: int | None = None,
    commit: bool = True,
) -> ChatMessage:
    """Update a processing message with the completed result.

    Args:
        db: Database session.
        message_id: ChatMessage ID to update.
        messages: Full list of messages (user + assistant).
        display_user_prompt: Optional user-visible prompt text to persist
            instead of the model-facing request content.

    Returns:
        The updated ChatMessage record.
    """
    db_message = (
        db.query(ChatMessage).filter(ChatMessage.id == message_id).with_for_update().first()
    )
    if not db_message:
        raise ValueError(f"Message {message_id} not found")

    if (
        expected_stream_generation is not None
        and db_message.stream_generation != expected_stream_generation
    ):
        raise ChatTurnOwnershipLost("A newer chat attempt owns this message")
    if db_message.status != MessageProcessingStatus.PROCESSING.value:
        return db_message

    message_json = _dump_messages_json(
        messages,
        display_user_prompt=display_user_prompt,
    )
    db_message.message_list = message_json
    db_message.render_metadata = _serialize_render_metadata(render_metadata)
    db_message.status = MessageProcessingStatus.COMPLETED.value
    db_message.error = None
    db_message.partial_text = None
    db.flush()
    if commit:
        db.commit()
        db.refresh(db_message)
    logger.debug(f"Updated message {message_id} to completed")
    return db_message


def update_message_failed(
    db: Session,
    message_id: int,
    error: str,
    *,
    expected_stream_generation: int | None = None,
    commit: bool = True,
) -> ChatMessage:
    """Mark a processing message as failed.

    Args:
        db: Database session.
        message_id: ChatMessage ID to update.
        error: Error message describing the failure.

    Returns:
        The updated ChatMessage record.
    """
    db_message = (
        db.query(ChatMessage).filter(ChatMessage.id == message_id).with_for_update().first()
    )
    if not db_message:
        raise ValueError(f"Message {message_id} not found")

    if (
        expected_stream_generation is not None
        and db_message.stream_generation != expected_stream_generation
    ):
        raise ChatTurnOwnershipLost("A newer chat attempt owns this message")
    if db_message.status != MessageProcessingStatus.PROCESSING.value:
        return db_message

    db_message.status = MessageProcessingStatus.FAILED.value
    db_message.render_metadata = None
    db_message.error = error
    db_message.partial_text = None
    db.flush()
    if commit:
        db.commit()
        db.refresh(db_message)
    logger.warning(f"Message {message_id} failed: {error}")
    return db_message


@dataclass
class ChatRunResult:
    """Result of a chat turn."""

    output_text: str
    new_messages: list[ModelMessage]


def _agent_output_text(result: object) -> str:
    """Return text output from the current pydantic-ai run result shape."""

    output = getattr(result, "output", None)
    if output is None:
        return ""
    if isinstance(output, str):
        return output
    return str(output)


def _serialize_render_metadata(
    render_metadata: ChatMessageRenderMetadata | dict[str, object] | None,
) -> dict[str, object] | None:
    """Normalize optional render metadata for DB storage."""

    if render_metadata is None:
        return None
    if isinstance(render_metadata, ChatMessageRenderMetadata):
        return render_metadata.model_dump(mode="json")
    return render_metadata


def _build_chat_deps(
    db: Session,
    session: ChatSession,
    include_full_text: bool = False,
    *,
    include_vm_tools: bool = True,
    user_prompt: str = "",
) -> ChatDeps:
    """Construct detached-safe chat dependencies for a session."""
    return _build_chat_deps_from_values(
        db,
        session_id=_require_session_id(session),
        user_id=_require_session_user_id(session),
        content_id=session.content_id,
        session_type=session.session_type,
        topic=session.topic,
        context_snapshot=session.context_snapshot,
        include_full_text=include_full_text,
        include_vm_tools=include_vm_tools,
        user_prompt=user_prompt,
    )


def _build_chat_deps_from_values(
    db: Session,
    *,
    session_id: int,
    user_id: int,
    content_id: int | None,
    session_type: str | None,
    topic: str | None,
    context_snapshot: str | None,
    include_full_text: bool = False,
    include_vm_tools: bool = True,
    llm_task_id: int | None = None,
    user_prompt: str = "",
) -> ChatDeps:
    """Construct detached-safe chat dependencies from explicit session fields."""
    content: Content | None = None
    article_context: str | None = None
    context_label = "Article Context"
    vm_runtime: LazyAgentVmRuntime | None = None

    if content_id:
        content = db.query(Content).filter(Content.id == content_id).first()

    use_live_content = content is not None and (
        session_type == KNOWLEDGE_SESSION_TYPE or not context_snapshot
    )
    if use_live_content and content is not None:
        max_system_article_tokens = min(
            int(CONTEXT_WINDOW_TOKENS * SYSTEM_AND_ARTICLE_BUDGET_RATIO),
            max(
                CONTEXT_WINDOW_TOKENS
                - CHAT_OUTPUT_RESERVE_TOKENS
                - CHAT_TOOL_SCHEMA_RESERVE_TOKENS
                - CHAT_HISTORY_MAX_TOKENS
                - _estimate_tokens(user_prompt),
                0,
            ),
        )
        system_tokens = _estimate_tokens(f"{SYSTEM_PROMPT_TEXT}\n\n{AGENT_VM_SYSTEM_INSTRUCTIONS}")
        header_text = "\n".join(_build_article_header(content, topic))
        header_tokens = _estimate_tokens(header_text)
        available_tokens = max(max_system_article_tokens - system_tokens - header_tokens, 0)
        article_context = build_article_context(
            db,
            content,
            include_full_text=include_full_text,
            max_tokens=available_tokens,
        )
    elif context_snapshot:
        max_snapshot_tokens = max(
            CONTEXT_WINDOW_TOKENS
            - CHAT_OUTPUT_RESERVE_TOKENS
            - CHAT_TOOL_SCHEMA_RESERVE_TOKENS
            - CHAT_HISTORY_MAX_TOKENS
            - _estimate_tokens(
                f"{SYSTEM_PROMPT_TEXT}\n{AGENT_VM_SYSTEM_INSTRUCTIONS}\n{user_prompt}"
            ),
            0,
        )
        article_context = _truncate_to_token_budget(context_snapshot, max_snapshot_tokens)
        context_label = "Session Context"

    if include_vm_tools:
        vm_runtime = _build_chat_vm_runtime(
            session_id=session_id,
            user_id=user_id,
            llm_task_id=llm_task_id,
        )

    system_context = "\n".join(
        _build_context_prompt_parts(
            content if use_live_content else None,
            topic,
            article_context,
            context_label,
        )
    )

    from app.core.db import get_session_factory

    return ChatDeps(
        session_id=session_id,
        user_id=user_id,
        content_id=content_id,
        has_content=use_live_content,
        article_context=article_context,
        system_context=system_context,
        vm_runtime=vm_runtime,
        session_factory=get_session_factory(),
    )


def _build_chat_vm_runtime(
    *,
    session_id: int,
    user_id: int,
    llm_task_id: int | None = None,
) -> LazyAgentVmRuntime | None:
    """Build a lazy handle without touching E2B or synchronizing user data."""
    settings = get_settings()
    if settings.llm_task_sandbox_provider == "disabled":
        return None
    return LazyAgentVmRuntime(
        user_id=user_id,
        session_id=session_id,
        llm_task_id=llm_task_id,
        feature="chat",
    )


def _sync_parent_session_activity(db: Session, session: ChatSession) -> None:
    """Mirror child-session activity onto a visible parent council session."""

    if not session.parent_session_id:
        return

    parent_session = (
        db.query(ChatSession).filter(ChatSession.id == session.parent_session_id).first()
    )
    if parent_session is None:
        return

    parent_session.updated_at = datetime.now(UTC)
    parent_session.last_message_at = session.last_message_at or datetime.now(UTC)


def _run_agent_sync(
    model_spec: str,
    user_prompt: str,
    deps: ChatDeps,
    history: list[ModelMessage],
    *,
    provider_api_key: str | None = None,
    partial_writer: _DurableChatPartialWriter | None = None,
):
    """Run the chat agent synchronously in a worker thread."""
    agent = get_chat_agent(model_spec, api_key_override=provider_api_key)
    event_stream_handler = (
        _build_final_text_event_stream_handler(partial_writer)
        if partial_writer is not None
        else None
    )
    return agent.run_sync(
        user_prompt,
        deps=deps,
        message_history=history,
        event_stream_handler=event_stream_handler,
        usage_limits=UsageLimits(request_limit=get_settings().llm_task_sandbox_request_limit),
    )


async def run_chat_turn(
    db: Session,
    session: ChatSession,
    user_prompt: str,
    *,
    source: str = "realtime",
    task_id: int | None = None,
) -> ChatRunResult:
    """Run a chat turn synchronously and persist messages.

    Args:
        db: Database session.
        session: Active chat session.
        user_prompt: User message text.
        source: Request source label (`realtime` or `queue`).
        task_id: Optional queue task identifier.
    """
    total_start = perf_counter()
    session_row_id = _require_session_id(session)
    session_usage_snapshot = _ChatUsageSnapshot.from_session(session)
    session_user_id = session_usage_snapshot.user_id
    model_spec = session_usage_snapshot.model
    session_content_id = session_usage_snapshot.content_id
    session_type = session_usage_snapshot.session_type
    provider = resolve_model_provider(model_spec)
    chat_llm_task = LlmTaskTurnTracker.create(
        db,
        user_id=session_user_id,
        spec=ARTICLE_CHAT_TURN_SPEC,
        input_json={
            "chat_session_id": session_row_id,
            "content_id": session_content_id,
            "source": source,
            "queue_task_id": task_id,
            "prompt_chars": len(user_prompt),
            "model": model_spec,
        },
    )
    chat_llm_task_id = chat_llm_task.task_id
    logger.info(
        "Chat turn started",
        extra=build_log_extra(
            component="chat",
            operation="run_chat_turn",
            event_name="chat.turn",
            status="started",
            session_id=session_row_id,
            user_id=session_user_id,
            content_id=session_content_id,
            source=source,
            context_data={
                "model": model_spec,
                "provider": provider,
                "llm_task_id": chat_llm_task_id,
                "session_type": session_type,
                "prompt_chars": len(user_prompt),
            },
        ),
    )

    deps_start = perf_counter()
    deps = _build_chat_deps(
        db,
        session,
        include_full_text=True,
        user_prompt=user_prompt,
    )
    provider_api_key = resolve_effective_api_key(
        db=db,
        user_id=session_user_id,
        model_spec=model_spec,
    )
    deps_ms = (perf_counter() - deps_start) * 1000
    logger.info(
        "Chat context built",
        extra=build_log_extra(
            component="chat",
            operation="build_context",
            event_name="chat.turn.context_built",
            status="completed",
            duration_ms=deps_ms,
            session_id=session_row_id,
            user_id=session_user_id,
            content_id=session_content_id,
            context_data={"context_chars": len(deps.article_context or "")},
        ),
    )

    history_start = perf_counter()
    history = load_message_history(
        db,
        session_row_id,
        max_tokens=available_chat_history_tokens(
            static_system_prompt=f"{SYSTEM_PROMPT_TEXT}\n{AGENT_VM_SYSTEM_INSTRUCTIONS}",
            dynamic_system_prompt=deps.system_context,
            user_prompt=user_prompt,
        ),
    )
    history_ms = (perf_counter() - history_start) * 1000
    logger.info(
        "Chat history loaded",
        extra=build_log_extra(
            component="chat",
            operation="load_history",
            event_name="chat.turn.history_loaded",
            status="completed",
            duration_ms=history_ms,
            session_id=session_row_id,
            user_id=session_user_id,
            context_data={"history_count": len(history)},
        ),
    )

    try:
        logger.info(
            "Chat LLM call started",
            extra=build_log_extra(
                component="chat",
                operation="llm_call",
                event_name="chat.turn.llm_started",
                status="started",
                session_id=session_row_id,
                user_id=session_user_id,
                content_id=session_content_id,
                source=source,
                context_data={"model": model_spec},
            ),
        )
        chat_llm_task.running(
            db,
            note="Running article chat agent",
            model_provider=provider,
            model_name=model_spec,
        )
        agent_start = perf_counter()
        result = await run_in_threadpool(
            _run_agent_sync,
            model_spec,
            user_prompt,
            deps,
            history,
            provider_api_key=provider_api_key,
        )
        agent_ms = (perf_counter() - agent_start) * 1000
        _log_chat_usage(result, session_usage_snapshot, session_row_id, None, "sync")
        output_text = _agent_output_text(result)
        new_messages = result.new_messages()
        save_messages(
            db,
            session_row_id,
            new_messages,
            display_user_prompt=user_prompt,
            commit=False,
        )
        enqueue_agent_data_sync(
            db,
            user_id=session_user_id,
            chat_session_ids=(session_row_id,),
        )

        session.last_message_at = datetime.now(UTC)
        session.updated_at = datetime.now(UTC)
        _sync_parent_session_activity(db, session)
        db.commit()

        total_ms = (perf_counter() - total_start) * 1000
        tool_names = _extract_tool_names(result)
        chat_llm_task.completed(
            db,
            note="Article chat turn completed",
            output_json={
                "chat_session_id": session_row_id,
                "content_id": session_content_id,
                "output_chars": len(output_text),
                "new_message_count": len(new_messages),
                "tool_names": tool_names,
            },
            model_provider=provider,
            model_name=model_spec,
        )
        logger.info(
            "Chat turn completed",
            extra=build_log_extra(
                component="chat",
                operation="run_chat_turn",
                event_name="chat.turn",
                status="completed",
                duration_ms=total_ms,
                session_id=session_row_id,
                user_id=session_user_id,
                content_id=session_content_id,
                source=source,
                context_data={
                    "model": model_spec,
                    "llm_task_id": chat_llm_task_id,
                    "deps_ms": round(deps_ms, 2),
                    "history_ms": round(history_ms, 2),
                    "agent_ms": round(agent_ms, 2),
                    "tool_names": tool_names,
                    "tool_count": len(tool_names),
                },
            ),
        )

        return ChatRunResult(
            output_text=output_text,
            new_messages=new_messages,
        )
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        chat_llm_task.failed(
            db,
            note="Article chat turn failed",
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        logger.exception(
            "Chat turn failed",
            extra=build_log_extra(
                component="chat",
                operation="run_chat_turn",
                event_name="chat.turn",
                status="failed",
                duration_ms=(perf_counter() - total_start) * 1000,
                session_id=session_row_id,
                user_id=session_user_id,
                content_id=session.content_id,
                source=source,
                context_data={
                    "failure_class": type(exc).__name__,
                    "llm_task_id": chat_llm_task_id,
                },
            ),
        )
        raise
    finally:
        _close_agent_vm_runtime(deps.vm_runtime)


@dataclass(frozen=True)
class _PreparedArticleChatTurn:
    deps: ChatDeps
    history: list[ModelMessage]
    provider_api_key: str | None
    deps_ms: float
    history_ms: float


@dataclass(frozen=True)
class _ExecutedArticleChatTurn:
    raw_result: object
    output_text: str
    new_messages: list[ModelMessage]
    tool_names: list[str]


def _prepare_article_background_turn(
    db: Session,
    session: ChatTurnSessionSnapshot,
    turn: _DetachedChatTurn,
    *,
    user_prompt: str,
) -> _PreparedArticleChatTurn:
    del db
    from app.core.db import get_session_factory

    session_factory = get_session_factory()

    def build_deps() -> tuple[ChatDeps, float]:
        started_at = perf_counter()
        with session_factory() as prepare_db:
            value = _build_chat_deps_from_values(
                prepare_db,
                session_id=session.effective_session_id,
                user_id=session.user_id,
                content_id=session.content_id,
                session_type=session.session_type,
                topic=session.topic,
                context_snapshot=session.context_snapshot,
                include_full_text=True,
                llm_task_id=turn.llm_task_id,
                user_prompt=user_prompt,
            )
        return value, (perf_counter() - started_at) * 1000

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

    def resolve_key() -> str | None:
        with session_factory() as key_db:
            return resolve_effective_api_key(
                db=key_db,
                user_id=turn.user_id,
                model_spec=turn.model,
            )

    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="chat-prepare") as executor:
        deps_future = executor.submit(build_deps)
        history_future = executor.submit(build_history)
        key_future = executor.submit(resolve_key)
        deps, deps_ms = deps_future.result()
        history, history_ms = history_future.result()
        provider_api_key = key_future.result()
    history = trim_message_history_to_token_budget(
        history,
        max_tokens=available_chat_history_tokens(
            static_system_prompt=f"{SYSTEM_PROMPT_TEXT}\n{AGENT_VM_SYSTEM_INSTRUCTIONS}",
            dynamic_system_prompt=deps.system_context,
            user_prompt=user_prompt,
        ),
    )

    logger.info(
        "Async chat context built",
        extra=build_log_extra(
            component="chat",
            operation="build_context",
            event_name="chat.turn.context_built",
            status="completed",
            duration_ms=deps_ms,
            session_id=turn.session_id,
            message_id=turn.message_id,
            user_id=turn.user_id,
            content_id=turn.content_id,
            source=turn.source,
            context_data={
                "context_chars": len(deps.article_context or ""),
                "has_content": deps.has_content,
                "sandbox_acquired": bool(deps.vm_runtime and deps.vm_runtime.acquired),
                "sandbox_acquisition_ms": 0.0,
                "sandbox_hydration_ms": 0.0,
                "llm_task_id": turn.llm_task_id,
            },
        ),
    )

    logger.info(
        "Async chat history loaded",
        extra=build_log_extra(
            component="chat",
            operation="load_history",
            event_name="chat.turn.history_loaded",
            status="completed",
            duration_ms=history_ms,
            session_id=turn.session_id,
            message_id=turn.message_id,
            user_id=turn.user_id,
            context_data={
                "history_count": len(history),
                "llm_task_id": turn.llm_task_id,
            },
        ),
    )
    return _PreparedArticleChatTurn(
        deps=deps,
        history=history,
        provider_api_key=provider_api_key,
        deps_ms=deps_ms,
        history_ms=history_ms,
    )


async def _execute_article_background_turn(
    prepared: _PreparedArticleChatTurn,
    turn: _DetachedChatTurn,
    *,
    user_prompt: str,
    partial_writer: _DurableChatPartialWriter,
) -> _ExecutedArticleChatTurn:
    if turn.message_id is not None:
        from app.core.db import get_session_factory

        prepared.deps.tool_progress_writer = DurableChatToolProgressWriter(
            session_factory=get_session_factory(),
            message_id=turn.message_id,
            stream_generation=turn.stream_generation,
        )
    logger.info(
        "Async chat LLM call started",
        extra=build_log_extra(
            component="chat",
            operation="llm_call",
            event_name="chat.turn.llm_started",
            status="started",
            session_id=turn.session_id,
            message_id=turn.message_id,
            user_id=turn.user_id,
            content_id=turn.content_id,
            source=turn.source,
            context_data={
                "model": turn.model,
                "history_count": len(prepared.history),
                "llm_task_id": turn.llm_task_id,
            },
        ),
    )
    agent_start = perf_counter()
    result = await run_in_threadpool(
        _run_agent_sync,
        turn.model,
        user_prompt,
        prepared.deps,
        prepared.history,
        provider_api_key=prepared.provider_api_key,
        partial_writer=partial_writer,
    )
    agent_ms = (perf_counter() - agent_start) * 1000
    output_text = _agent_output_text(result)
    new_messages = result.new_messages()
    tool_names = _extract_tool_names(result)
    logger.info(
        "Async chat LLM call completed",
        extra=build_log_extra(
            component="chat",
            operation="llm_call",
            event_name="chat.turn.llm_completed",
            status="completed",
            duration_ms=agent_ms,
            session_id=turn.session_id,
            message_id=turn.message_id,
            user_id=turn.user_id,
            content_id=turn.content_id,
            source=turn.source,
            context_data={
                "tool_names": tool_names,
                "tool_count": len(tool_names),
                "output_chars": len(output_text),
                "llm_task_id": turn.llm_task_id,
            },
        ),
    )
    return _ExecutedArticleChatTurn(
        raw_result=result,
        output_text=output_text,
        new_messages=new_messages,
        tool_names=tool_names,
    )


def _persist_article_background_turn(
    db: Session,
    executed: _ExecutedArticleChatTurn,
    turn: _DetachedChatTurn,
    *,
    user_prompt: str,
) -> dict[str, object]:
    if turn.message_id is None:
        raise ValueError("Article chat turn is missing a message id")
    update_message_completed(
        db,
        turn.message_id,
        executed.new_messages,
        display_user_prompt=user_prompt,
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
        "output_chars": len(executed.output_text),
        "new_message_count": len(executed.new_messages),
        "tool_names": executed.tool_names,
    }


def _stage_message_failed(
    db: Session,
    message_id: int,
    error: str,
    stream_generation: int,
) -> ChatMessage:
    return update_message_failed(
        db,
        message_id,
        error,
        expected_stream_generation=stream_generation,
        commit=False,
    )


async def process_message_async(
    session_id: int,
    message_id: int,
    user_prompt: str,
    *,
    turn_context: ChatTurnProcessingContext,
    stream_generation: int,
    ensure_lease: Callable[[], bool],
    source: str = "realtime",
    task_id: int | None = None,
) -> QueuedChatTurnOutcome:
    """Process one article-chat message without retaining a DB session across I/O."""
    from app.core.db import get_session_factory

    total_start = perf_counter()
    session_factory = get_session_factory()
    lifecycle = ARTICLE_BACKGROUND_TURN_LIFECYCLE
    logger.info(
        "Async chat turn started",
        extra=build_log_extra(
            component="chat",
            operation="process_message_async",
            event_name="chat.turn",
            status="started",
            session_id=session_id,
            message_id=message_id,
            source=source,
            context_data={"prompt_chars": len(user_prompt)},
        ),
    )

    session_snapshot = turn_context.session
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
            "source": turn.source,
            "queue_task_id": turn.task_id,
            "prompt_chars": len(user_prompt),
            "model": turn.model,
            "stream_generation": turn.stream_generation,
        },
        prepare=lambda db, turn: _prepare_article_background_turn(
            db,
            session_snapshot,
            turn,
            user_prompt=user_prompt,
        ),
        execute=lambda prepared, turn, partial_writer: _execute_article_background_turn(
            prepared,
            turn,
            user_prompt=user_prompt,
            partial_writer=partial_writer,
        ),
        persist=lambda db, executed, turn: _persist_article_background_turn(
            db,
            executed,
            turn,
            user_prompt=user_prompt,
        ),
        mark_message_failed=_stage_message_failed,
        raw_result=lambda executed: executed.raw_result,
        record_usage=_log_chat_usage,
        ensure_lease=ensure_lease,
        cleanup=lambda prepared: _close_agent_vm_runtime(prepared.deps.vm_runtime),
        after_persist=_sync_parent_session_activity,
    )
    if result.outcome != QueuedChatTurnOutcome.COMPLETED:
        return result.outcome

    turn = result.turn
    prepared = result.prepared
    if turn is None or prepared is None:
        raise RuntimeError("Article chat turn completed without runtime state")

    logger.info(
        "Async chat turn persisted",
        extra=build_log_extra(
            component="chat",
            operation="process_message_async",
            event_name="chat.turn.persisted",
            status="completed",
            duration_ms=(perf_counter() - total_start) * 1000,
            session_id=turn.session_id,
            message_id=turn.message_id,
            user_id=turn.user_id,
            content_id=turn.content_id,
            source=turn.source,
            context_data={
                "model": turn.model,
                "deps_ms": round(prepared.deps_ms, 2),
                "history_ms": round(prepared.history_ms, 2),
                "agent_ms": round(result.external_ms, 2),
                "save_ms": round(result.persistence_ms, 2),
                "partial_write_count": result.partial_write_count,
                "first_partial_ms": result.first_partial_ms,
                "llm_task_id": turn.llm_task_id,
            },
        ),
    )
    return result.outcome


INITIAL_QUESTIONS_PROMPT = load_prompt("chat/article#initial_questions_user")
INITIAL_SUGGESTIONS_TURN_LIFECYCLE = _DetachedChatTurnLifecycle(
    task_spec=ARTICLE_CHAT_TURN_SPEC,
    running_note="Running initial article chat suggestions",
    completed_note="Initial article chat suggestions completed",
    failed_note="Initial article chat suggestions failed",
    usage_context="initial_suggestions",
)


@dataclass(frozen=True)
class _PreparedInitialSuggestionsTurn:
    deps: ChatDeps
    provider_api_key: str | None
    deps_ms: float


@dataclass(frozen=True)
class _ExecutedInitialSuggestionsTurn:
    raw_result: object
    chat_result: ChatRunResult
    tool_names: list[str]


def _prepare_initial_suggestions_turn(
    db: Session,
    session: ChatSession,
    turn: _DetachedChatTurn,
) -> _PreparedInitialSuggestionsTurn:
    deps_start = perf_counter()
    deps = _build_chat_deps(
        db,
        session,
        include_full_text=True,
        include_vm_tools=False,
    )
    provider_api_key = resolve_effective_api_key(
        db=db,
        user_id=turn.user_id,
        model_spec=turn.model,
    )
    return _PreparedInitialSuggestionsTurn(
        deps=deps,
        provider_api_key=provider_api_key,
        deps_ms=(perf_counter() - deps_start) * 1000,
    )


async def _execute_initial_suggestions_turn(
    prepared: _PreparedInitialSuggestionsTurn,
    turn: _DetachedChatTurn,
) -> _ExecutedInitialSuggestionsTurn:
    result = await run_in_threadpool(
        _run_agent_sync,
        turn.model,
        INITIAL_QUESTIONS_PROMPT,
        prepared.deps,
        [],
        provider_api_key=prepared.provider_api_key,
    )
    output_text = _agent_output_text(result)
    from pydantic_ai.messages import ModelResponse, TextPart

    new_messages: list[ModelMessage] = [ModelResponse(parts=[TextPart(content=output_text)])]
    tool_names = _extract_tool_names(result)
    return _ExecutedInitialSuggestionsTurn(
        raw_result=result,
        chat_result=ChatRunResult(
            output_text=output_text,
            new_messages=new_messages,
        ),
        tool_names=tool_names,
    )


def _persist_initial_suggestions_turn(
    db: Session,
    executed: _ExecutedInitialSuggestionsTurn,
    turn: _DetachedChatTurn,
) -> dict[str, object]:
    message = save_messages(
        db,
        turn.session_id,
        executed.chat_result.new_messages,
        commit=False,
    )
    if message.id is None:
        raise ValueError("Initial suggestions message is missing an id")
    enqueue_agent_data_sync(
        db,
        user_id=turn.user_id,
        chat_session_ids=(turn.session_id,),
    )
    return {
        "chat_session_id": turn.session_id,
        "message_id": int(message.id),
        "content_id": turn.content_id,
        "output_chars": len(executed.chat_result.output_text),
        "new_message_count": len(executed.chat_result.new_messages),
        "tool_names": executed.tool_names,
    }


async def generate_initial_suggestions(
    session_id: int,
    *,
    source: str = "realtime",
    task_id: int | None = None,
) -> ChatRunResult | None:
    """Generate initial suggestions while owning every DB session it opens."""
    from app.core.db import get_session_factory

    total_start = perf_counter()
    session_factory = get_session_factory()
    lifecycle = INITIAL_SUGGESTIONS_TURN_LIFECYCLE
    tracker = LlmTaskTurnTracker(task_id=None)
    turn: _DetachedChatTurn | None = None
    prepared: _PreparedInitialSuggestionsTurn | None = None

    try:
        with session_factory() as prepare_db:
            session = prepare_db.query(ChatSession).filter(ChatSession.id == session_id).first()
            if session is None:
                logger.error("Initial suggestions session %s not found", session_id)
                return None

            turn = _snapshot_detached_chat_turn(
                session,
                message_id=None,
                source=source,
                task_id=task_id,
            )
            logger.info(
                "Initial suggestions started",
                extra=build_log_extra(
                    component="chat",
                    operation="generate_initial_suggestions",
                    event_name="chat.turn",
                    status="started",
                    session_id=turn.session_id,
                    user_id=turn.user_id,
                    content_id=turn.content_id,
                    source=source,
                    context_data={"model": turn.model, "session_type": turn.session_type},
                ),
            )

            if not turn.content_id and not session.context_snapshot:
                logger.warning(
                    "Initial suggestions skipped because session has no context",
                    extra=build_log_extra(
                        component="chat",
                        operation="generate_initial_suggestions",
                        event_name="chat.turn",
                        status="skipped",
                        session_id=turn.session_id,
                        user_id=turn.user_id,
                        source=source,
                    ),
                )
                return None

            turn, tracker = _start_detached_chat_turn(
                prepare_db,
                turn=turn,
                lifecycle=lifecycle,
                input_json={
                    "operation": "initial_suggestions",
                    "chat_session_id": turn.session_id,
                    "content_id": turn.content_id,
                    "source": turn.source,
                    "queue_task_id": turn.task_id,
                    "prompt_chars": len(INITIAL_QUESTIONS_PROMPT),
                    "model": turn.model,
                },
            )
            prepared = _prepare_initial_suggestions_turn(prepare_db, session, turn)
            _mark_detached_chat_turn_running(
                prepare_db,
                turn=turn,
                tracker=tracker,
                lifecycle=lifecycle,
            )

        external_start = perf_counter()
        executed = await _execute_initial_suggestions_turn(prepared, turn)
        external_ms = (perf_counter() - external_start) * 1000
        _log_chat_usage(
            executed.raw_result,
            turn.usage_snapshot,
            turn.session_id,
            turn.message_id,
            lifecycle.usage_context,
        )

        persistence_start = perf_counter()
        with session_factory() as persist_db:
            persisted_session = (
                persist_db.query(ChatSession).filter(ChatSession.id == turn.session_id).first()
            )
            if persisted_session is None:
                raise RuntimeError(f"Chat session {turn.session_id} disappeared before persistence")
            output_json = _persist_initial_suggestions_turn(
                persist_db,
                executed,
                turn,
            )
            _complete_detached_chat_turn(
                persist_db,
                session=persisted_session,
                turn=turn,
                tracker=tracker,
                lifecycle=lifecycle,
                output_json=output_json,
            )
        persistence_ms = (perf_counter() - persistence_start) * 1000
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Initial suggestions failed",
            extra=build_log_extra(
                component="chat",
                operation="generate_initial_suggestions",
                event_name="chat.turn.failed",
                status="failed",
                duration_ms=(perf_counter() - total_start) * 1000,
                session_id=session_id,
                user_id=turn.user_id if turn is not None else None,
                content_id=turn.content_id if turn is not None else None,
                source=source,
                context_data={
                    "failure_class": type(exc).__name__,
                    "llm_task_id": turn.llm_task_id if turn is not None else None,
                },
            ),
        )
        _persist_detached_turn_failure(
            session_factory=session_factory,
            tracker=tracker,
            lifecycle=lifecycle,
            message_id=None,
            error=exc,
            mark_message_failed=None,
        )
        raise
    finally:
        if prepared is not None:
            _close_agent_vm_runtime(prepared.deps.vm_runtime)

    if turn is None or prepared is None:
        raise RuntimeError("Initial suggestions completed without runtime state")

    logger.info(
        "Initial suggestions persisted",
        extra=build_log_extra(
            component="chat",
            operation="generate_initial_suggestions",
            event_name="chat.turn.persisted",
            status="completed",
            duration_ms=(perf_counter() - total_start) * 1000,
            session_id=turn.session_id,
            user_id=turn.user_id,
            content_id=turn.content_id,
            source=turn.source,
            context_data={
                "model": turn.model,
                "deps_ms": round(prepared.deps_ms, 2),
                "agent_ms": round(external_ms, 2),
                "save_ms": round(persistence_ms, 2),
                "tool_names": executed.tool_names,
                "tool_count": len(executed.tool_names),
                "llm_task_id": turn.llm_task_id,
            },
        ),
    )
    return executed.chat_result
