"""Chat session endpoints for deep-dive conversations."""

from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Path,
    Query,
    Response,
    status,
)
from sqlalchemy.orm import Session

from app.commands import create_assistant_turn as create_assistant_turn_command
from app.commands import create_chat_session as create_chat_session_command
from app.commands import delete_chat_session as delete_chat_session_command
from app.commands import send_chat_message as send_chat_message_command
from app.commands import update_chat_session as update_chat_session_command
from app.core.db import get_db_session, get_readonly_db_session, get_session_factory
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
from app.models.contracts import ChatMessageRole
from app.models.db import ChatSession
from app.models.db.users import User
from app.queries import get_chat_message_status as get_chat_message_status_query
from app.queries import get_chat_session as get_chat_session_query
from app.queries import list_chat_sessions as list_chat_sessions_query
from app.queries.chat_read_models import extract_messages_for_display
from app.services.chat_agent import generate_initial_suggestions
from app.services.council_chat import (
    retry_council_branch,
    select_council_branch,
    start_council_chat,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


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
    return update_chat_session_command.execute(
        db,
        user_id=require_user_id(current_user),
        session_id=session_id,
        request=request,
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
    delete_chat_session_command.execute(
        db,
        user_id=require_user_id(current_user),
        session_id=session_id,
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
    )


@router.post(
    "/assistant/turns",
    response_model=AssistantTurnResponse,
    summary="Create or continue a contextual assistant turn",
)
def create_assistant_turn(
    request: AssistantTurnRequest,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> AssistantTurnResponse:
    """Create or continue an assistant-driven chat turn with screen context."""
    return create_assistant_turn_command.execute(
        db,
        user_id=require_user_id(current_user),
        request=request,
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
    model_spec = session.llm_model

    logger.info(
        "Initial suggestions requested",
        extra=build_log_extra(
            component="chat",
            operation="initial_suggestions",
            event_name="chat.initial_suggestions",
            status="started",
            user_id=user_id,
            session_id=session_id,
            context_data={"model": model_spec},
        ),
    )

    db.close()
    result = await generate_initial_suggestions(session_id)
    if result is None:
        raise HTTPException(status_code=500, detail="Unable to generate suggestions")

    session_factory = get_session_factory()
    with session_factory() as response_db:
        messages = extract_messages_for_display(response_db, session_id, user_id=user_id)
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
            context_data={"model": model_spec},
        ),
    )

    return assistant_message
