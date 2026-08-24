"""Shared helpers for chat turn lifecycles."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from threading import Lock
from typing import cast

from pydantic_ai.messages import ModelResponse, ToolCallPart
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.observability import build_log_extra
from app.models.db import ChatSession
from app.models.internal.chat_turn import ChatTurnSessionSnapshot
from app.services.lazy_agent_vm import LazyAgentVmRuntime
from app.services.llm_models import DEFAULT_MODEL, resolve_model_provider
from app.services.llm_task_turn_tracker import LlmTaskTurnSpec, LlmTaskTurnTracker
from app.services.vendor_costs import extract_usage_from_result, record_vendor_usage_out_of_band

logger = get_logger(__name__)

_AGENT_CACHE_LOCK = Lock()
_AGENT_CACHE: dict[tuple[str, str, str], object] = {}


@dataclass(frozen=True)
class ChatUsageSnapshot:
    """Detached-safe chat session fields used to record usage after a turn."""

    user_id: int
    model: str
    content_id: int | None
    session_type: str | None

    @classmethod
    def from_session(cls, session: ChatSession) -> ChatUsageSnapshot:
        return cls(
            user_id=require_session_user_id(session),
            model=resolve_session_model(session),
            content_id=session.content_id,
            session_type=session.session_type,
        )


@dataclass(frozen=True)
class DetachedChatTurnLifecycle:
    """Static ledger and usage settings for one background chat-turn family."""

    task_spec: LlmTaskTurnSpec
    running_note: str
    completed_note: str
    failed_note: str
    usage_context: str


@dataclass(frozen=True)
class DetachedChatTurn:
    """Immutable fields safe to use after the preparation DB session closes."""

    session_id: int
    message_id: int | None
    user_id: int
    model: str
    provider: str
    content_id: int | None
    news_item_id: int | None
    session_type: str | None
    source: str
    task_id: int | None
    stream_generation: int = 0
    llm_task_id: int | None = None

    @property
    def usage_snapshot(self) -> ChatUsageSnapshot:
        return ChatUsageSnapshot(
            user_id=self.user_id,
            model=self.model,
            content_id=self.content_id,
            session_type=self.session_type,
        )


def snapshot_detached_chat_turn(
    session: ChatSession,
    *,
    message_id: int | None,
    source: str,
    task_id: int | None,
    stream_generation: int = 0,
) -> DetachedChatTurn:
    """Snapshot session fields that remain safe after its DB session closes."""
    usage_snapshot = ChatUsageSnapshot.from_session(session)
    return DetachedChatTurn(
        session_id=require_session_id(session),
        message_id=message_id,
        user_id=usage_snapshot.user_id,
        model=usage_snapshot.model,
        provider=resolve_model_provider(usage_snapshot.model),
        content_id=usage_snapshot.content_id,
        news_item_id=session.news_item_id,
        session_type=usage_snapshot.session_type,
        source=source,
        task_id=task_id,
        stream_generation=stream_generation,
    )


def snapshot_detached_chat_turn_from_snapshot(
    snapshot: ChatTurnSessionSnapshot,
    *,
    message_id: int | None,
    source: str,
    task_id: int | None,
    stream_generation: int = 0,
) -> DetachedChatTurn:
    """Build detached runtime state directly from an acceptance-time snapshot."""
    return DetachedChatTurn(
        session_id=snapshot.effective_session_id,
        message_id=message_id,
        user_id=snapshot.user_id,
        model=snapshot.model,
        provider=snapshot.provider,
        content_id=snapshot.content_id,
        news_item_id=snapshot.news_item_id,
        session_type=snapshot.session_type,
        source=source,
        task_id=task_id,
        stream_generation=stream_generation,
    )


class QueuedChatTurnOutcome(StrEnum):
    """Terminal result returned to the durable queue adapter."""

    COMPLETED = "completed"
    FAILED = "failed"
    OWNERSHIP_LOST = "ownership_lost"


class ChatTurnOwnershipLost(RuntimeError):
    """The current attempt may no longer mutate canonical chat state."""


class ChatTurnLeaseCheckError(RuntimeError):
    """The exact lease could not be verified, so the queue must retry safely."""


def start_detached_chat_turn(
    db: Session,
    *,
    turn: DetachedChatTurn,
    lifecycle: DetachedChatTurnLifecycle,
    input_json: dict[str, object],
) -> tuple[DetachedChatTurn, LlmTaskTurnTracker]:
    """Create the durable ledger row before provider work begins."""
    tracker = LlmTaskTurnTracker.create(
        db,
        user_id=turn.user_id,
        spec=lifecycle.task_spec,
        input_json=input_json,
    )
    if tracker.task_id is None:
        raise RuntimeError("Chat turn ledger row was not initialized")
    return replace(turn, llm_task_id=tracker.task_id), tracker


def mark_detached_chat_turn_running(
    db: Session,
    *,
    turn: DetachedChatTurn,
    tracker: LlmTaskTurnTracker,
    lifecycle: DetachedChatTurnLifecycle,
) -> None:
    """Record that preparation finished and external work is starting."""
    tracker.running(
        db,
        note=lifecycle.running_note,
        model_provider=turn.provider,
        model_name=turn.model,
    )


def complete_detached_chat_turn(
    db: Session,
    *,
    session: ChatSession,
    turn: DetachedChatTurn,
    tracker: LlmTaskTurnTracker,
    lifecycle: DetachedChatTurnLifecycle,
    output_json: dict[str, object],
) -> None:
    """Atomically persist session activity and the completed ledger state."""
    now = datetime.now(UTC)
    session.last_message_at = now
    session.updated_at = now
    tracker.completed(
        db,
        note=lifecycle.completed_note,
        output_json=output_json,
        model_provider=turn.provider,
        model_name=turn.model,
    )


def persist_detached_turn_failure(
    *,
    session_factory: Callable[[], Session],
    tracker: LlmTaskTurnTracker,
    lifecycle: DetachedChatTurnLifecycle,
    message_id: int | None,
    error: Exception,
    mark_message_failed: Callable[[Session, int, str], object] | None,
) -> None:
    """Best-effort persistence for an optional message and durable ledger row."""

    try:
        with session_factory() as fail_db:
            if message_id is not None and mark_message_failed is not None:
                mark_message_failed(fail_db, message_id, str(error))
            if tracker.task_id is None:
                fail_db.commit()
            else:
                tracker.failed(
                    fail_db,
                    note=lifecycle.failed_note,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
    except Exception:  # noqa: BLE001
        logger.exception(
            "Failed to persist detached chat turn failure",
            extra={"message_id": message_id, "llm_task_id": tracker.task_id},
        )


def require_session_id(session: ChatSession) -> int:
    """Return a persisted chat session id or raise."""
    session_id = session.id
    if session_id is None:
        raise ValueError("Chat session is missing an id")
    return int(session_id)


def require_session_user_id(session: ChatSession) -> int:
    """Return the owning user id for a chat session or raise."""
    user_id = session.user_id
    if user_id is None:
        raise ValueError("Chat session is missing a user_id")
    return int(user_id)


def resolve_session_model(session: ChatSession) -> str:
    """Return the effective model spec for a chat session."""
    model_spec = session.llm_model
    if isinstance(model_spec, str) and model_spec.strip():
        return model_spec
    return DEFAULT_MODEL


def extract_tool_names(result: object) -> list[str]:
    """Return tool names used by this turn from canonical new message parts."""
    new_messages = getattr(result, "new_messages", None)
    if not callable(new_messages):
        return []
    return [
        part.tool_name
        for message in new_messages()
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, ToolCallPart) and part.tool_name
    ]


def build_agent_cache_key(model_spec: str, api_key_override: str | None) -> tuple[str, str]:
    """Build a stable agent-cache key without retaining raw secrets."""
    if not api_key_override:
        return model_spec, ""
    return model_spec, hashlib.sha256(api_key_override.encode("utf-8")).hexdigest()


def get_or_create_cached_agent[AgentT](
    namespace: str,
    model_spec: str,
    api_key_override: str | None,
    factory: Callable[[], AgentT],
) -> AgentT:
    """Return a cached agent for a chat runtime namespace."""
    model_key, credential_key = build_agent_cache_key(model_spec, api_key_override)
    cache_key = (namespace, model_key, credential_key)
    with _AGENT_CACHE_LOCK:
        existing = _AGENT_CACHE.get(cache_key)
        if existing is not None:
            return cast(AgentT, existing)
        agent = factory()
        _AGENT_CACHE[cache_key] = agent
        return agent


def clear_agent_cache_for_tests() -> None:
    """Clear shared chat agent cache for tests."""
    with _AGENT_CACHE_LOCK:
        _AGENT_CACHE.clear()


def close_agent_vm_runtime(vm_runtime: LazyAgentVmRuntime | None) -> None:
    """Release a lazy per-turn VM handle without destroying persistent compute."""
    if vm_runtime is None:
        return
    try:
        vm_runtime.close()
    except Exception:
        logger.debug("Ignoring agent VM runtime close failure", exc_info=True)


def require_current_chat_lease(ensure_lease: Callable[[], bool]) -> None:
    """Turn an exact lease callback into explicit ownership semantics."""

    try:
        owns_lease = ensure_lease()
    except Exception as exc:  # noqa: BLE001
        raise ChatTurnLeaseCheckError("Unable to verify chat task ownership") from exc
    if not owns_lease:
        raise ChatTurnOwnershipLost("Chat task lease ownership was lost")


def log_chat_usage(
    result: object,
    usage_snapshot: ChatUsageSnapshot,
    session_id: int,
    message_id: int | None,
    context: str,
) -> None:
    """Persist and log token usage for a chat request when available."""
    usage_details = extract_usage_from_result(result)
    if usage_details is None:
        return

    user_id = usage_snapshot.user_id
    model_spec = usage_snapshot.model
    provider = resolve_model_provider(model_spec)

    try:
        usage = record_vendor_usage_out_of_band(
            provider=provider,
            model=model_spec,
            feature="chat",
            operation=f"chat.{context}",
            source=context,
            usage=usage_details,
            session_id=session_id,
            message_id=message_id,
            user_id=user_id,
            content_id=usage_snapshot.content_id,
            metadata={"session_type": usage_snapshot.session_type},
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
            user_id=user_id,
            content_id=usage_snapshot.content_id,
            source=context,
            context_data={
                "model": model_spec,
                "provider": provider,
                "usage_recorded": usage is not None,
            },
        ),
    )
