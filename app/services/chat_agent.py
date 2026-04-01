"""Chat agent service using pydantic-ai for deep-dive conversations."""

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter

from fastapi.concurrency import run_in_threadpool
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.observability import build_log_extra
from app.models.chat_message_metadata import ChatMessageRenderMetadata
from app.models.schema import ChatMessage, ChatSession, Content, MessageProcessingStatus
from app.services.exa_client import exa_search, get_exa_client
from app.services.langfuse_tracing import langfuse_trace_context
from app.services.llm_costs import extract_usage_from_result, record_llm_usage
from app.services.llm_models import (  # noqa: F401 (re-export for API schemas)
    LLMProvider as ChatModelProvider,
)
from app.services.llm_models import (
    build_pydantic_model,
    resolve_effective_api_key,
    resolve_model_provider,
)

logger = get_logger(__name__)

CONTEXT_WINDOW_TOKENS = 200_000
SYSTEM_AND_ARTICLE_BUDGET_RATIO = 0.75
TOKEN_CHARS_PER_TOKEN = 4

SYSTEM_PROMPT_TEXT = (
    "You are an assistant helping users explore articles, news, and topics. "
    "Be concise but thorough. Help users understand what they read."
    "\n\n"
    "**CRITICAL - How to Use Web Search:**\n"
    "- Use exa_web_search to research topics, verify claims, and find context\n"
    "- AFTER searching, you MUST synthesize the results into your response:\n"
    "  1. Summarize key findings from the search results\n"
    "  2. Quote or paraphrase specific insights from the sources\n"
    "  3. Include clickable markdown links: [Source Title](url)\n"
    "  4. Compare/contrast what different sources say\n"
    "- If search returns relevant content, NEVER give a generic response - use the content!\n"
    "- Search multiple times if exploring different angles"
    "\n\n"
    "**Response Format:**\n"
    "- Do not use markdown tables in chat responses. "
    "On mobile, format comparisons as headings, bullets, "
    "or one-item-per-line entries instead\n"
    "- Always cite sources with markdown links when referencing search results\n"
    "- Keep responses focused and scannable"
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


# Agent cache keyed by model spec and effective credential identity.
_agents: dict[tuple[str, str], Agent[ChatDeps, str]] = {}


def _build_agent_cache_key(model_spec: str, api_key_override: str | None) -> tuple[str, str]:
    """Build a stable cache key without persisting raw secrets in memory."""
    if not api_key_override:
        return model_spec, ""
    return model_spec, hashlib.sha256(api_key_override.encode("utf-8")).hexdigest()


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
        parts.append(
            "\nProvided reference context is available below. Treat it as the "
            "conversation's source material even if the user does not repeat it, "
            "and do not ask the user to paste it again unless the context is actually missing."
        )
        parts.append(f"\n{context_label}:\n{article_context}")

    return parts


def _build_run_user_prompt(user_prompt: str, deps: ChatDeps) -> str:
    """Build the model-facing user prompt for a chat turn."""
    if deps.session.context_snapshot and deps.article_context:
        return (
            "Use the provided session context below as the source material for this "
            "conversation, even if the user does not repeat it.\n\n"
            f"{deps.context_label}:\n{deps.article_context}\n\n"
            f"User request:\n{user_prompt}"
        )
    return user_prompt


def get_chat_agent(
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
    cache_key = _build_agent_cache_key(model_spec, api_key_override)
    if cache_key in _agents:
        return _agents[cache_key]

    # Build model with explicit API key if needed
    model, model_settings = build_pydantic_model(
        model_spec,
        api_key_override=api_key_override,
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

    _agents[cache_key] = agent
    logger.info(f"Created chat agent for model: {model_spec}")
    return agent


def build_article_context(
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

    transcript = metadata.get("transcript")
    content_text = metadata.get("content")
    full_markdown = None
    if not content_text and isinstance(summary, dict):
        full_markdown = summary.get("full_markdown")

    full_text_label = None
    full_text = None
    if isinstance(transcript, str) and transcript.strip():
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


def load_message_history(db: Session, session_id: int) -> list[ModelMessage]:
    """Load all messages for a chat session from the database.

    Args:
        db: Database session.
        session_id: Chat session ID.

    Returns:
        List of ModelMessage objects in chronological order.
    """
    messages: list[ModelMessage] = []

    # Query chat_messages ordered by created_at
    db_messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at)
        .all()
    )

    for db_msg in db_messages:
        try:
            # Deserialize JSON to list of ModelMessage
            msg_list = ModelMessagesTypeAdapter.validate_json(db_msg.message_list)
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
        if not parts:
            break
        first_part = parts[0]
        if isinstance(first_part, dict) and "content" in first_part:
            first_part["content"] = display_user_prompt
        break
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
    db: Session, session: ChatSession, include_full_text: bool = False
) -> ChatDeps:
    """Construct chat dependencies (content + context) for a session."""
    content: Content | None = None
    article_context: str | None = None
    context_label = "Article Context"

    if session.context_snapshot:
        return ChatDeps(
            session=session,
            content=None,
            article_context=session.context_snapshot,
            context_label="Session Context",
        )

    if session.content_id:
        content = db.query(Content).filter(Content.id == session.content_id).first()
        if content:
            max_system_article_tokens = int(CONTEXT_WINDOW_TOKENS * SYSTEM_AND_ARTICLE_BUDGET_RATIO)
            system_tokens = _estimate_tokens(SYSTEM_PROMPT_TEXT)
            header_text = "\n".join(_build_article_header(content, session))
            header_tokens = _estimate_tokens(header_text)
            available_tokens = max(max_system_article_tokens - system_tokens - header_tokens, 0)
            article_context = build_article_context(
                content,
                include_full_text=include_full_text,
                max_tokens=available_tokens,
            )

    return ChatDeps(
        session=session,
        content=content,
        article_context=article_context,
        context_label=context_label,
    )


def _log_chat_usage(
    result: object,
    db: Session,
    session: ChatSession,
    session_id: int,
    message_id: int | None,
    context: str,
) -> None:
    """Persist and log token usage for a chat request when available."""
    usage_details = extract_usage_from_result(result)
    if usage_details is None:
        return

    try:
        usage = record_llm_usage(
            db,
            provider=resolve_model_provider(session.llm_model),
            model=session.llm_model,
            feature="chat",
            operation=f"chat.{context}",
            source=context,
            usage=usage_details,
            session_id=session_id,
            message_id=message_id,
            user_id=session.user_id,
            content_id=session.content_id,
            metadata={"session_type": session.session_type},
        )
    except Exception:  # noqa: BLE001
        return

    logger.info(
        "Chat usage recorded",
        extra=build_log_extra(
            component="chat",
            operation="usage",
            event_name="chat.turn.usage",
            status="completed",
            session_id=session_id,
            message_id=message_id,
            user_id=session.user_id,
            content_id=session.content_id,
            source=context,
            context_data={
                "model": session.llm_model,
                "provider": resolve_model_provider(session.llm_model),
                "usage_recorded": usage is not None,
            },
        ),
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
    logger.info(
        "Chat turn started",
        extra=build_log_extra(
            component="chat",
            operation="run_chat_turn",
            event_name="chat.turn",
            status="started",
            session_id=session.id,
            user_id=session.user_id,
            content_id=session.content_id,
            source=source,
            context_data={
                "model": session.llm_model,
                "provider": resolve_model_provider(session.llm_model),
                "session_type": session.session_type,
                "prompt_chars": len(user_prompt),
            },
        ),
    )

    history_start = perf_counter()
    history = load_message_history(db, session.id)
    history_ms = (perf_counter() - history_start) * 1000
    logger.info(
        "Chat history loaded",
        extra=build_log_extra(
            component="chat",
            operation="load_history",
            event_name="chat.turn.history_loaded",
            status="completed",
            duration_ms=history_ms,
            session_id=session.id,
            user_id=session.user_id,
            context_data={"history_count": len(history)},
        ),
    )
    include_full_text = True

    deps_start = perf_counter()
    deps = _build_chat_deps(db, session, include_full_text=include_full_text)
    provider_api_key = resolve_effective_api_key(
        db=db,
        user_id=session.user_id,
        model_spec=session.llm_model,
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
            session_id=session.id,
            user_id=session.user_id,
            content_id=session.content_id,
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
                session_id=session.id,
                user_id=session.user_id,
                content_id=session.content_id,
                source=source,
                context_data={"model": session.llm_model},
            ),
        )
        agent_start = perf_counter()
        result = await run_in_threadpool(
            _run_agent_sync,
            session.llm_model,
            user_prompt,
            deps,
            history,
            trace_name="chat.turn.sync",
            source=source,
            task_id=task_id,
            provider_api_key=provider_api_key,
        )
        agent_ms = (perf_counter() - agent_start) * 1000
        _log_chat_usage(result, db, session, session.id, None, "sync")
        new_messages = result.new_messages()
        save_messages(
            db,
            session.id,
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
        logger.info(
            "Chat turn completed",
            extra=build_log_extra(
                component="chat",
                operation="run_chat_turn",
                event_name="chat.turn",
                status="completed",
                duration_ms=total_ms,
                session_id=session.id,
                user_id=session.user_id,
                content_id=session.content_id,
                source=source,
                context_data={
                    "model": session.llm_model,
                    "deps_ms": round(deps_ms, 2),
                    "history_ms": round(history_ms, 2),
                    "agent_ms": round(agent_ms, 2),
                    "tool_names": tool_names,
                    "tool_count": len([name for name in tool_names if name]),
                },
            ),
        )

        return ChatRunResult(
            output_text=result.output,
            new_messages=new_messages,
            all_messages=result.all_messages,
            tool_calls=getattr(result, "tool_calls", []),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Chat turn failed",
            extra=build_log_extra(
                component="chat",
                operation="run_chat_turn",
                event_name="chat.turn",
                status="failed",
                duration_ms=(perf_counter() - total_start) * 1000,
                session_id=session.id,
                user_id=session.user_id,
                content_id=session.content_id,
                source=source,
                context_data={"failure_class": type(exc).__name__},
            ),
        )
        db.rollback()
        raise


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
    db = SessionLocal()
    try:
        session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
        if not session:
            logger.error("[AsyncChat:ERROR] Session %s not found", session_id)
            return

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
                user_id=session.user_id,
                content_id=session.content_id,
                source=source,
                context_data={
                    "context_chars": context_len,
                    "has_content": deps.content is not None,
                },
            ),
        )

        # Load history (excluding the processing message we just created)
        history_start = perf_counter()
        history = load_message_history(db, session.id)
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
                user_id=session.user_id,
                context_data={"history_count": len(history)},
            ),
        )
        provider_api_key = resolve_effective_api_key(
            db=db,
            user_id=session.user_id,
            model_spec=session.llm_model,
        )

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
                user_id=session.user_id,
                content_id=session.content_id,
                source=source,
                context_data={"model": session.llm_model, "history_count": len(history)},
            ),
        )
        agent_start = perf_counter()
        result = await run_in_threadpool(
            _run_agent_sync,
            session.llm_model,
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
        _log_chat_usage(result, db, session, session_id, message_id, "async")

        # Extract tool calls info
        tool_calls = getattr(result, "tool_calls", []) or []
        tool_names = [
            getattr(tc, "name", None)
            or getattr(tc, "function_name", None)
            or getattr(tc, "tool_name", None)
            for tc in tool_calls
        ]
        output_len = len(result.output) if result.output else 0
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
                user_id=session.user_id,
                content_id=session.content_id,
                source=source,
                context_data={
                    "tool_names": tool_names,
                    "tool_count": len([name for name in tool_names if name]),
                    "output_chars": output_len,
                },
            ),
        )

        # Update the message with the complete result
        save_start = perf_counter()
        new_messages = result.new_messages()
        update_message_completed(
            db,
            message_id,
            new_messages,
            display_user_prompt=user_prompt,
        )
        save_ms = (perf_counter() - save_start) * 1000

        # Update session timestamps
        session.last_message_at = datetime.now(UTC)
        session.updated_at = datetime.now(UTC)
        _sync_parent_session_activity(db, session)
        db.commit()

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
                user_id=session.user_id,
                content_id=session.content_id,
                source=source,
                context_data={
                    "model": session.llm_model,
                    "deps_ms": round(deps_ms, 2),
                    "history_ms": round(history_ms, 2),
                    "agent_ms": round(agent_ms, 2),
                    "save_ms": round(save_ms, 2),
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
                context_data={"failure_class": type(exc).__name__},
            ),
        )
        try:
            update_message_failed(db, message_id, str(exc))
        except Exception as update_exc:
            logger.error("[AsyncChat:UPDATE_FAILED] mid=%s error=%s", message_id, update_exc)
    finally:
        db.close()


