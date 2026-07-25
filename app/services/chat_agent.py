"""Chat agent service using pydantic-ai for deep-dive conversations."""

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter

from fastapi.concurrency import run_in_threadpool
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter
from pydantic_ai.models.openai import ReasoningEffort
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
from app.services.chat_turn_runtime import (
    ChatUsageSnapshot as _ChatUsageSnapshot,
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
from app.services.exa_client import exa_search, get_exa_client
from app.services.langfuse_tracing import langfuse_trace_context
from app.services.llm_models import (  # noqa: F401 (re-export for API schemas)
    LLMProvider as ChatModelProvider,
)
from app.services.llm_models import (
    build_pydantic_model,
    resolve_effective_api_key,
    resolve_model_provider,
)
from app.services.llm_task_turn_tracker import LlmTaskTurnSpec, LlmTaskTurnTracker
from app.services.personal_markdown_library import sync_personal_markdown_library_for_user
from app.services.prompt_library import load_prompt, render_prompt
from app.services.sandbox_runtime import (
    PersonalLibrarySandboxSession,
    SandboxRuntimeUnavailableError,
    create_personal_library_sandbox_session,
)

logger = get_logger(__name__)

CHAT_OPENAI_REASONING_EFFORT: ReasoningEffort = "low"
CONTEXT_WINDOW_TOKENS = 200_000
SYSTEM_AND_ARTICLE_BUDGET_RATIO = 0.75
TOKEN_CHARS_PER_TOKEN = 4

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
        "personal_library": "read_only",
    },
    prompt_pack="chat.article",
)


def _estimate_tokens(text: str | None) -> int:
    """Approximate token count using character length."""
    if not text:
        return 0
    return max(1, math.ceil(len(text) / TOKEN_CHARS_PER_TOKEN))


def _truncate_to_token_budget(text: str, max_tokens: int) -> str:
    """Truncate text to an approximate token budget."""
    if max_tokens <= 0:
        return ""
    max_chars = max_tokens * TOKEN_CHARS_PER_TOKEN
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."


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
    """Dependencies passed to the chat agent."""

    session: ChatSession
    content: Content | None
    article_context: str | None  # Pre-built context string from article/session snapshot
    context_label: str = "Article Context"
    sandbox_session: PersonalLibrarySandboxSession | None = None
    personal_library_error: str | None = None


def _build_article_header(content: Content | None, session: ChatSession) -> list[str]:
    parts: list[str] = []
    if content:
        parts.append(f"Article Title: {content.title or 'Untitled'}")
        parts.append(f"Source: {content.source or 'Unknown'}")
        parts.append(f"URL: {content.url}")
    if session.topic:
        parts.append(f"\nFocus Topic: {session.topic}")
    return parts


def _build_context_prompt_parts(
    content: Content | None,
    session: ChatSession,
    article_context: str | None,
    context_label: str,
) -> list[str]:
    """Build dynamic prompt sections that expose reference context to the model."""
    parts = _build_article_header(content, session)

    if article_context:
        parts.append(f"\n{load_prompt('chat/article#context_notice')}")
        parts.append(f"\n{context_label}:\n{article_context}")

    return parts


