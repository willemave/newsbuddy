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
from sqlalchemy.orm import Session

from app.commands import create_chat_session as create_chat_session_command
from app.commands import send_chat_message as send_chat_message_command
from app.core.db import get_db_session, get_readonly_db_session
from app.core.deps import get_current_user, require_user_id
from app.core.logging import get_logger
from app.core.observability import build_log_extra
from app.models.api.chat import (
    AssistantTurnRequest,
    AssistantTurnResponse,
    ChatMessageDto,
    ChatSessionDetailDto,
    ChatSessionListResponse,
    ChatSessionSummaryDto,
    CouncilRetryRequest,
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
from app.models.contracts import ChatMessageRole
from app.models.db import (
    ChatSession,
    Content,
    NewsItem,
)
from app.models.db.users import User
from app.models.internal.assistant import AssistantScreenContext
from app.queries import chat_read_models as _chat_read_models
from app.queries import get_chat_message_status as get_chat_message_status_query
from app.queries import get_chat_session as get_chat_session_query
from app.queries import list_chat_sessions as list_chat_sessions_query
from app.queries.chat_read_models import (
    build_processing_user_message as _build_processing_user_message,
)
from app.queries.chat_read_models import (
    build_session_summaries as _build_session_summaries,
)
from app.queries.chat_read_models import (
    extract_messages_for_display as _extract_messages_for_display,
)
from app.queries.chat_read_models import (
    extract_short_summary as _extract_short_summary,
)
from app.queries.chat_read_models import (
    news_item_article_metadata as _news_item_article_metadata,
)
from app.queries.chat_read_models import (
    require_message_id as _require_message_id,
)
from app.queries.chat_read_models import (
    require_session_id as _require_session_id,
)
from app.queries.chat_read_models import (
    require_timestamp as _require_timestamp,
)
from app.queries.chat_read_models import (
    resolve_article_title as _resolve_article_title,
)
from app.queries.chat_read_models import (
    resolve_news_item_title as _resolve_news_item_title,
)
from app.queries.chat_read_models import (
    session_to_summary as _session_to_summary,
)
from app.services.assistant_router import (
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
    retry_council_branch,
    select_council_branch,
    start_council_chat,
)
from app.services.llm_models import (
    is_deep_research_provider,
    resolve_model,
)
from app.services.news_feed import get_visible_news_item

logger = get_logger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

_extract_last_message_preview = _chat_read_models.extract_last_message_preview
_format_process_summary_label = _chat_read_models.format_process_summary_label


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
    session.news_item_id = screen_context.news_item_id
    session.topic = screen_context.selected_topic

    title = screen_context.screen_title or session.title or "Knowledge Chat"
    if screen_context.content_id is not None:
        content = db.query(Content).filter(Content.id == screen_context.content_id).first()
        if content is not None:
            resolved_title = _resolve_article_title(content)
            if resolved_title:
                title = resolved_title
    elif screen_context.news_item_id is not None:
        item = get_visible_news_item(
            db,
            user_id=user_id,
            news_item_id=screen_context.news_item_id,
        )
        if item is not None:
            title = _resolve_news_item_title(item)
    session.title = title[:500]
    session.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(session)


@router.get(
    "/sessions",
    response_model=list[ChatSessionSummaryDto],
    summary="List chat sessions",
    description="List all chat sessions for the current user, ordered by most recent activity.",
)
def list_sessions(
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    content_id: Annotated[int | None, Query(description="Filter by content ID")] = None,
    news_item_id: Annotated[int | None, Query(description="Filter by news item ID")] = None,
    limit: Annotated[int, Query(ge=1, le=100, description="Maximum sessions to return")] = 50,
) -> list[ChatSessionSummaryDto]:
    """List chat sessions for the current user.

    Returns sessions ordered by last_message_at (most recent first),
    falling back to created_at for sessions without messages.
    """
    if content_id is not None and news_item_id is not None:
        raise HTTPException(status_code=400, detail="Use either content_id or news_item_id")
    user_id = require_user_id(current_user)
    return list_chat_sessions_query.execute(
        db,
        user_id=user_id,
        content_id=content_id,
        news_item_id=news_item_id,
        limit=limit,
    )


@router.get(
    "/sessions/list",
    response_model=ChatSessionListResponse,
    summary="List chat sessions page",
    description="List a page of chat sessions for the current user.",
)
def list_sessions_page(
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    content_id: Annotated[int | None, Query(description="Filter by content ID")] = None,
    news_item_id: Annotated[int | None, Query(description="Filter by news item ID")] = None,
    cursor: Annotated[str | None, Query(description="Pagination cursor for next page")] = None,
    limit: Annotated[int, Query(ge=1, le=100, description="Maximum sessions to return")] = 25,
) -> ChatSessionListResponse:
    """List one cursor-paginated page of chat sessions."""
    if content_id is not None and news_item_id is not None:
        raise HTTPException(status_code=400, detail="Use either content_id or news_item_id")
    user_id = require_user_id(current_user)
    return list_chat_sessions_query.execute_page(
        db,
        user_id=user_id,
        content_id=content_id,
        news_item_id=news_item_id,
        cursor=cursor,
        limit=limit,
    )


@router.post(
    "/sessions",
    response_model=CreateChatSessionResponse,
    summary="Create chat session",
    description="Create a new chat session, optionally associated with an article.",
)
def create_session(
    request: CreateChatSessionRequest,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> CreateChatSessionResponse:
    """Create a new chat session.

    If content_id is provided, the session will be associated with that article
    and the article's context will be available to the chat agent.
    """
    user_id = require_user_id(current_user)
    return create_chat_session_command.execute(db, user_id=user_id, request=request)


@router.patch(
    "/sessions/{session_id}",
    response_model=ChatSessionSummaryDto,
    summary="Update chat session",
    description="Update a chat session's settings, such as the LLM provider.",
)
def update_session(
    session_id: Annotated[int, Path(..., description="Chat session ID", gt=0)],
    request: UpdateChatSessionRequest,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ChatSessionSummaryDto:
    """Update a chat session's provider or other settings.

    Allows switching LLM provider mid-conversation while preserving chat history.
    """
    user_id = require_user_id(current_user)
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to access this session")

    # Update provider if specified
    if request.llm_provider is not None:
        if is_deep_research_provider(request.llm_provider):
            raise HTTPException(
                status_code=400,
                detail="Deep research must be started as a dedicated deep research session",
            )
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
                user_id=user_id,
                session_id=_require_session_id(session),
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
    elif session.news_item_id:
        news_item = db.query(NewsItem).filter(NewsItem.id == session.news_item_id).first()
        if news_item:
            article_title, article_url, article_summary, article_source = (
                _news_item_article_metadata(news_item)
            )

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
def get_session(
    session_id: Annotated[int, Path(..., description="Chat session ID", gt=0)],
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ChatSessionDetailDto:
    """Get chat session details with message history."""
    user_id = require_user_id(current_user)
    return get_chat_session_query.execute(db, user_id=user_id, session_id=session_id)


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete chat session",
    description="Soft-delete a chat session for the current user by archiving it.",
)
def delete_session(
    session_id: Annotated[int, Path(..., description="Chat session ID", gt=0)],
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Response:
    """Archive a chat session for the current user."""
    user_id = require_user_id(current_user)
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.user_id != user_id:
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
            user_id=user_id,
            session_id=_require_session_id(session),
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
def send_message(
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
    user_id = require_user_id(current_user)
    return send_chat_message_command.execute(
        db,
        user_id=user_id,
        session_id=session_id,
        request=request,
        background_tasks=background_tasks,
        process_message=process_message_async,
        process_assistant_turn=process_assistant_turn_async,
    )


@router.post(
    "/assistant/turns",
    response_model=AssistantTurnResponse,
    summary="Create or continue a contextual assistant turn",
)
def create_assistant_turn(
    request: AssistantTurnRequest,
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> AssistantTurnResponse:
    """Create or continue an assistant-driven chat turn with screen context."""
    user_id = require_user_id(current_user)
    screen_context: AssistantScreenContext = request.screen_context
    session: ChatSession
    if screen_context.news_item_id is not None and not get_visible_news_item(
        db,
        user_id=user_id,
        news_item_id=screen_context.news_item_id,
    ):
        raise HTTPException(status_code=404, detail="News item not found")

    if request.session_id is not None:
        existing_session = (
            db.query(ChatSession).filter(ChatSession.id == request.session_id).first()
        )
        if existing_session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        if existing_session.user_id != user_id:
            raise HTTPException(status_code=403, detail="Not authorized to access this session")
        session = existing_session
        _refresh_assistant_session_context(
            db=db,
            session=session,
            user_id=user_id,
            screen_context=screen_context,
        )
    else:
        context_snapshot = build_screen_context_snapshot(
            db,
            user_id=user_id,
            screen_context=screen_context,
        )
        session = create_assistant_session(
            db,
            user_id=user_id,
            context_snapshot=context_snapshot,
            screen_context=screen_context,
            initial_message=request.message,
        )
    session_row_id = _require_session_id(session)

    logger.info(
        "Assistant turn accepted",
        extra=build_log_extra(
            component="assistant_turn",
            operation="create_turn",
            event_name="assistant.turn",
            status="started",
            user_id=user_id,
            session_id=session_row_id,
            content_id=screen_context.content_id,
            context_data={
                "model": session.llm_model,
                "screen_type": screen_context.screen_type,
            },
        ),
    )

    db_message = create_processing_message(db, session_row_id, request.message)
    message_id = _require_message_id(db_message)
    message_created_at = _require_timestamp(
        db_message.created_at,
        detail="Chat message missing created_at",
    )
    session.last_message_at = message_created_at
    session.updated_at = message_created_at
    db.commit()
    db.refresh(session)

    background_tasks.add_task(
        process_assistant_turn_async,
        session_row_id,
        message_id,
        request.message,
        screen_context=screen_context,
    )

    return AssistantTurnResponse(
        session=_build_session_summaries(db, user_id=user_id, sessions=[session])[0],
        user_message=_build_processing_user_message(
            db_message=db_message,
            session_id=session_row_id,
            content=request.message,
        ),
        message_id=message_id,
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
def get_message_status(
    message_id: Annotated[int, Path(..., description="Message ID to poll", gt=0)],
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> MessageStatusResponse:
    """Poll for message completion status.

    Returns the current status and assistant message if completed.
    Poll every 500ms-1s until status is 'completed' or 'failed'.
    """
    user_id = require_user_id(current_user)
    return get_chat_message_status_query.execute(db, user_id=user_id, message_id=message_id)


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

    user_id = require_user_id(current_user)
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != user_id:
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

    return get_session(session_id=session_id, db=db, current_user=current_user)


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

    user_id = require_user_id(current_user)
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != user_id:
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

    return get_session(session_id=session_id, db=db, current_user=current_user)


@router.post(
    "/sessions/{session_id}/council/retry",
    response_model=ChatSessionDetailDto,
    summary="Retry council branch",
)
async def retry_council_mode_branch(
    session_id: Annotated[int, Path(..., description="Chat session ID", gt=0)],
    request: CouncilRetryRequest,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ChatSessionDetailDto:
    """Regenerate one council branch and return the merged parent transcript."""

    user_id = require_user_id(current_user)
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to access this session")
    if not session.council_mode:
        raise HTTPException(status_code=400, detail="Council mode is not active for this chat")

    try:
        await retry_council_branch(
            db,
            parent_session=session,
            child_session_id=request.child_session_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return get_session(session_id=session_id, db=db, current_user=current_user)


@router.post(
    "/sessions/{session_id}/initial-suggestions",
    response_model=ChatMessageDto,
    summary="Get initial suggestions",
    description=(
        "Generate initial follow-up question suggestions for an article-based session. "
        "Only works for sessions with content or news context."
    ),
)
async def get_initial_suggestions(
    session_id: Annotated[int, Path(..., description="Chat session ID", gt=0)],
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ChatMessageDto:
    """Get initial follow-up question suggestions for an article-based session."""
    user_id = require_user_id(current_user)
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to access this session")

    if not session.content_id and not session.news_item_id:
        raise HTTPException(
            status_code=400,
            detail="Initial suggestions only available for contextual sessions",
        )

    logger.info(
        "Initial suggestions requested",
        extra=build_log_extra(
            component="chat",
            operation="initial_suggestions",
            event_name="chat.initial_suggestions",
            status="started",
            user_id=user_id,
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
            user_id=user_id,
            session_id=session_id,
            context_data={"model": session.llm_model},
        ),
    )

    return assistant_message
