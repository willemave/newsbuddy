"""Durable queued chat-turn handler."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from pydantic import ValidationError

from app.core.logging import get_logger
from app.models.contracts import MessageProcessingStatus, TaskType
from app.models.db import ChatMessage, ChatSession, ProcessingTask, User
from app.models.internal.chat_turn import ChatTurnProcessingContext
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope, TaskResult
from app.services.chat_turn_runtime import (
    ChatTurnLeaseCheckError,
    QueuedChatTurnOutcome,
)

logger = get_logger(__name__)

ORDER_RETRY_DELAY_SECONDS = 2
CHAT_TURN_FAILED_MESSAGE = "This chat turn could not be completed. Please retry."
CHAT_TURN_UNAVAILABLE_MESSAGE = "This chat is no longer available."


class ChatTurnHandler:
    """Execute one immutable chat turn after its predecessors finish."""

    task_type = TaskType.CHAT_TURN

    def handle(self, task: TaskEnvelope, context: TaskContext) -> TaskResult:
        """Validate ownership and ordering before invoking a chat provider."""
        payload = task.payload
        user_id = int(payload["user_id"])
        session_id = int(payload["session_id"])
        message_id = int(payload["message_id"])

        with context.db_factory() as db:
            message = db.query(ChatMessage).filter(ChatMessage.id == message_id).first()
            if message is None:
                return TaskResult.fail("Chat message not found", retryable=False)
            if message.session_id != session_id:
                return TaskResult.fail("Chat task message/session mismatch", retryable=False)
            if message.status == MessageProcessingStatus.COMPLETED.value:
                return TaskResult.ok()
            if message.status == MessageProcessingStatus.FAILED.value:
                return TaskResult.fail(CHAT_TURN_FAILED_MESSAGE, retryable=False)

            session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
            queue_task = db.query(ProcessingTask).filter(ProcessingTask.id == task.id).first()
            user = db.query(User).filter(User.id == user_id).first()
            if (
                queue_task is None
                or queue_task.owner_user_id != user_id
                or session is None
                or session.user_id != user_id
            ):
                return TaskResult.fail("Chat turn ownership validation failed", retryable=False)

            try:
                turn_context = ChatTurnProcessingContext.model_validate(message.processing_context)
            except ValidationError:
                return _terminalize_preflight_failure(
                    context=context,
                    message=message,
                    public_error=CHAT_TURN_UNAVAILABLE_MESSAGE,
                    task_error="Invalid chat processing context",
                )

            snapshot = turn_context.session
            if snapshot.user_id != user_id or snapshot.effective_session_id != session_id:
                return TaskResult.fail("Chat turn ownership validation failed", retryable=False)
            if not _turn_lifecycle_is_valid(
                user=user,
                session=session,
                turn_context=turn_context,
            ):
                return _terminalize_preflight_failure(
                    context=context,
                    message=message,
                    public_error=CHAT_TURN_UNAVAILABLE_MESSAGE,
                    task_error="Chat turn lifecycle validation failed",
                )
            if task.retry_count > context.settings.queue.max_retries:
                return _terminalize_preflight_failure(
                    context=context,
                    message=message,
                    public_error=CHAT_TURN_FAILED_MESSAGE,
                    task_error="Chat turn stopped after repeated worker interruptions",
                )

            earlier_message_exists = (
                db.query(ChatMessage.id)
                .filter(
                    ChatMessage.session_id == session_id,
                    ChatMessage.id < message_id,
                    ChatMessage.status == MessageProcessingStatus.PROCESSING.value,
                )
                .first()
                is not None
            )
            if earlier_message_exists:
                return TaskResult.defer(retry_delay_seconds=ORDER_RETRY_DELAY_SECONDS)

        try:
            outcome = _run_chat_turn(
                task,
                turn_context,
                ensure_lease=context.renew_current_lease,
            )
        except ChatTurnLeaseCheckError:
            logger.warning(
                "Queued chat turn could not verify its terminal lease",
                extra={
                    "component": "chat_turn",
                    "operation": "verify_terminal_lease",
                    "task_id": task.id,
                    "user_id": user_id,
                    "session_id": session_id,
                    "message_id": message_id,
                },
            )
            return TaskResult.fail("Chat task lease could not be verified", retryable=True)
        except Exception:  # noqa: BLE001
            logger.exception(
                "Queued chat turn raised an exception",
                extra={
                    "component": "chat_turn",
                    "operation": "run",
                    "task_id": task.id,
                    "user_id": user_id,
                    "session_id": session_id,
                    "message_id": message_id,
                },
            )
            return TaskResult.fail(CHAT_TURN_FAILED_MESSAGE, retryable=True)

        if outcome == QueuedChatTurnOutcome.COMPLETED:
            return TaskResult.ok()
        if outcome == QueuedChatTurnOutcome.OWNERSHIP_LOST:
            return TaskResult.ok()
        return TaskResult.fail(CHAT_TURN_FAILED_MESSAGE, retryable=False)


def _turn_lifecycle_is_valid(
    *,
    user: User | None,
    session: ChatSession,
    turn_context: ChatTurnProcessingContext,
) -> bool:
    if user is None or not user.is_active or session.is_archived:
        return False
    snapshot = turn_context.session
    if turn_context.kind == "council":
        return bool(
            session.is_hidden_from_history
            and session.parent_session_id == snapshot.visible_session_id
            and snapshot.parent_session_id == snapshot.visible_session_id
        )
    return not session.is_hidden_from_history


def _mark_message_failed(message: ChatMessage, error: str) -> None:
    """Compare-and-set a processing row to a stable terminal failure."""
    if message.status != MessageProcessingStatus.PROCESSING.value:
        return
    message.status = MessageProcessingStatus.FAILED.value
    message.render_metadata = None
    message.error = error
    message.partial_text = None


def _terminalize_preflight_failure(
    *,
    context: TaskContext,
    message: ChatMessage,
    public_error: str,
    task_error: str,
) -> TaskResult:
    """Write a pre-provider failure only while the exact queue claim is current."""

    try:
        owns_lease = context.renew_current_lease()
    except Exception:  # noqa: BLE001
        logger.exception("Could not verify chat task ownership before preflight failure")
        return TaskResult.fail("Chat task lease could not be verified", retryable=True)
    if not owns_lease:
        return TaskResult.fail("Chat task lease ownership was lost", retryable=True)
    _mark_message_failed(message, public_error)
    return TaskResult.fail(task_error, retryable=False)


def _run_chat_turn(
    task: TaskEnvelope,
    context: ChatTurnProcessingContext,
    *,
    ensure_lease: Callable[[], bool],
) -> QueuedChatTurnOutcome:
    """Route one validated context to its queue-aware async executor."""
    snapshot = context.session
    if context.kind in {"article", "council"}:
        from app.services.chat_agent import process_message_async

        return asyncio.run(
            process_message_async(
                snapshot.effective_session_id,
                int(task.payload["message_id"]),
                context.user_prompt,
                source=context.source,
                task_id=task.id,
                turn_context=context,
                stream_generation=task.retry_count,
                ensure_lease=ensure_lease,
            )
        )
    if context.kind == "deep_research":
        from app.services.deep_research import process_deep_research_message

        return asyncio.run(
            process_deep_research_message(
                snapshot.effective_session_id,
                int(task.payload["message_id"]),
                context.user_prompt,
                source=context.source,
                task_id=task.id,
                turn_context=context,
                stream_generation=task.retry_count,
                ensure_lease=ensure_lease,
            )
        )

    from app.services.assistant_router import process_assistant_turn_async

    if context.screen_context is None:
        raise ValueError("Assistant turn is missing screen context")
    return asyncio.run(
        process_assistant_turn_async(
            snapshot.effective_session_id,
            int(task.payload["message_id"]),
            context.user_prompt,
            screen_context=context.screen_context,
            source=context.source,
            task_id=task.id,
            turn_context=context,
            stream_generation=task.retry_count,
            ensure_lease=ensure_lease,
        )
    )
