"""Helpers for auto-starting dig-deeper chats."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.db import ChatSession, Content, ContentDiscussion, ProcessingTask
from app.services.chat_agent import create_processing_message, process_message_async
from app.services.llm_models import DEFAULT_MODEL, DEFAULT_PROVIDER
from app.services.personal_markdown_library import sync_personal_markdown_for_content
from app.services.queue import TaskQueue, TaskStatus, TaskType
from app.utils.title_utils import resolve_content_display_title

logger = get_logger(__name__)

KNOWLEDGE_SESSION_TYPE = "knowledge_chat"
DIG_DEEPER_PROMPT_TEMPLATE = (
    "Dig deeper into the key points of {title}. For each main point, explain reasoning, "
    "supporting evidence, and include a bit more detail explaining the point. "
    "Also pull out key ideas from the discussion context when available, and add more insights "
    "from the discussion, including notable agreements and disagreements. "
    "Keep answers concise and numbered."
)
MAX_DISCUSSION_COMMENT_SNIPPETS = 8
MAX_DISCUSSION_GROUP_SNIPPETS = 4
MAX_DISCUSSION_SNIPPET_CHARS = 220


def _require_session_id(session: ChatSession) -> int:
    """Return a persisted chat-session ID or raise."""
    session_id = session.id
    if session_id is None:
        raise ValueError("Chat session must be persisted before use")
    return session_id


def _require_content_id(content: Content) -> int:
    """Return a persisted content ID or raise."""
    content_id = content.id
    if content_id is None:
        raise ValueError("Content must be persisted before use")
    return int(content_id)


def _require_message_id(message: Any) -> int:
    """Return a persisted chat-message ID or raise."""
    message_id = getattr(message, "id", None)
    if message_id is None:
        raise ValueError("Chat message must be persisted before use")
    return int(message_id)


def _require_task_id(task: ProcessingTask) -> int:
    """Return a persisted processing-task ID or raise."""
    task_id = task.id
    if task_id is None:
        raise ValueError("Processing task must be persisted before use")
    return task_id


def _build_content_context_snapshot(content: Content, user_id: int) -> str:
    """Build a compact content-grounding snapshot without importing assistant_router."""

    display_title = resolve_content_display_title(
        title=content.title,
        metadata=content.content_metadata,
        fallback="Untitled",
    )
    lines = [
        f"Screen Type: {KNOWLEDGE_SESSION_TYPE}",
        "Screen Title: Knowledge",
        "Visible Content:",
        f"- [{content.id}] {display_title} ({content.source or 'unknown'}) — {content.url}",
    ]
    if content.short_summary:
        lines.append(f"  Short Summary: {content.short_summary}")
    lines.append(f"User ID: {user_id}")
    return "\n".join(lines)


def _truncate_snippet(text: str, max_chars: int = MAX_DISCUSSION_SNIPPET_CHARS) -> str:
    """Normalize and cap prompt snippets."""
    normalized = " ".join(text.split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."


def _extract_comment_snippets(data: dict[str, Any]) -> list[str]:
    """Extract compact discussion comments for prompt context."""
    snippets: list[str] = []

    compact_comments = data.get("compact_comments")
    if isinstance(compact_comments, list):
        for raw in compact_comments:
            if not isinstance(raw, str):
                continue
            snippet = _truncate_snippet(raw.strip())
            if not snippet or snippet in snippets:
                continue
            snippets.append(snippet)
            if len(snippets) >= MAX_DISCUSSION_COMMENT_SNIPPETS:
                return snippets

    comments = data.get("comments")
    if isinstance(comments, list):
        for raw in comments:
            if not isinstance(raw, dict):
                continue
            value = raw.get("compact_text") or raw.get("text")
            if not isinstance(value, str):
                continue
            snippet = _truncate_snippet(value.strip())
            if not snippet or snippet in snippets:
                continue
            snippets.append(snippet)
            if len(snippets) >= MAX_DISCUSSION_COMMENT_SNIPPETS:
                return snippets

    return snippets


def _extract_group_snippets(data: dict[str, Any]) -> list[str]:
    """Extract discussion-group labels/items for prompt context."""
    snippets: list[str] = []
    groups = data.get("discussion_groups")
    if not isinstance(groups, list):
        return snippets

    for raw_group in groups:
        if not isinstance(raw_group, dict):
            continue
        label = str(raw_group.get("label") or "Discussion").strip()
        raw_items = raw_group.get("items")
        if not isinstance(raw_items, list):
            continue

        titles: list[str] = []
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                continue
            raw_title = raw_item.get("title") or raw_item.get("url")
            if not isinstance(raw_title, str):
                continue
            title = _truncate_snippet(raw_title.strip(), max_chars=90)
            if not title or title in titles:
                continue
            titles.append(title)
            if len(titles) >= 3:
                break

        if not titles:
            continue
        snippets.append(f"{label}: {', '.join(titles)}")
        if len(snippets) >= MAX_DISCUSSION_GROUP_SNIPPETS:
            break

    return snippets


def _build_discussion_context(db: Session, content_id: int | None) -> str | None:
    """Build a compact discussion-context block for dig-deeper prompts."""
    if content_id is None:
        return None

    discussion = (
        db.query(ContentDiscussion).filter(ContentDiscussion.content_id == content_id).first()
    )
    if discussion is None:
        return None

    data = discussion.discussion_data if isinstance(discussion.discussion_data, dict) else {}
    if not data:
        return None

    comment_snippets = _extract_comment_snippets(data)
    group_snippets = _extract_group_snippets(data)
    if not comment_snippets and not group_snippets:
        return None

    lines: list[str] = ["Discussion context:"]
    if comment_snippets:
        lines.append("Comment highlights:")
        lines.extend(f"- {snippet}" for snippet in comment_snippets)
    if group_snippets:
        lines.append("Discussion thread topics:")
        lines.extend(f"- {snippet}" for snippet in group_snippets)

    return "\n".join(lines)


def resolve_display_title(content: Content) -> str:
    """Resolve a display-friendly title for dig-deeper prompts.

    Args:
        content: Content record.

    Returns:
        Display title string.
    """
    try:
        return resolve_content_display_title(
            title=content.title,
            metadata=content.content_metadata,
            fallback="this content",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to resolve display title for content %s: %s",
            content.id,
            exc,
            extra={
                "component": "dig_deeper",
                "operation": "resolve_display_title",
                "item_id": content.id,
            },
        )
        return "this content"


def build_dig_deeper_prompt(db: Session, content: Content) -> str:
    """Build the default dig-deeper prompt for content.

    Args:
        db: Database session for fetching optional discussion context.
        content: Content record to reference in the prompt.

    Returns:
        Prompt string for the chat agent.
    """
    title = resolve_display_title(content)
    prompt = DIG_DEEPER_PROMPT_TEMPLATE.format(title=title)
    discussion_context = _build_discussion_context(db, content.id)
    if not discussion_context:
        return prompt
    return f"{prompt}\n\n{discussion_context}"


def get_or_create_dig_deeper_session(
    db: Session,
    content: Content,
    user_id: int,
) -> ChatSession:
    """Get or create a chat session for dig-deeper workflows.

    Args:
        db: Database session.
        content: Content record.
        user_id: User requesting the dig-deeper chat.

    Returns:
        ChatSession for the content/user.
    """
    existing = (
        db.query(ChatSession)
        .filter(
            ChatSession.content_id == content.id,
            ChatSession.user_id == user_id,
            ChatSession.is_archived == False,  # noqa: E712
        )
        .first()
    )
    if existing:
        return existing

    title = resolve_display_title(content)
    session = ChatSession(
        user_id=user_id,
        content_id=content.id,
        title=title,
        session_type=KNOWLEDGE_SESSION_TYPE,
        context_snapshot=_build_content_context_snapshot(content, user_id),
        llm_provider=DEFAULT_PROVIDER,
        llm_model=DEFAULT_MODEL,
        created_at=datetime.now(UTC),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    try:
        sync_personal_markdown_for_content(
            db,
            user_id=user_id,
            content_id=_require_content_id(content),
        )
    except Exception:
        logger.exception(
            "Failed to sync personal markdown for dig-deeper session",
            extra={
                "component": "dig_deeper",
                "operation": "create_session",
                "item_id": content.id,
                "context_data": {"user_id": user_id, "session_id": session.id},
            },
        )
    return session


def create_dig_deeper_message(
    db: Session,
    content: Content,
    user_id: int,
    initial_message: str | None = None,
) -> tuple[int, int, str]:
    """Create a processing message for a dig-deeper chat.

    Args:
        db: Database session.
        content: Content record.
        user_id: User requesting the dig-deeper chat.

    Returns:
        Tuple of (session_id, message_id, prompt).
    """
    session = get_or_create_dig_deeper_session(db, content, user_id)
    prompt = initial_message.strip() if initial_message and initial_message.strip() else None
    if prompt is None:
        prompt = build_dig_deeper_prompt(db, content)
    session_id = _require_session_id(session)
    message = create_processing_message(db, session_id, prompt)
    return session_id, _require_message_id(message), prompt


def run_dig_deeper_message(
    session_id: int,
    message_id: int,
    prompt: str,
    *,
    task_id: int | None = None,
) -> None:
    """Run the dig-deeper message processing synchronously.

    Args:
        session_id: Chat session ID.
        message_id: Chat message ID created for processing.
        prompt: Prompt string to send.
        task_id: Optional queue task identifier for telemetry.
    """
    asyncio.run(
        process_message_async(
            session_id,
            message_id,
            prompt,
            source="queue",
            task_id=task_id,
        )
    )


def enqueue_dig_deeper_task(
    db: Session,
    content_id: int,
    user_id: int,
    *,
    initial_message: str | None = None,
) -> int:
    """Enqueue a dig-deeper task for later processing.

    Args:
        db: Database session.
        content_id: Content ID to chat about.
        user_id: User requesting dig-deeper.

    Returns:
        Processing task ID.
    """
    payload: dict[str, Any] = {"user_id": user_id}
    if initial_message and initial_message.strip():
        payload["initial_message"] = initial_message.strip()
    task = ProcessingTask(
        task_type=TaskType.DIG_DEEPER.value,
        content_id=content_id,
        payload=payload,
        status=TaskStatus.PENDING.value,
        queue_name=TaskQueue.CHAT.value,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return _require_task_id(task)
