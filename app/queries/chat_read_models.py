"""Shared chat read-model helpers."""

from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.api.chat import (
    ChatMessageDto,
    ChatSessionSummaryDto,
)
from app.models.api.chat import (
    MessageProcessingStatus as MessageProcessingStatusDto,
)
from app.models.contracts import ChatMessageDisplayType, ChatMessageRole, MessageProcessingStatus
from app.models.db import (
    ChatMessage,
    ChatSession,
    Content,
    ContentKnowledgeSave,
    NewsItem,
)
from app.models.domain.chat_render import ChatMessageRenderMetadata
from app.services.llm_models import DEFAULT_MODEL, DEFAULT_PROVIDER
from app.utils.news_titles import resolve_news_display_title
from app.utils.pagination import PaginationCursor
from app.utils.title_utils import resolve_content_display_title

logger = get_logger(__name__)

SEARCH_TOOL_NAMES = {
    "exa_web_search",
    "search_personal_library",
}

INTERNAL_USER_PROMPT_SENTINELS = (
    "Use the provided session context below",
    "Provided reference context is available below",
    "You are starting a new conversation about the article described in your context",
    "Turn instructions:",
)


def require_session_id(session: ChatSession) -> int:
    session_id = session.id
    if session_id is None:
        raise HTTPException(status_code=500, detail="Chat session missing id")
    return session_id


def require_message_id(db_message: ChatMessage) -> int:
    message_id = db_message.id
    if message_id is None:
        raise HTTPException(status_code=500, detail="Chat message missing id")
    return message_id


def require_timestamp(value: datetime | None, *, detail: str) -> datetime:
    if value is None:
        raise HTTPException(status_code=500, detail=detail)
    return value


def resolve_message_status(db_message: ChatMessage) -> MessageProcessingStatusDto:
    raw_status = db_message.status or MessageProcessingStatus.PROCESSING.value
    return MessageProcessingStatusDto(raw_status)


def count_process_summary_tools(tool_names: list[str]) -> dict[str, int]:
    tool_counts: dict[str, int] = {}
    for raw_name in tool_names:
        name = raw_name.strip() if raw_name else ""
        if name:
            tool_counts[name] = tool_counts.get(name, 0) + 1
    return tool_counts


def format_process_summary_label(
    tool_counts: dict[str, int],
    *,
    has_intermediate_assistant_text: bool,
) -> str | None:
    """Build a compact transcript label for intermediate tool/thinking activity."""
    normalized_tool_names = {name.lower() for name in tool_counts}
    tool_call_count = sum(tool_counts.values())

    if tool_call_count:
        tool_label = "tool" if tool_call_count == 1 else "tools"

        if normalized_tool_names & SEARCH_TOOL_NAMES:
            return f"Thinking • Executed {tool_call_count} {tool_label} and reviewed sources"

        return f"Thinking • Executed {tool_call_count} {tool_label} and reviewed results"

    if has_intermediate_assistant_text:
        return "Thinking • Considered the request"

    return None


def format_process_summary_detail(
    tool_counts: dict[str, int],
    *,
    has_intermediate_assistant_text: bool,
) -> str | None:
    """Build expanded transcript detail for intermediate tool/thinking activity."""
    if tool_counts:
        total_count = sum(tool_counts.values())
        tool_label = "tool call" if total_count == 1 else "tool calls"
        lines = [f"Executed {total_count} {tool_label}:"]
        for name, count in tool_counts.items():
            count_suffix = f" x{count}" if count > 1 else ""
            lines.append(f"• {name}{count_suffix}")
        return "\n".join(lines)

    if has_intermediate_assistant_text:
        return "Prepared intermediate context before writing the final answer."

    return None


def extract_visible_user_prompt(raw_content: object) -> str | None:
    """Return client-visible user text from a stored model prompt."""
    text = str(raw_content).strip()
    if not text:
        return None

    marker = "User request:\n"
    if marker in text:
        request_text = text.split(marker, 1)[1]
        for suffix in ("\n\nCurrent context:", "\n\nSession Context:", "\n\nArticle Context:"):
            if suffix in request_text:
                request_text = request_text.split(suffix, 1)[0]
                break
        request_text = request_text.strip()
        return request_text or None

    if any(sentinel in text for sentinel in INTERNAL_USER_PROMPT_SENTINELS):
        return None

    return text


def load_render_metadata(db_message: ChatMessage) -> ChatMessageRenderMetadata | None:
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


