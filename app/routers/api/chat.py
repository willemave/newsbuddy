"""Chat session endpoints for deep-dive conversations."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Path,
    Query,
    Response,
    status,
)
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.db import get_db_session, get_readonly_db_session
from app.core.deps import get_current_user
from app.core.logging import get_logger
from app.core.observability import build_log_extra
from app.models.api.chat import (
    AssistantTurnRequest,
    AssistantTurnResponse,
    ChatMessageDisplayType,
    ChatMessageDto,
    ChatMessageRole,
    ChatSessionDetailDto,
    ChatSessionSummaryDto,
    CouncilSelectRequest,
    CouncilStartRequest,
    CreateChatSessionRequest,
    CreateChatSessionResponse,
    MessageStatusResponse,
    SendChatMessageRequest,
    SendMessageResponse,
    UpdateChatSessionRequest,
)
from app.models.api.chat import (
    MessageProcessingStatus as MessageProcessingStatusDto,
)
from app.models.chat_message_metadata import ChatMessageRenderMetadata
from app.models.content_mapper import content_to_domain
from app.models.internal.assistant import AssistantScreenContext
from app.models.schema import (
    ChatMessage,
    ChatSession,
    Content,
    ContentKnowledgeSave,
    MessageProcessingStatus,
)
from app.models.user import User
from app.services.assistant_router import (
    ASSISTANT_SESSION_TYPES,
    KNOWLEDGE_SESSION_TYPE,
    build_screen_context_snapshot,
    create_assistant_session,
    process_assistant_turn_async,
)
from app.services.chat_agent import (
    create_processing_message,
    generate_initial_suggestions,
    process_message_async,
)
from app.services.council_chat import (
    select_council_branch,
    start_council_chat,
)
from app.services.llm_models import is_deep_research_provider, resolve_model
from app.services.personal_markdown_library import sync_personal_markdown_for_content

logger = get_logger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

_SEARCH_TOOL_NAMES = {
    "exa_web_search",
    "search_personal_library",
}


def _format_process_summary_label(
    tool_names: list[str],
    *,
    has_intermediate_assistant_text: bool,
) -> str | None:
    """Build a compact transcript label for intermediate tool/thinking activity."""
    normalized_tool_names = {name.strip().lower() for name in tool_names if name and name.strip()}
    tool_call_count = len([name for name in tool_names if name and name.strip()])

    if tool_call_count:
        tool_label = "tool" if tool_call_count == 1 else "tools"

        if normalized_tool_names & _SEARCH_TOOL_NAMES:
            return f"Thinking • Executed {tool_call_count} {tool_label} and reviewed sources"

        return f"Thinking • Executed {tool_call_count} {tool_label} and reviewed results"

    if has_intermediate_assistant_text:
        return "Thinking • Considered the request"

    return None


def _load_render_metadata(db_message: ChatMessage) -> ChatMessageRenderMetadata | None:
    """Load validated render metadata from a stored chat message."""

    if not isinstance(db_message.render_metadata, dict):
        return None
    try:
        return ChatMessageRenderMetadata.model_validate(db_message.render_metadata)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to parse render metadata for chat message %s: %s",
            db_message.id,
            exc,
        )
        return None


def _session_to_summary(
    session: ChatSession,
    article_title: str | None = None,
    article_url: str | None = None,
    article_summary: str | None = None,
    article_source: str | None = None,
    has_pending_message: bool = False,
    is_saved_to_knowledge: bool = False,
    has_messages: bool = True,
    last_message_preview: str | None = None,
    last_message_role: str | None = None,
) -> ChatSessionSummaryDto:
    """Convert database ChatSession to API response."""
    return ChatSessionSummaryDto(
        id=session.id,
        content_id=session.content_id,
        title=session.title,
        session_type=session.session_type,
        topic=session.topic,
        llm_provider=session.llm_provider,
        llm_model=session.llm_model,
        created_at=session.created_at,
        updated_at=session.updated_at,
        last_message_at=session.last_message_at,
        article_title=article_title,
        article_url=article_url,
        article_summary=article_summary,
        article_source=article_source,
        is_archived=session.is_archived,
        has_pending_message=has_pending_message,
        is_saved_to_knowledge=is_saved_to_knowledge,
        has_messages=has_messages,
        last_message_preview=last_message_preview,
        last_message_role=last_message_role,
        council_mode=session.council_mode,
        active_child_session_id=session.active_child_session_id,
    )


def _build_processing_user_message(
    *,
    db_message: ChatMessage,
    session_id: int,
    content: str,
) -> ChatMessageDto:
    return ChatMessageDto(
        id=db_message.id,
        source_message_id=db_message.id,
        session_id=session_id,
        role=ChatMessageRole.USER,
        content=content,
        timestamp=db_message.created_at,
        status=MessageProcessingStatusDto.PROCESSING,
    )


def _resolve_active_child_session(db: Session, session: ChatSession) -> ChatSession | None:
    """Return the active council child session for a parent session."""

    if not session.council_mode or not session.active_child_session_id:
        return None
    return (
        db.query(ChatSession)
        .filter(
            ChatSession.id == session.active_child_session_id,
            ChatSession.parent_session_id == session.id,
            ChatSession.is_hidden_from_history == True,  # noqa: E712
        )
        .first()
    )


def _build_async_assistant_display_id(message_id: int) -> int:
    """Build a stable display ID for an async assistant reply.

    The pending user message and the completed assistant reply share the same
    backing `chat_messages` row. UI surfaces render them as distinct rows, so
    they need distinct display IDs to avoid SwiftUI identity collisions.
    """

    return 1_000_000_000 + message_id


def _refresh_assistant_session_context(
    *,
    db: Session,
    session: ChatSession,
    user_id: int,
    screen_context: AssistantScreenContext,
) -> None:
    """Refresh persisted assistant session context for the current screen."""

    session.context_snapshot = build_screen_context_snapshot(
        db,
        user_id=user_id,
        screen_context=screen_context,
    )
    session.content_id = screen_context.content_id
    session.topic = screen_context.selected_topic

    title = screen_context.screen_title or session.title or "Knowledge Chat"
    if screen_context.content_id is not None:
        content = db.query(Content).filter(Content.id == screen_context.content_id).first()
        if content is not None and content.title:
            title = content.title
    session.title = title[:500]
    session.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(session)


def _resolve_article_title(content: Content) -> str | None:
    """Resolve a chat-friendly title from content, falling back to display_title."""
    if content.title:
        return content.title

    try:
        domain_content = content_to_domain(content)
        return domain_content.display_title
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.warning("Failed to resolve display title for content %s: %s", content.id, exc)
        return None


def _extract_short_summary(content: Content) -> str | None:
    """Extract short summary from content metadata."""
    return content.short_summary


def _extract_last_message_preview(
    db_message: ChatMessage,
    max_length: int = 200,
) -> tuple[str | None, str | None]:
    """Extract the last user/assistant text and role from a ChatMessage record.

    Returns (preview_text, role) where role is 'user' or 'assistant'.
    """
    from pydantic_ai.messages import (
        ModelMessagesTypeAdapter,
        ModelRequest,
        ModelResponse,
        TextPart,
        UserPromptPart,
    )

    try:
        msg_list = ModelMessagesTypeAdapter.validate_json(db_message.message_list)
    except Exception:
        return None, None

    # Walk backwards to find the last text content
    for model_msg in reversed(msg_list):
        if isinstance(model_msg, ModelResponse):
            for part in reversed(model_msg.parts):
                if isinstance(part, TextPart) and part.content:
                    text = part.content[:max_length]
                    return text, "assistant"
        elif isinstance(model_msg, ModelRequest):
            for part in reversed(model_msg.parts):
                if isinstance(part, UserPromptPart) and part.content:
                    text = str(part.content)[:max_length]
                    return text, "user"

    return None, None


def _extract_messages_for_display(
    db: Session,
    session_id: int,
    *,
    session_id_override: int | None = None,
    min_message_id_exclusive: int | None = None,
) -> list[ChatMessageDto]:
    """Load messages from DB and convert to display format.

    Extracts user and assistant text messages from the ModelMessage format
    stored in the database. Includes status for async message processing.
    """
    from pydantic_ai.messages import (
        ModelMessagesTypeAdapter,
        ModelRequest,
        ModelResponse,
        TextPart,
        ToolCallPart,
        UserPromptPart,
    )

    messages: list[ChatMessageDto] = []
    display_id = 0  # Unique ID for each display message (user/assistant parts)

    # Query chat_messages ordered by created_at
    query = db.query(ChatMessage).filter(ChatMessage.session_id == session_id)
    if min_message_id_exclusive is not None:
        query = query.filter(ChatMessage.id > min_message_id_exclusive)
    db_messages = query.order_by(ChatMessage.created_at).all()

    for db_msg in db_messages:
        try:
            # Deserialize JSON to list of ModelMessage
            msg_list = ModelMessagesTypeAdapter.validate_json(db_msg.message_list)
            status = MessageProcessingStatusDto(db_msg.status)
            render_metadata = _load_render_metadata(db_msg)
            assistant_responses: list[str] = []
            tool_names: list[str] = []
            user_text_emitted = False

            for model_msg in msg_list:
                if isinstance(model_msg, ModelRequest):
                    # Only show the first user-authored prompt for this stored turn.
                    # Hide tool-return/system parts and any later internal requests.
                    for part in model_msg.parts:
                        if user_text_emitted:
                            break
                        if isinstance(part, UserPromptPart) and part.content:
                            user_text_emitted = True
                            display_id += 1
                            messages.append(
                                ChatMessageDto(
                                    id=display_id,  # Unique display ID
                                    source_message_id=db_msg.id,
                                    session_id=session_id_override or session_id,
                                    role=ChatMessageRole.USER,
                                    timestamp=db_msg.created_at,
                                    content=part.content,
                                    status=status,
                                    error=db_msg.error,
                                )
                            )
                elif isinstance(model_msg, ModelResponse):
                    response_text_parts: list[str] = []
                    for part in model_msg.parts:
                        if isinstance(part, TextPart) and part.content:
                            response_text_parts.append(part.content)
                        elif isinstance(part, ToolCallPart):
                            tool_names.append(part.tool_name)

                    if response_text_parts:
                        assistant_responses.append("\n\n".join(response_text_parts))

            latest_assistant_text = assistant_responses[-1] if assistant_responses else None
            process_summary_label = _format_process_summary_label(
                tool_names,
                has_intermediate_assistant_text=len(assistant_responses) > 1,
            )

            if process_summary_label:
                display_id += 1
                messages.append(
                    ChatMessageDto(
                        id=display_id,
                        source_message_id=db_msg.id,
                        session_id=session_id_override or session_id,
                        role=ChatMessageRole.TOOL,
                        timestamp=db_msg.created_at,
                        content=process_summary_label,
                        display_type=ChatMessageDisplayType.PROCESS_SUMMARY,
                        process_label=process_summary_label,
                        status=status,
                        error=db_msg.error,
                    )
                )

            if latest_assistant_text:
                display_id += 1
                messages.append(
                    ChatMessageDto(
                        id=display_id,  # Unique display ID
                        source_message_id=db_msg.id,
                        session_id=session_id_override or session_id,
                        role=ChatMessageRole.ASSISTANT,
                        timestamp=db_msg.created_at,
                        content=latest_assistant_text,
                        display_type=ChatMessageDisplayType.MESSAGE,
                        status=status,
                        error=db_msg.error,
                        feed_options=render_metadata.feed_options if render_metadata else [],
                        council_candidates=(
                            render_metadata.council_candidates if render_metadata else []
                        ),
                        active_council_child_session_id=(
                            render_metadata.active_council_child_session_id
                            if render_metadata
                            else None
                        ),
                    )
                )
        except Exception as e:
            logger.warning(f"Failed to deserialize message {db_msg.id}: {e}")
            continue

    return messages


@router.get(
    "/sessions",
    response_model=list[ChatSessionSummaryDto],
    summary="List chat sessions",
    description="List all chat sessions for the current user, ordered by most recent activity.",
)
async def list_sessions(
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    content_id: Annotated[int | None, Query(description="Filter by content ID")] = None,
    limit: Annotated[int, Query(ge=1, le=100, description="Maximum sessions to return")] = 50,
) -> list[ChatSessionSummaryDto]:
    """List chat sessions for the current user.

    Returns sessions ordered by last_message_at (most recent first),
    falling back to created_at for sessions without messages.
    """
    query = db.query(ChatSession).filter(
        ChatSession.user_id == current_user.id,
        ChatSession.is_archived == False,  # noqa: E712
        ChatSession.is_hidden_from_history == False,  # noqa: E712
    )

    if content_id is not None:
        query = query.filter(ChatSession.content_id == content_id)

    # Order by most recent activity (coalesce with created_at for new sessions)
    sessions = (
        query.order_by(
            func.coalesce(ChatSession.last_message_at, ChatSession.created_at).desc(),
        )
        .limit(limit)
        .all()
    )

    active_child_ids = [
        session.active_child_session_id
        for session in sessions
        if session.active_child_session_id is not None
    ]
    active_child_sessions: dict[int, ChatSession] = {}
    if active_child_ids:
        active_child_rows = (
            db.query(ChatSession)
            .filter(ChatSession.id.in_(active_child_ids))
            .filter(ChatSession.is_hidden_from_history == True)  # noqa: E712
            .all()
        )
        active_child_sessions = {child.id: child for child in active_child_rows}

    content_ids = [session.content_id for session in sessions if session.content_id is not None]
    contents_by_id: dict[int, Content] = {}
    if content_ids:
        content_rows = db.query(Content).filter(Content.id.in_(content_ids)).all()
        contents_by_id = {content.id: content for content in content_rows}

    # Get session IDs that have pending messages (for efficiency)
    session_ids = [s.id for s in sessions]
    preview_session_ids = session_ids + active_child_ids
    pending_session_ids: set[int] = set()
    sessions_with_messages: set[int] = set()

    if preview_session_ids:
        # Check for pending messages
        pending_messages = (
            db.query(ChatMessage.session_id)
            .filter(
                ChatMessage.session_id.in_(preview_session_ids),
                ChatMessage.status == MessageProcessingStatus.PROCESSING.value,
            )
            .distinct()
            .all()
        )
        pending_session_ids = {m.session_id for m in pending_messages}

        # Check which sessions have any messages at all
        sessions_with_any_messages = (
            db.query(ChatMessage.session_id)
            .filter(ChatMessage.session_id.in_(preview_session_ids))
            .distinct()
            .all()
        )
        sessions_with_messages = {m.session_id for m in sessions_with_any_messages}

    # Batch-query the most recent message per session for previews
    last_message_map: dict[int, ChatMessage] = {}
    if preview_session_ids:
        # Subquery to get the max message ID per session
        latest_msg_subq = (
            db.query(
                ChatMessage.session_id,
                func.max(ChatMessage.id).label("max_id"),
            )
            .filter(ChatMessage.session_id.in_(preview_session_ids))
            .group_by(ChatMessage.session_id)
            .subquery()
        )
        latest_messages = (
            db.query(ChatMessage)
            .join(latest_msg_subq, ChatMessage.id == latest_msg_subq.c.max_id)
            .all()
        )
        last_message_map = {m.session_id: m for m in latest_messages}

    # Get knowledge-saved content IDs for this user
    knowledge_saved_content_ids: set[int] = set()
    if content_ids:
        knowledge_saves = (
            db.query(ContentKnowledgeSave.content_id)
            .filter(
                ContentKnowledgeSave.user_id == current_user.id,
                ContentKnowledgeSave.content_id.in_(content_ids),
            )
            .all()
        )
        knowledge_saved_content_ids = {row.content_id for row in knowledge_saves}

    # Build response with article titles, URLs, summaries, and sources
    result = []
    for session in sessions:
        article_title = None
        article_url = None
        article_summary = None
        article_source = None

        if session.content_id:
            content = contents_by_id.get(session.content_id)
            if content:
                article_title = _resolve_article_title(content)
                article_url = content.url
                article_summary = _extract_short_summary(content)
                article_source = content.source

        preview_session = session
        if session.council_mode and session.active_child_session_id is not None:
            candidate_child = active_child_sessions.get(session.active_child_session_id)
            if candidate_child and candidate_child.parent_session_id == session.id:
                preview_session = candidate_child
        has_pending = preview_session.id in pending_session_ids
        is_saved_to_knowledge = (
            session.content_id in knowledge_saved_content_ids if session.content_id else False
        )
        has_messages = session.id in sessions_with_messages

        # Extract last message preview
        last_preview: str | None = None
        last_role: str | None = None
        last_msg = last_message_map.get(preview_session.id)
        if last_msg:
            last_preview, last_role = _extract_last_message_preview(last_msg)

        result.append(
            _session_to_summary(
                session,
                article_title=article_title,
                article_url=article_url,
                article_summary=article_summary,
                article_source=article_source,
                has_pending_message=has_pending,
                is_saved_to_knowledge=is_saved_to_knowledge,
                has_messages=has_messages,
                last_message_preview=last_preview,
                last_message_role=last_role,
            )
        )

    return result


@router.post(
    "/sessions",
    response_model=CreateChatSessionResponse,
    summary="Create chat session",
    description="Create a new chat session, optionally associated with an article.",
)
async def create_session(
    request: CreateChatSessionRequest,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> CreateChatSessionResponse:
    """Create a new chat session.

    If content_id is provided, the session will be associated with that article
    and the article's context will be available to the chat agent.
    """
    # Resolve model
    provider, model_spec = resolve_model(request.llm_provider, request.llm_model_hint)

    # Determine session type
    if is_deep_research_provider(request.llm_provider):
        session_type = "deep_research"
    else:
        session_type = KNOWLEDGE_SESSION_TYPE

    # Get article title and URL if content_id provided
    article_title = None
    article_url = None
    article_summary = None
    article_source = None
    context_snapshot: str | None = None
    if request.content_id:
        content = db.query(Content).filter(Content.id == request.content_id).first()
        if not content:
            raise HTTPException(status_code=404, detail="Content not found")
        article_title = _resolve_article_title(content)
        article_url = content.url
        article_summary = _extract_short_summary(content)
        article_source = content.source
        context_snapshot = build_screen_context_snapshot(
            db,
            user_id=current_user.id,
            screen_context=AssistantScreenContext(
                screen_type=KNOWLEDGE_SESSION_TYPE,
                screen_title="Knowledge",
                content_id=request.content_id,
                selected_topic=request.topic,
                note=request.initial_message[:500] if request.initial_message else None,
            ),
        )
    elif session_type == KNOWLEDGE_SESSION_TYPE:
        context_snapshot = build_screen_context_snapshot(
            db,
            user_id=current_user.id,
            screen_context=AssistantScreenContext(
                screen_type=KNOWLEDGE_SESSION_TYPE,
                screen_title="Knowledge",
                selected_topic=request.topic,
                note=request.initial_message[:500] if request.initial_message else None,
            ),
        )

    # Build session title
    if request.topic and article_title:
        title = f"{article_title} - {request.topic}"
    elif article_title:
        title = article_title
    elif request.topic:
        title = request.topic
    elif request.initial_message:
        title = request.initial_message[:80]
    else:
        title = "New Chat"

    # Create session
    session = ChatSession(
        user_id=current_user.id,
        content_id=request.content_id,
        title=title,
        session_type=session_type,
        topic=request.topic,
        context_snapshot=context_snapshot,
        llm_model=model_spec,
        llm_provider=provider,
        created_at=datetime.now(UTC),
    )

    db.add(session)
    db.commit()
    db.refresh(session)

    if request.content_id:
        try:
            sync_personal_markdown_for_content(
                db,
                user_id=current_user.id,
                content_id=request.content_id,
            )
        except Exception:
            logger.exception(
                "Failed to sync personal markdown after chat session creation",
                extra=build_log_extra(
                    component="chat",
                    operation="create_session",
                    event_name="chat.session.personal_markdown",
                    status="degraded",
                    user_id=current_user.id,
                    session_id=session.id,
                    content_id=request.content_id,
                ),
            )

    logger.info(
        "Chat session created",
        extra=build_log_extra(
            component="chat",
            operation="create_session",
            event_name="chat.session",
            status="completed",
            user_id=current_user.id,
            session_id=session.id,
            content_id=request.content_id,
            context_data={"model": model_spec, "session_type": session_type},
        ),
    )

    session_summary = _session_to_summary(
        session,
        article_title,
        article_url,
        article_summary,
        article_source,
    )
    return CreateChatSessionResponse(session=session_summary)


@router.patch(
    "/sessions/{session_id}",
    response_model=ChatSessionSummaryDto,
    summary="Update chat session",
    description="Update a chat session's settings, such as the LLM provider.",
)
async def update_session(
    session_id: Annotated[int, Path(..., description="Chat session ID", gt=0)],
    request: UpdateChatSessionRequest,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ChatSessionSummaryDto:
    """Update a chat session's provider or other settings.

    Allows switching LLM provider mid-conversation while preserving chat history.
    """
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this session")

    # Update provider if specified
    if request.llm_provider is not None:
        provider, model_spec = resolve_model(request.llm_provider, request.llm_model_hint)
        session.llm_provider = provider
        session.llm_model = model_spec
        session.updated_at = datetime.now(UTC)

        logger.info(
            "Chat session provider changed",
            extra=build_log_extra(
                component="chat",
                operation="update_session",
                event_name="chat.session_provider_changed",
                status="completed",
                user_id=current_user.id,
                session_id=session.id,
                context_data={"model": model_spec},
            ),
        )

    db.commit()
    db.refresh(session)

    # Get article title and URL if content_id exists
    article_title = None
    article_url = None
    article_summary = None
    article_source = None
    if session.content_id:
        content = db.query(Content).filter(Content.id == session.content_id).first()
        if content:
            article_title = _resolve_article_title(content)
            article_url = content.url
            article_summary = _extract_short_summary(content)
            article_source = content.source

    return _session_to_summary(
        session,
        article_title,
        article_url,
        article_summary,
        article_source,
    )


@router.get(
    "/sessions/{session_id}",
    response_model=ChatSessionDetailDto,
    summary="Get chat session details",
    description="Get a chat session with its message history.",
)
async def get_session(
    session_id: Annotated[int, Path(..., description="Chat session ID", gt=0)],
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ChatSessionDetailDto:
    """Get chat session details with message history."""
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this session")

    # Get article title and URL
    article_title = None
    article_url = None
    article_summary = None
    article_source = None
    if session.content_id:
        content = db.query(Content).filter(Content.id == session.content_id).first()
        if content:
            article_title = _resolve_article_title(content)
            article_url = content.url
            article_summary = _extract_short_summary(content)
            article_source = content.source

    # Load messages
    messages = _extract_messages_for_display(db, session_id)
    if session.council_mode:
        active_child_session = _resolve_active_child_session(db, session)
        if active_child_session is not None:
            branch_messages = _extract_messages_for_display(
                db,
                active_child_session.id,
                session_id_override=session.id,
                min_message_id_exclusive=active_child_session.branch_start_message_id,
            )
            messages.extend(branch_messages)

    session_summary = _session_to_summary(
        session,
        article_title,
        article_url,
        article_summary,
        article_source,
    )
    return ChatSessionDetailDto(session=session_summary, messages=messages)


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete chat session",
    description="Soft-delete a chat session for the current user by archiving it.",
)
async def delete_session(
    session_id: Annotated[int, Path(..., description="Chat session ID", gt=0)],
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Response:
    """Archive a chat session for the current user."""
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this session")

    if not session.is_archived:
        session.is_archived = True
        session.updated_at = datetime.now(UTC)
        if session.council_mode:
            (
                db.query(ChatSession)
                .filter(ChatSession.parent_session_id == session.id)
                .update(
                    {
                        ChatSession.is_archived: True,
                        ChatSession.updated_at: datetime.now(UTC),
                    },
                    synchronize_session=False,
                )
            )
        db.commit()

    logger.info(
        "Chat session archived",
        extra=build_log_extra(
            component="chat",
            operation="delete_session",
            event_name="chat.session_deleted",
            status="completed",
            user_id=current_user.id,
            session_id=session.id,
        ),
    )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/sessions/{session_id}/messages",
    response_model=SendMessageResponse,
    summary="Send message (async)",
    description=(
        "Send a message in a chat session. Returns immediately with a message_id "
        "to poll for completion. The assistant response is processed in the background."
    ),
)
async def send_message(
    session_id: Annotated[int, Path(..., description="Chat session ID", gt=0)],
    request: SendChatMessageRequest,
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> SendMessageResponse:
    """Send a message and start async processing.

    Returns immediately with the user message and a message_id.
    Poll GET /messages/{message_id}/status for completion.
    """
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this session")

    effective_session = session
    if session.council_mode:
        active_child_session = _resolve_active_child_session(db, session)
        if active_child_session is None:
            raise HTTPException(status_code=400, detail="No active council branch selected")
        effective_session = active_child_session

    logger.info(
        "Chat message accepted",
        extra=build_log_extra(
            component="chat",
            operation="send_message",
            event_name="chat.turn",
            status="started",
            user_id=current_user.id,
            session_id=effective_session.id,
            context_data={"model": effective_session.llm_model},
        ),
    )

    # Create the processing message record immediately
    db_message = create_processing_message(db, effective_session.id, request.message)
    effective_session.last_message_at = datetime.now(UTC)
    effective_session.updated_at = datetime.now(UTC)
    if session.council_mode:
        session.last_message_at = effective_session.last_message_at
        session.updated_at = effective_session.updated_at
    db.commit()

    trimmed_msg = request.message.replace("\n", " ")[:100]
    if len(request.message) > 100:
        trimmed_msg = f"{trimmed_msg}..."
    logger.info(
        "[Chat:SEND] sid=%s mid=%s user=%s prompt='%s'",
        session_id,
        db_message.id,
        current_user.id,
        trimmed_msg,
    )

    # Start async processing using BackgroundTasks (not asyncio.create_task which can be GC'd)
    if session.council_mode:
        background_tasks.add_task(
            process_message_async,
            effective_session.id,
            db_message.id,
            request.message,
            source="council",
        )
    elif effective_session.session_type == "deep_research":
        from app.services.deep_research import process_deep_research_message

        background_tasks.add_task(
            process_deep_research_message, effective_session.id, db_message.id, request.message
        )
    elif effective_session.session_type in ASSISTANT_SESSION_TYPES:
        background_tasks.add_task(
            process_assistant_turn_async,
            effective_session.id,
            db_message.id,
            request.message,
            screen_context=AssistantScreenContext(
                screen_type=effective_session.session_type,
                screen_title=effective_session.title,
                content_id=effective_session.content_id,
            ),
        )
    else:
        background_tasks.add_task(
            process_message_async, effective_session.id, db_message.id, request.message
        )

    user_message = _build_processing_user_message(
        db_message=db_message,
        session_id=session.id,
        content=request.message,
    )

    return SendMessageResponse(
        session_id=session.id,
        user_message=user_message,
        message_id=db_message.id,
        status=MessageProcessingStatusDto.PROCESSING,
    )


@router.post(
    "/assistant/turns",
    response_model=AssistantTurnResponse,
    summary="Create or continue a contextual assistant turn",
)
async def create_assistant_turn(
    request: AssistantTurnRequest,
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> AssistantTurnResponse:
    """Create or continue an assistant-driven chat turn with screen context."""
    screen_context: AssistantScreenContext = request.screen_context
    session: ChatSession

    if request.session_id is not None:
        session = db.query(ChatSession).filter(ChatSession.id == request.session_id).first()
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        if session.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not authorized to access this session")
        _refresh_assistant_session_context(
            db=db,
            session=session,
            user_id=current_user.id,
            screen_context=screen_context,
        )
    else:
        context_snapshot = build_screen_context_snapshot(
            db,
            user_id=current_user.id,
            screen_context=screen_context,
        )
        session = create_assistant_session(
            db,
            user_id=current_user.id,
            context_snapshot=context_snapshot,
            screen_context=screen_context,
            initial_message=request.message,
        )

    logger.info(
        "Assistant turn accepted",
        extra=build_log_extra(
            component="assistant_turn",
            operation="create_turn",
            event_name="assistant.turn",
            status="started",
            user_id=current_user.id,
            session_id=session.id,
            content_id=screen_context.content_id,
            context_data={
                "model": session.llm_model,
                "screen_type": screen_context.screen_type,
            },
        ),
    )

    db_message = create_processing_message(db, session.id, request.message)
    background_tasks.add_task(
        process_assistant_turn_async,
        session.id,
        db_message.id,
        request.message,
        screen_context=screen_context,
    )

    article_title = None
    article_url = None
    article_summary = None
    article_source = None
    if session.content_id:
        content = db.query(Content).filter(Content.id == session.content_id).first()
        if content:
            article_title = _resolve_article_title(content)
            article_url = content.url
            article_summary = _extract_short_summary(content)
            article_source = content.source

    return AssistantTurnResponse(
        session=_session_to_summary(
            session,
            article_title,
            article_url,
            article_summary,
            article_source,
        ),
        user_message=_build_processing_user_message(
            db_message=db_message,
            session_id=session.id,
            content=request.message,
        ),
        message_id=db_message.id,
        status=MessageProcessingStatusDto.PROCESSING,
    )


@router.get(
    "/messages/{message_id}/status",
    response_model=MessageStatusResponse,
    summary="Poll message status",
    description=(
        "Poll for the status of an async message. Returns the assistant response when completed."
    ),
)
async def get_message_status(
    message_id: Annotated[int, Path(..., description="Message ID to poll", gt=0)],
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> MessageStatusResponse:
    """Poll for message completion status.

    Returns the current status and assistant message if completed.
    Poll every 500ms-1s until status is 'completed' or 'failed'.
    """
    from pydantic_ai.messages import (
        ModelMessagesTypeAdapter,
        ModelResponse,
        TextPart,
    )

    db_message = db.query(ChatMessage).filter(ChatMessage.id == message_id).first()

    if not db_message:
        raise HTTPException(status_code=404, detail="Message not found")

    # Verify ownership via session
    session = db.query(ChatSession).filter(ChatSession.id == db_message.session_id).first()

    if not session or session.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this message")

    status = MessageProcessingStatusDto(db_message.status)

    # If still processing, return status only
    if status == MessageProcessingStatusDto.PROCESSING:
        return MessageStatusResponse(
            message_id=message_id,
            status=status,
            assistant_message=None,
            error=None,
        )

    # If failed, return status with error
    if status == MessageProcessingStatusDto.FAILED:
        return MessageStatusResponse(
            message_id=message_id,
            status=status,
            assistant_message=None,
            error=db_message.error,
        )

    # If completed, extract assistant message
    try:
        msg_list = ModelMessagesTypeAdapter.validate_json(db_message.message_list)
        render_metadata = _load_render_metadata(db_message)

        # Find the last assistant text response
        assistant_content = None
        for model_msg in reversed(msg_list):
            if isinstance(model_msg, ModelResponse):
                for part in model_msg.parts:
                    if isinstance(part, TextPart) and part.content:
                        assistant_content = part.content
                        break
                if assistant_content:
                    break

        if not assistant_content:
            raise HTTPException(status_code=500, detail="Assistant response missing")

        assistant_message = ChatMessageDto(
            id=_build_async_assistant_display_id(message_id),
            source_message_id=message_id,
            session_id=session.parent_session_id or db_message.session_id,
            role=ChatMessageRole.ASSISTANT,
            content=assistant_content,
            timestamp=db_message.created_at,
            status=MessageProcessingStatusDto.COMPLETED,
            feed_options=render_metadata.feed_options if render_metadata else [],
            council_candidates=render_metadata.council_candidates if render_metadata else [],
            active_council_child_session_id=(
                render_metadata.active_council_child_session_id if render_metadata else None
            ),
        )

        return MessageStatusResponse(
            message_id=message_id,
            status=status,
            assistant_message=assistant_message,
            error=None,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to extract assistant message: {e}")
        raise HTTPException(status_code=500, detail="Failed to parse message") from None


@router.post(
    "/sessions/{session_id}/council/start",
    response_model=ChatSessionDetailDto,
    summary="Start council mode",
)
async def start_council_mode(
    session_id: Annotated[int, Path(..., description="Chat session ID", gt=0)],
    request: CouncilStartRequest,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ChatSessionDetailDto:
    """Fork the current chat into four persona branches and persist the council row."""

    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this session")

    try:
        await start_council_chat(
            db,
            parent_session=session,
            user=current_user,
            user_prompt=request.message,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return await get_session(session_id=session_id, db=db, current_user=current_user)


@router.post(
    "/sessions/{session_id}/council/select",
    response_model=ChatSessionDetailDto,
    summary="Select council branch",
)
async def select_council_mode_branch(
    session_id: Annotated[int, Path(..., description="Chat session ID", gt=0)],
    request: CouncilSelectRequest,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ChatSessionDetailDto:
    """Switch the active council branch and return the merged parent transcript."""

    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this session")
    if not session.council_mode:
        raise HTTPException(status_code=400, detail="Council mode is not active for this chat")

    try:
        select_council_branch(
            db,
            parent_session=session,
            child_session_id=request.child_session_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return await get_session(session_id=session_id, db=db, current_user=current_user)


@router.post(
    "/sessions/{session_id}/initial-suggestions",
    response_model=ChatMessageDto,
    summary="Get initial suggestions",
    description=(
        "Generate initial follow-up question suggestions for an article-based session. "
        "Only works for sessions with a content_id (article-based sessions)."
    ),
)
async def get_initial_suggestions(
    session_id: Annotated[int, Path(..., description="Chat session ID", gt=0)],
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ChatMessageDto:
    """Get initial follow-up question suggestions for an article-based session."""
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this session")

    if not session.content_id:
        raise HTTPException(
            status_code=400,
            detail="Initial suggestions only available for article-based sessions",
        )

    logger.info(
        "Initial suggestions requested",
        extra=build_log_extra(
            component="chat",
            operation="initial_suggestions",
            event_name="chat.initial_suggestions",
            status="started",
            user_id=current_user.id,
            session_id=session_id,
            context_data={"model": session.llm_model},
        ),
    )

    result = await generate_initial_suggestions(db, session)
    if result is None:
        raise HTTPException(status_code=500, detail="Unable to generate suggestions")

    messages = _extract_messages_for_display(db, session_id)
    assistant_message = next(
        (msg for msg in reversed(messages) if msg.role == ChatMessageRole.ASSISTANT),
        None,
    )
    if assistant_message is None:
        raise HTTPException(status_code=500, detail="Assistant response missing")

    logger.info(
        "Initial suggestions completed",
        extra=build_log_extra(
            component="chat",
            operation="initial_suggestions",
            event_name="chat.initial_suggestions",
            status="completed",
            user_id=current_user.id,
            session_id=session_id,
            context_data={"model": session.llm_model},
        ),
    )

    return assistant_message