def _build_run_user_prompt(user_prompt: str, deps: ChatDeps) -> str:
    """Build the model-facing user prompt for a chat turn."""
    if deps.session.context_snapshot and deps.article_context:
        return render_prompt(
            "chat/article#run_with_context_user",
            context_label=deps.context_label,
            article_context=deps.article_context,
            user_prompt=user_prompt,
        )
    return user_prompt


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
        system_prompt=SYSTEM_PROMPT_TEXT,
        model_settings=model_settings,
    )

    @agent.system_prompt
    def add_article_context(ctx: RunContext[ChatDeps]) -> str:
        """Add article context to the system prompt."""
        parts = _build_context_prompt_parts(
            ctx.deps.content,
            ctx.deps.session,
            ctx.deps.article_context,
            ctx.deps.context_label,
        )
        if parts:
            return "\n".join(parts)
        return ""

    @agent.tool
    def execute_bash(
        ctx: RunContext[ChatDeps],
        command: str,
        timeout_seconds: int | None = None,
    ) -> dict[str, object]:
        """Run a bash command in the chat sandbox for additional investigation."""
        sandbox_session = ctx.deps.sandbox_session
        if sandbox_session is None:
            return {
                "ok": False,
                "error": ctx.deps.personal_library_error or "Chat sandbox is unavailable.",
            }
        bounded_timeout = min(max(timeout_seconds or 60, 1), 300)
        try:
            result = sandbox_session.execute_bash(
                command,
                timeout_seconds=bounded_timeout,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Chat sandbox command failed",
                extra=build_log_extra(
                    component="chat",
                    operation="execute_bash",
                    event_name="chat.tool.execute_bash",
                    status="failed",
                    session_id=ctx.deps.session.id,
                    user_id=ctx.deps.session.user_id,
                    context_data={"failure_class": type(exc).__name__},
                ),
            )
            return {
                "ok": False,
                "error": "Sandbox command failed.",
                "failure_class": type(exc).__name__,
            }
        return {
            "ok": result.exit_code == 0,
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    @agent.tool
    def search_personal_library(
        ctx: RunContext[ChatDeps],
        query: str,
        limit: int = 20,
        glob: str = "*.md",
    ) -> str:
        """Search the user's personal markdown library."""
        sandbox_session = ctx.deps.sandbox_session
        if sandbox_session is None:
            return _personal_library_unavailable_message(ctx.deps.personal_library_error)

        normalized_limit = max(1, min(limit, 50))
        return sandbox_session.search_files(
            query=query,
            glob=glob,
            limit=normalized_limit,
        )

    @agent.tool
    def list_personal_library(
        ctx: RunContext[ChatDeps],
        subpath: str = "",
        limit: int = 200,
    ) -> str:
        """List markdown files in the user's personal markdown library."""
        sandbox_session = ctx.deps.sandbox_session
        if sandbox_session is None:
            return _personal_library_unavailable_message(ctx.deps.personal_library_error)

        normalized_limit = max(1, min(limit, 500))
        return sandbox_session.list_files(
            subpath=subpath,
            limit=normalized_limit,
        )

    @agent.tool
    def read_personal_markdown_file(
        ctx: RunContext[ChatDeps],
        relative_path: str,
        max_chars: int = 12_000,
    ) -> str:
        """Read one markdown file from the user's personal markdown library."""
        sandbox_session = ctx.deps.sandbox_session
        if sandbox_session is None:
            return _personal_library_unavailable_message(ctx.deps.personal_library_error)

        normalized_max_chars = max(500, min(max_chars, 40_000))
        return sandbox_session.read_file(
            relative_path=relative_path,
            max_chars=normalized_max_chars,
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
        session_id = ctx.deps.session.id
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
                    "user_id": ctx.deps.session.user_id,
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


def load_message_history(
    db: Session,
    session_id: int,
    *,
    exclude_message_id: int | None = None,
    completed_only: bool = True,
) -> list[ModelMessage]:
    """Load model history for a chat session from the database.

    Args:
        db: Database session.
        session_id: Chat session ID.
        exclude_message_id: Optional active turn row to omit from history.
        completed_only: When true, ignore processing/failed rows so placeholders
            and failed partial turns do not become model context.

    Returns:
        List of ModelMessage objects in chronological order.
    """
    messages: list[ModelMessage] = []

    # Query chat_messages ordered by created_at
    query = db.query(ChatMessage).filter(ChatMessage.session_id == session_id)
    if exclude_message_id is not None:
        query = query.filter(ChatMessage.id != exclude_message_id)
    if completed_only:
        query = query.filter(ChatMessage.status == MessageProcessingStatus.COMPLETED.value)
    db_messages = query.order_by(ChatMessage.created_at).all()

    for db_msg in db_messages:
        try:
            # Deserialize JSON to list of ModelMessage
            message_list_json = db_msg.message_list
            if not isinstance(message_list_json, str):
                continue
            msg_list = ModelMessagesTypeAdapter.validate_json(message_list_json)
            messages.extend(msg_list)
        except Exception as e:
            logger.warning(f"Failed to deserialize message {db_msg.id}: {e}")
            continue

    return messages


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
) -> ChatMessage:
    """Save new messages to the database.

    Args:
        db: Database session.
        session_id: Chat session ID.
        messages: List of ModelMessage objects to save.
        status: Processing status for the message.
        display_user_prompt: Optional user-visible prompt text to persist
            instead of the model-facing request content.

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
    return save_messages(db, session_id, [user_message], status=MessageProcessingStatus.PROCESSING)


def update_message_completed(
    db: Session,
    message_id: int,
    messages: list[ModelMessage],
    *,
    display_user_prompt: str | None = None,
    render_metadata: ChatMessageRenderMetadata | dict[str, object] | None = None,
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
    db_message = db.query(ChatMessage).filter(ChatMessage.id == message_id).first()
    if not db_message:
        raise ValueError(f"Message {message_id} not found")

    message_json = _dump_messages_json(
        messages,
        display_user_prompt=display_user_prompt,
    )
    db_message.message_list = message_json
    db_message.render_metadata = _serialize_render_metadata(render_metadata)
    db_message.status = MessageProcessingStatus.COMPLETED.value
    db.commit()
    db.refresh(db_message)
    logger.debug(f"Updated message {message_id} to completed")
    return db_message


def update_message_failed(
    db: Session,
    message_id: int,
    error: str,
) -> ChatMessage:
    """Mark a processing message as failed.

    Args:
        db: Database session.
        message_id: ChatMessage ID to update.
        error: Error message describing the failure.

    Returns:
        The updated ChatMessage record.
    """
    db_message = db.query(ChatMessage).filter(ChatMessage.id == message_id).first()
    if not db_message:
        raise ValueError(f"Message {message_id} not found")

    db_message.status = MessageProcessingStatus.FAILED.value
    db_message.render_metadata = None
    db_message.error = error
    db.commit()
    db.refresh(db_message)
    logger.warning(f"Message {message_id} failed: {error}")
    return db_message


@dataclass
class ChatRunResult:
    """Result of a chat turn."""

    output_text: str
    new_messages: list[ModelMessage]
    all_messages: list[ModelMessage]
    tool_calls: list[object]


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
    include_library_tools: bool = True,
) -> ChatDeps:
    """Construct chat dependencies (content + context) for a session."""
    content: Content | None = None
    article_context: str | None = None
    context_label = "Article Context"
    sandbox_session: PersonalLibrarySandboxSession | None = None
    personal_library_error: str | None = None

    if session.content_id:
        content = db.query(Content).filter(Content.id == session.content_id).first()

    use_live_content = content is not None and (
        session.session_type == KNOWLEDGE_SESSION_TYPE or not session.context_snapshot
    )
    if use_live_content and content is not None:
        max_system_article_tokens = int(CONTEXT_WINDOW_TOKENS * SYSTEM_AND_ARTICLE_BUDGET_RATIO)
        system_tokens = _estimate_tokens(SYSTEM_PROMPT_TEXT)
        header_text = "\n".join(_build_article_header(content, session))
        header_tokens = _estimate_tokens(header_text)
        available_tokens = max(max_system_article_tokens - system_tokens - header_tokens, 0)
        article_context = build_article_context(
            db,
            content,
            include_full_text=include_full_text,
            max_tokens=available_tokens,
        )
    elif session.context_snapshot:
        article_context = session.context_snapshot
        context_label = "Session Context"

    if include_library_tools:
        sandbox_session, personal_library_error = _build_personal_library_runtime(db, session)

    return ChatDeps(
        session=session,
        content=content if use_live_content else None,
        article_context=article_context,
        context_label=context_label,
        sandbox_session=sandbox_session,
        personal_library_error=personal_library_error,
    )


def _build_personal_library_runtime(
    db: Session,
    session: ChatSession,
) -> tuple[PersonalLibrarySandboxSession | None, str | None]:
    """Synchronize and hydrate the personal markdown library for a chat turn."""
    session_id = _require_session_id(session)
    user_id = _require_session_user_id(session)
    settings = get_settings()
    if settings.chat_sandbox_provider == "disabled":
        return None, None

    try:
        if settings.personal_markdown_enabled:
            sync_personal_markdown_library_for_user(db, user_id=user_id)
        sandbox_session = create_personal_library_sandbox_session(user_id=user_id)
        return sandbox_session, None
    except SandboxRuntimeUnavailableError as exc:
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to prepare personal markdown library",
            extra=build_log_extra(
                component="chat",
                operation="build_personal_library_runtime",
                event_name="chat.turn.personal_library",
                status="degraded",
                session_id=session_id,
                user_id=user_id,
                content_id=session.content_id,
                context_data={"failure_class": type(exc).__name__},
            ),
        )
        return None, str(exc)


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
    trace_name: str,
    source: str,
    task_id: int | None = None,
    message_id: int | None = None,
    provider_api_key: str | None = None,
):
    """Run the chat agent synchronously in a worker thread."""
    agent = get_chat_agent(model_spec, api_key_override=provider_api_key)
    model_user_prompt = _build_run_user_prompt(user_prompt, deps)
    metadata = {
        "source": source,
        "model_spec": model_spec,
        "content_id": deps.session.content_id,
        "task_id": task_id,
        "message_id": message_id,
    }
    tags = ["chat", source]
    with langfuse_trace_context(
        trace_name=trace_name,
        user_id=deps.session.user_id,
        session_id=deps.session.id,
        metadata=metadata,
        tags=tags,
    ):
        return agent.run_sync(model_user_prompt, deps=deps, message_history=history)


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

    history_start = perf_counter()
    history = load_message_history(db, session_row_id)
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
    include_full_text = True

    deps_start = perf_counter()
    deps = _build_chat_deps(
        db,
        session,
        include_full_text=include_full_text,
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
            trace_name="chat.turn.sync",
            source=source,
            task_id=task_id,
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
        )

        session.last_message_at = datetime.now(UTC)
        session.updated_at = datetime.now(UTC)
        _sync_parent_session_activity(db, session)
        db.commit()

        total_ms = (perf_counter() - total_start) * 1000
        tool_calls = getattr(result, "tool_calls", []) or []
        tool_names = [
            getattr(tc, "name", None)
            or getattr(tc, "function_name", None)
            or getattr(tc, "tool_name", None)
            for tc in tool_calls
        ]
        chat_llm_task.completed(
            db,
            note="Article chat turn completed",
            output_json={
                "chat_session_id": session_row_id,
                "content_id": session_content_id,
                "output_chars": len(output_text),
                "new_message_count": len(new_messages),
                "tool_names": [name for name in tool_names if name],
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
                    "tool_count": len([name for name in tool_names if name]),
                },
            ),
        )

        return ChatRunResult(
            output_text=output_text,
            new_messages=new_messages,
            all_messages=result.all_messages,
            tool_calls=getattr(result, "tool_calls", []),
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
        _close_sandbox_session(deps.sandbox_session)


async def process_message_async(
    session_id: int,
    message_id: int,
    user_prompt: str,
    *,
    source: str = "realtime",
    task_id: int | None = None,
) -> None:
    """Process a chat message asynchronously in the background.

    This function runs independently after the endpoint returns.
    It gets a fresh DB session, processes the LLM call, and updates
    the message record with the result.

    Args:
        session_id: Chat session ID.
        message_id: ChatMessage ID to update on completion.
        user_prompt: The user's message text.
        source: Request source label (`realtime` or `queue`).
        task_id: Optional queue task identifier.
    """
    from app.core.db import get_session_factory

    total_start = perf_counter()
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

    SessionLocal = get_session_factory()
    db: Session | None = SessionLocal()
    deps: ChatDeps | None = None
    chat_llm_task = LlmTaskTurnTracker(task_id=None)
    chat_llm_task_id: int | None = None
    try:
        if db is None:
            raise RuntimeError("Database session was not initialized")
        session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
        if not session:
            logger.error("[AsyncChat:ERROR] Session %s not found", session_id)
            return
        session_row_id = _require_session_id(session)
        session_usage_snapshot = _ChatUsageSnapshot.from_session(session)
        session_user_id = session_usage_snapshot.user_id
        model_spec = session_usage_snapshot.model
        session_content_id = session_usage_snapshot.content_id
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

        include_full_text = True

        # Build dependencies
        deps_start = perf_counter()
        deps = _build_chat_deps(db, session, include_full_text=include_full_text)
        deps_ms = (perf_counter() - deps_start) * 1000
        context_len = len(deps.article_context) if deps.article_context else 0
        logger.info(
            "Async chat context built",
            extra=build_log_extra(
                component="chat",
                operation="build_context",
                event_name="chat.turn.context_built",
                status="completed",
                duration_ms=deps_ms,
                session_id=session_id,
                message_id=message_id,
                user_id=session_user_id,
                content_id=session_content_id,
                source=source,
                context_data={
                    "context_chars": context_len,
                    "has_content": deps.content is not None,
                    "llm_task_id": chat_llm_task_id,
                },
            ),
        )

        # Load prior completed history, excluding the processing row for this turn.
        history_start = perf_counter()
        history = load_message_history(
            db,
            session_row_id,
            exclude_message_id=message_id,
            completed_only=True,
        )
        history_ms = (perf_counter() - history_start) * 1000
        logger.info(
            "Async chat history loaded",
            extra=build_log_extra(
                component="chat",
                operation="load_history",
                event_name="chat.turn.history_loaded",
                status="completed",
                duration_ms=history_ms,
                session_id=session_id,
                message_id=message_id,
                user_id=session_user_id,
                context_data={"history_count": len(history)},
            ),
        )
        provider_api_key = resolve_effective_api_key(
            db=db,
            user_id=session_user_id,
            model_spec=model_spec,
        )
        chat_llm_task.running(
            db,
            note="Running async article chat agent",
            model_provider=provider,
            model_name=model_spec,
        )
        db.close()
        db = None

        # Run the agent
        logger.info(
            "Async chat LLM call started",
            extra=build_log_extra(
                component="chat",
                operation="llm_call",
                event_name="chat.turn.llm_started",
                status="started",
                session_id=session_id,
                message_id=message_id,
                user_id=session_user_id,
                content_id=session_content_id,
                source=source,
                context_data={
                    "model": model_spec,
                    "history_count": len(history),
                    "llm_task_id": chat_llm_task_id,
                },
            ),
        )
        agent_start = perf_counter()
        result = await run_in_threadpool(
            _run_agent_sync,
            model_spec,
            user_prompt,
            deps,
            history,
            trace_name="chat.turn.async",
            source=source,
            task_id=task_id,
            message_id=message_id,
            provider_api_key=provider_api_key,
        )
        agent_ms = (perf_counter() - agent_start) * 1000
        _log_chat_usage(result, session_usage_snapshot, session_id, message_id, "async")
        output_text = _agent_output_text(result)

        # Extract tool calls info
        tool_calls = getattr(result, "tool_calls", []) or []
        tool_names = [
            getattr(tc, "name", None)
            or getattr(tc, "function_name", None)
            or getattr(tc, "tool_name", None)
            for tc in tool_calls
        ]
        output_len = len(output_text)
        logger.info(
            "Async chat LLM call completed",
            extra=build_log_extra(
                component="chat",
                operation="llm_call",
                event_name="chat.turn.llm_completed",
                status="completed",
                duration_ms=agent_ms,
                session_id=session_id,
                message_id=message_id,
                user_id=session_user_id,
                content_id=session_content_id,
                source=source,
                context_data={
                    "tool_names": tool_names,
                    "tool_count": len([name for name in tool_names if name]),
                    "output_chars": output_len,
                    "llm_task_id": chat_llm_task_id,
                },
            ),
        )

        # Update the message with the complete result
        save_start = perf_counter()
        new_messages = result.new_messages()
        with SessionLocal() as persist_db:
            update_message_completed(
                persist_db,
                message_id,
                new_messages,
                display_user_prompt=user_prompt,
            )
            session_to_update = (
                persist_db.query(ChatSession).filter(ChatSession.id == session_id).first()
            )
            if session_to_update is None:
                raise ValueError(f"Session {session_id} not found")
            session_to_update.last_message_at = datetime.now(UTC)
            session_to_update.updated_at = datetime.now(UTC)
            _sync_parent_session_activity(persist_db, session_to_update)
            persist_db.commit()
            chat_llm_task.completed(
                persist_db,
                note="Async article chat turn completed",
                output_json={
                    "chat_session_id": session_id,
                    "message_id": message_id,
                    "content_id": session.content_id,
                    "output_chars": output_len,
                    "new_message_count": len(new_messages),
                    "tool_names": [name for name in tool_names if name],
                },
                model_provider=provider,
                model_name=model_spec,
            )
        save_ms = (perf_counter() - save_start) * 1000

        total_ms = (perf_counter() - total_start) * 1000
        logger.info(
            "Async chat turn persisted",
            extra=build_log_extra(
                component="chat",
                operation="process_message_async",
                event_name="chat.turn.persisted",
                status="completed",
                duration_ms=total_ms,
                session_id=session_id,
                message_id=message_id,
                user_id=session_user_id,
                content_id=session.content_id,
                source=source,
                context_data={
                    "model": model_spec,
                    "deps_ms": round(deps_ms, 2),
                    "history_ms": round(history_ms, 2),
                    "agent_ms": round(agent_ms, 2),
                    "save_ms": round(save_ms, 2),
                    "llm_task_id": chat_llm_task_id,
                },
            ),
        )

    except Exception as exc:
        total_ms = (perf_counter() - total_start) * 1000
        logger.exception(
            "Async chat turn failed",
            extra=build_log_extra(
                component="chat",
                operation="process_message_async",
                event_name="chat.turn.failed",
                status="failed",
                duration_ms=total_ms,
                session_id=session_id,
                message_id=message_id,
                source=source,
                context_data={
                    "failure_class": type(exc).__name__,
                    "llm_task_id": chat_llm_task_id,
                },
            ),
        )
        try:
            if db is not None:
                update_message_failed(db, message_id, str(exc))
                chat_llm_task.failed(
                    db,
                    note="Async article chat turn failed",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
            else:
                with SessionLocal() as fail_db:
                    update_message_failed(fail_db, message_id, str(exc))
                    chat_llm_task.failed(
                        fail_db,
                        note="Async article chat turn failed",
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                    )
        except Exception as update_exc:
            logger.error("[AsyncChat:UPDATE_FAILED] mid=%s error=%s", message_id, update_exc)
    finally:
        _close_sandbox_session(deps.sandbox_session if deps is not None else None)
        if db is not None:
            db.close()


INITIAL_QUESTIONS_PROMPT = load_prompt("chat/article#initial_questions_user")


async def generate_initial_suggestions(
    db: Session,
    session: ChatSession,
    *,
    source: str = "realtime",
    task_id: int | None = None,
) -> ChatRunResult | None:
    """Generate the initial assistant message for article-based sessions.

    Args:
        db: Database session.
        session: Active chat session.
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
    logger.info(
        "Initial suggestions started",
        extra=build_log_extra(
            component="chat",
            operation="generate_initial_suggestions",
            event_name="chat.turn",
            status="started",
            session_id=session_row_id,
            user_id=session_user_id,
            content_id=session_content_id,
            source=source,
            context_data={"model": model_spec, "session_type": session_type},
        ),
    )

    if not session_content_id and not session.context_snapshot:
        logger.warning(
            "Initial suggestions skipped because session has no context",
            extra=build_log_extra(
                component="chat",
                operation="generate_initial_suggestions",
                event_name="chat.turn",
                status="skipped",
                session_id=session_row_id,
                user_id=session_user_id,
                source=source,
            ),
        )
        return None

    include_full_text = True
    deps = _build_chat_deps(db, session, include_full_text=include_full_text)
    provider_api_key = resolve_effective_api_key(
        db=db,
        user_id=session_user_id,
        model_spec=model_spec,
    )

    try:
        agent_start = perf_counter()
        result = await run_in_threadpool(
            _run_agent_sync,
            model_spec,
            INITIAL_QUESTIONS_PROMPT,
            deps,
            [],
            trace_name="chat.initial_suggestions",
            source=source,
            task_id=task_id,
            provider_api_key=provider_api_key,
        )
        agent_ms = (perf_counter() - agent_start) * 1000
        _log_chat_usage(
            result,
            session_usage_snapshot,
            session_row_id,
            None,
            "initial_suggestions",
        )
        output_text = _agent_output_text(result)
        from pydantic_ai.messages import ModelResponse, TextPart

        new_messages: list[ModelMessage] = [ModelResponse(parts=[TextPart(content=output_text)])]
        save_start = perf_counter()
        save_messages(db, session_row_id, new_messages)
        save_ms = (perf_counter() - save_start) * 1000

        session.last_message_at = datetime.now(UTC)
        session.updated_at = datetime.now(UTC)
        db.commit()

        total_ms = (perf_counter() - total_start) * 1000
        tool_calls = getattr(result, "tool_calls", []) or []
        tool_names = [
            getattr(tc, "name", None)
            or getattr(tc, "function_name", None)
            or getattr(tc, "tool_name", None)
            for tc in tool_calls
        ]
        logger.info(
            "Initial suggestions persisted",
            extra=build_log_extra(
                component="chat",
                operation="generate_initial_suggestions",
                event_name="chat.turn.persisted",
                status="completed",
                duration_ms=total_ms,
                session_id=session_row_id,
                user_id=session_user_id,
                content_id=session_content_id,
                source=source,
                context_data={
                    "model": model_spec,
                    "agent_ms": round(agent_ms, 2),
                    "save_ms": round(save_ms, 2),
                    "tool_names": tool_names,
                    "tool_count": len([name for name in tool_names if name]),
                },
            ),
        )

        return ChatRunResult(
            output_text=output_text,
            new_messages=new_messages,
            all_messages=result.all_messages,
            tool_calls=getattr(result, "tool_calls", []),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Initial suggestions failed",
            extra=build_log_extra(
                component="chat",
                operation="generate_initial_suggestions",
                event_name="chat.turn.failed",
                status="failed",
                duration_ms=(perf_counter() - total_start) * 1000,
                session_id=session_row_id,
                user_id=session_user_id,
                content_id=session.content_id,
                source=source,
                context_data={"failure_class": type(exc).__name__},
            ),
        )
        db.rollback()
        raise
    finally:
        _close_sandbox_session(deps.sandbox_session)