def session_to_summary(
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
    session_id = require_session_id(session)
    created_at = require_timestamp(session.created_at, detail="Chat session missing created_at")
    return ChatSessionSummaryDto(
        id=session_id,
        content_id=session.content_id,
        news_item_id=session.news_item_id,
        title=session.title,
        session_type=session.session_type,
        topic=session.topic,
        llm_provider=session.llm_provider or DEFAULT_PROVIDER,
        llm_model=session.llm_model or DEFAULT_MODEL,
        created_at=created_at,
        updated_at=session.updated_at,
        last_message_at=session.last_message_at,
        article_title=article_title,
        article_url=article_url,
        article_summary=article_summary,
        article_source=article_source,
        is_archived=bool(session.is_archived),
        has_pending_message=has_pending_message,
        is_saved_to_knowledge=is_saved_to_knowledge,
        has_messages=has_messages,
        last_message_preview=last_message_preview,
        last_message_role=last_message_role,
        council_mode=bool(session.council_mode),
        active_child_session_id=session.active_child_session_id,
    )


def build_processing_user_message(
    *,
    db_message: ChatMessage,
    session_id: int,
    content: str,
) -> ChatMessageDto:
    message_id = require_message_id(db_message)
    return ChatMessageDto(
        id=message_id,
        source_message_id=message_id,
        session_id=session_id,
        role=ChatMessageRole.USER,
        content=content,
        timestamp=require_timestamp(
            db_message.created_at,
            detail="Chat message missing created_at",
        ),
        status=MessageProcessingStatusDto.PROCESSING,
    )


def resolve_active_child_session(db: Session, session: ChatSession) -> ChatSession | None:
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


def build_async_assistant_display_id(message_id: int) -> int:
    """Build a stable display ID for an async assistant reply."""
    return 1_000_000_000 + message_id


def resolve_article_title(content: Content) -> str | None:
    """Resolve a chat-friendly title from content, falling back to display_title."""
    try:
        return resolve_content_display_title(
            title=content.title,
            metadata=content.content_metadata,
            fallback="Untitled",
        )
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.warning("Failed to resolve display title for content %s: %s", content.id, exc)
        return None


def resolve_news_item_title(item: NewsItem) -> str:
    """Resolve a chat-friendly title from a news item."""
    return resolve_news_display_title(
        item.raw_metadata,
        summary_text=item.summary_text,
        fallback="Untitled News Item",
    )


def resolve_news_item_source(item: NewsItem) -> str | None:
    candidates = [item.source_label, item.article_domain, item.platform, item.source_type]
    for candidate in candidates:
        if candidate and candidate.strip():
            return candidate.strip()
    return None


def news_item_article_metadata(item: NewsItem) -> tuple[str, str | None, str | None, str | None]:
    return (
        resolve_news_item_title(item),
        item.article_url or item.canonical_story_url,
        item.summary_text,
        resolve_news_item_source(item),
    )


def extract_short_summary(content: Content) -> str | None:
    """Extract short summary from content metadata."""
    return content.short_summary


def resolve_session_article_metadata(
    db: Session,
    session: ChatSession,
) -> tuple[str | None, str | None, str | None, str | None]:
    """Resolve article-like metadata for a chat session."""
    if session.content_id:
        content = db.query(Content).filter(Content.id == session.content_id).first()
        if content:
            return (
                resolve_article_title(content),
                content.url,
                extract_short_summary(content),
                content.source,
            )
    elif session.news_item_id:
        news_item = db.query(NewsItem).filter(NewsItem.id == session.news_item_id).first()
        if news_item:
            return news_item_article_metadata(news_item)
    return None, None, None, None


def extract_last_message_preview(
    db_message: ChatMessage,
    max_length: int = 200,
) -> tuple[str | None, str | None]:
    """Extract the last user/assistant text and role from a ChatMessage record."""
    from pydantic_ai.messages import (
        ModelMessagesTypeAdapter,
        ModelRequest,
        ModelResponse,
        TextPart,
        UserPromptPart,
    )

    try:
        message_list_json = db_message.message_list
        if not isinstance(message_list_json, str):
            return None, None
        msg_list = ModelMessagesTypeAdapter.validate_json(message_list_json)
    except Exception:
        return None, None

    for model_msg in reversed(msg_list):
        if isinstance(model_msg, ModelResponse):
            for response_part in reversed(model_msg.parts):
                if isinstance(response_part, TextPart) and response_part.content:
                    text = response_part.content[:max_length]
                    return text, "assistant"
        elif isinstance(model_msg, ModelRequest):
            for request_part in reversed(model_msg.parts):
                if isinstance(request_part, UserPromptPart) and request_part.content:
                    visible_text = extract_visible_user_prompt(request_part.content)
                    if visible_text:
                        return visible_text[:max_length], "user"

    return None, None


def chat_session_activity_expr():
    return func.coalesce(ChatSession.last_message_at, ChatSession.created_at)


def list_visible_chat_sessions(
    db: Session,
    *,
    user_id: int,
    content_id: int | None,
    news_item_id: int | None,
    limit: int,
    cursor: str | None = None,
    overfetch: bool = False,
) -> list[ChatSession]:
    activity_expr = chat_session_activity_expr()
    query = db.query(ChatSession).filter(
        ChatSession.user_id == user_id,
        ChatSession.is_archived == False,  # noqa: E712
        ChatSession.is_hidden_from_history == False,  # noqa: E712
    )
    if content_id is not None:
        query = query.filter(ChatSession.content_id == content_id)
    if news_item_id is not None:
        query = query.filter(ChatSession.news_item_id == news_item_id)

    if cursor:
        try:
            cursor_data = PaginationCursor.decode_cursor(cursor)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if not PaginationCursor.validate_cursor(
            cursor_data,
            {"content_id": content_id, "news_item_id": news_item_id},
        ):
            raise HTTPException(status_code=400, detail="Invalid pagination cursor for filters")

        last_id = cursor_data.last_id
        last_activity_at = cursor_data.last_created_at
        query = query.filter(
            or_(
                activity_expr < last_activity_at,
                and_(activity_expr == last_activity_at, ChatSession.id < last_id),
            )
        )

    fetch_limit = limit + 1 if overfetch else limit
    return (
        query.order_by(
            activity_expr.desc(),
            ChatSession.id.desc(),
        )
        .limit(fetch_limit)
        .all()
    )


def build_session_summaries(
    db: Session,
    *,
    user_id: int,
    sessions: list[ChatSession],
) -> list[ChatSessionSummaryDto]:
    active_child_ids = {
        session.active_child_session_id
        for session in sessions
        if session.active_child_session_id is not None
    }
    active_child_sessions: dict[int, ChatSession] = {}
    if active_child_ids:
        active_child_rows = (
            db.query(ChatSession)
            .filter(ChatSession.id.in_(active_child_ids))
            .filter(ChatSession.is_hidden_from_history == True)  # noqa: E712
            .all()
        )
        active_child_sessions = {
            child.id: child for child in active_child_rows if child.id is not None
        }

    content_ids = {session.content_id for session in sessions if session.content_id is not None}
    contents_by_id: dict[int, Content] = {}
    if content_ids:
        content_rows = db.query(Content).filter(Content.id.in_(content_ids)).all()
        contents_by_id = {content.id: content for content in content_rows if content.id is not None}

    news_item_ids = {
        session.news_item_id for session in sessions if session.news_item_id is not None
    }
    news_items_by_id: dict[int, NewsItem] = {}
    if news_item_ids:
        news_item_rows = db.query(NewsItem).filter(NewsItem.id.in_(news_item_ids)).all()
        news_items_by_id = {item.id: item for item in news_item_rows if item.id is not None}

    session_ids = {s.id for s in sessions if s.id is not None}
    preview_session_ids = session_ids | active_child_ids
    pending_session_ids: set[int] = set()
    sessions_with_messages: set[int] = set()

    if preview_session_ids:
        pending_messages = (
            db.query(ChatMessage.session_id)
            .filter(
                ChatMessage.session_id.in_(preview_session_ids),
                ChatMessage.status == MessageProcessingStatus.PROCESSING.value,
            )
            .distinct()
            .all()
        )
        pending_session_ids = {m.session_id for m in pending_messages if m.session_id is not None}

        sessions_with_any_messages = (
            db.query(ChatMessage.session_id)
            .filter(ChatMessage.session_id.in_(preview_session_ids))
            .distinct()
            .all()
        )
        sessions_with_messages = {
            m.session_id for m in sessions_with_any_messages if m.session_id is not None
        }

    last_message_map: dict[int, ChatMessage] = {}
    if preview_session_ids:
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
        last_message_map = {m.session_id: m for m in latest_messages if m.session_id is not None}

    knowledge_saved_content_ids: set[int] = set()
    if content_ids:
        knowledge_saves = (
            db.query(ContentKnowledgeSave.content_id)
            .filter(
                ContentKnowledgeSave.user_id == user_id,
                ContentKnowledgeSave.content_id.in_(content_ids),
            )
            .all()
        )
        knowledge_saved_content_ids = {
            row.content_id for row in knowledge_saves if row.content_id is not None
        }

    result: list[ChatSessionSummaryDto] = []
    for session in sessions:
        article_title = None
        article_url = None
        article_summary = None
        article_source = None

        if session.content_id:
            content = contents_by_id.get(session.content_id)
            if content:
                article_title = resolve_article_title(content)
                article_url = content.url
                article_summary = extract_short_summary(content)
                article_source = content.source
        elif session.news_item_id:
            news_item = news_items_by_id.get(session.news_item_id)
            if news_item:
                article_title, article_url, article_summary, article_source = (
                    news_item_article_metadata(news_item)
                )

        preview_session = session
        if session.council_mode and session.active_child_session_id is not None:
            candidate_child = active_child_sessions.get(session.active_child_session_id)
            if candidate_child and candidate_child.parent_session_id == session.id:
                preview_session = candidate_child

        preview_session_id = require_session_id(preview_session)
        session_row_id = require_session_id(session)
        has_pending = preview_session_id in pending_session_ids
        is_saved_to_knowledge = (
            session.content_id in knowledge_saved_content_ids if session.content_id else False
        )
        has_messages = session_row_id in sessions_with_messages

        last_preview: str | None = None
        last_role: str | None = None
        last_msg = last_message_map.get(preview_session_id)
        if last_msg:
            last_preview, last_role = extract_last_message_preview(last_msg)

        result.append(
            session_to_summary(
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


def extract_messages_for_display(
    db: Session,
    session_id: int,
    *,
    session_id_override: int | None = None,
    min_message_id_exclusive: int | None = None,
) -> list[ChatMessageDto]:
    """Load messages from DB and convert to display format."""
    from pydantic_ai.messages import (
        ModelMessagesTypeAdapter,
        ModelRequest,
        ModelResponse,
        TextPart,
        ToolCallPart,
        UserPromptPart,
    )

    messages: list[ChatMessageDto] = []
    display_id = 0

    query = db.query(ChatMessage).filter(ChatMessage.session_id == session_id)
    if min_message_id_exclusive is not None:
        query = query.filter(ChatMessage.id > min_message_id_exclusive)
    db_messages = query.order_by(ChatMessage.created_at).all()

    for db_msg in db_messages:
        try:
            message_list_json = db_msg.message_list
            if not isinstance(message_list_json, str):
                continue
            msg_list = ModelMessagesTypeAdapter.validate_json(message_list_json)
            status = resolve_message_status(db_msg)
            render_metadata = load_render_metadata(db_msg)
            assistant_responses: list[str] = []
            tool_names: list[str] = []
            user_text_emitted = False
            message_id = require_message_id(db_msg)
            message_timestamp = require_timestamp(
                db_msg.created_at,
                detail="Chat message missing created_at",
            )

            for model_msg in msg_list:
                if isinstance(model_msg, ModelRequest):
                    for request_part in model_msg.parts:
                        if user_text_emitted:
                            break
                        if isinstance(request_part, UserPromptPart) and request_part.content:
                            user_text = extract_visible_user_prompt(request_part.content)
                            if not user_text:
                                continue
                            user_text_emitted = True
                            display_id += 1
                            messages.append(
                                ChatMessageDto(
                                    id=display_id,
                                    source_message_id=message_id,
                                    session_id=session_id_override or session_id,
                                    role=ChatMessageRole.USER,
                                    timestamp=message_timestamp,
                                    content=user_text,
                                    status=status,
                                    error=db_msg.error,
                                )
                            )
                elif isinstance(model_msg, ModelResponse):
                    response_text_parts: list[str] = []
                    for response_part in model_msg.parts:
                        if isinstance(response_part, TextPart) and response_part.content:
                            response_text_parts.append(response_part.content)
                        elif isinstance(response_part, ToolCallPart):
                            tool_names.append(response_part.tool_name)

                    if response_text_parts:
                        assistant_responses.append("\n\n".join(response_text_parts))

            latest_assistant_text = assistant_responses[-1] if assistant_responses else None
            has_intermediate_assistant_text = len(assistant_responses) > 1
            tool_counts = count_process_summary_tools(tool_names)
            process_summary_label = format_process_summary_label(
                tool_counts,
                has_intermediate_assistant_text=has_intermediate_assistant_text,
            )
            process_summary_detail = format_process_summary_detail(
                tool_counts,
                has_intermediate_assistant_text=has_intermediate_assistant_text,
            )

            if process_summary_label:
                display_id += 1
                messages.append(
                    ChatMessageDto(
                        id=display_id,
                        source_message_id=message_id,
                        session_id=session_id_override or session_id,
                        role=ChatMessageRole.TOOL,
                        timestamp=message_timestamp,
                        content=process_summary_detail or process_summary_label,
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
                        id=display_id,
                        source_message_id=message_id,
                        session_id=session_id_override or session_id,
                        role=ChatMessageRole.ASSISTANT,
                        timestamp=message_timestamp,
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
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to deserialize message %s: %s", db_msg.id, exc)
            continue

    return messages