INITIAL_QUESTIONS_PROMPT = """
You are starting a new conversation about the article described in your context.

Write a short welcome message (1-2 sentences) that:
- Briefly states what help you can provide (explain, critique, brainstorm, apply ideas).
- Sounds friendly and concise.

After the welcome, propose 2-4 concrete directions the user could take next:
- Use bullet points.
- Mix question types: clarification, implications, counterpoints, practical applications.
- Make them specific to this article, not generic.

Do not mention tools, system prompts, or implementation details. Just write what the user sees.
""".strip()


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
    logger.info(
        "Initial suggestions started",
        extra=build_log_extra(
            component="chat",
            operation="generate_initial_suggestions",
            event_name="chat.turn",
            status="started",
            session_id=session.id,
            user_id=session.user_id,
            content_id=session.content_id,
            source=source,
            context_data={"model": session.llm_model, "session_type": session.session_type},
        ),
    )

    if not session.content_id:
        logger.warning(
            "Initial suggestions skipped because session has no content",
            extra=build_log_extra(
                component="chat",
                operation="generate_initial_suggestions",
                event_name="chat.turn",
                status="skipped",
                session_id=session.id,
                user_id=session.user_id,
                source=source,
            ),
        )
        return None

    include_full_text = True
    deps = _build_chat_deps(db, session, include_full_text=include_full_text)
    provider_api_key = resolve_effective_api_key(
        db=db,
        user_id=session.user_id,
        model_spec=session.llm_model,
    )

    try:
        agent_start = perf_counter()
        result = await run_in_threadpool(
            _run_agent_sync,
            session.llm_model,
            INITIAL_QUESTIONS_PROMPT,
            deps,
            [],
            trace_name="chat.initial_suggestions",
            source=source,
            task_id=task_id,
            provider_api_key=provider_api_key,
        )
        agent_ms = (perf_counter() - agent_start) * 1000
        _log_chat_usage(result, db, session, session.id, None, "initial_suggestions")
        new_messages = result.new_messages()
        save_start = perf_counter()
        save_messages(db, session.id, new_messages)
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
                session_id=session.id,
                user_id=session.user_id,
                content_id=session.content_id,
                source=source,
                context_data={
                    "model": session.llm_model,
                    "agent_ms": round(agent_ms, 2),
                    "save_ms": round(save_ms, 2),
                    "tool_names": tool_names,
                    "tool_count": len([name for name in tool_names if name]),
                },
            ),
        )

        return ChatRunResult(
            output_text=result.output,
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
                session_id=session.id,
                user_id=session.user_id,
                content_id=session.content_id,
                source=source,
                context_data={"failure_class": type(exc).__name__},
            ),
        )
        db.rollback()
        raise
