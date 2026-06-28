"""Shared helpers for chat turn lifecycles."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from typing import cast

from app.core.logging import get_logger
from app.core.observability import build_log_extra
from app.models.db import ChatSession
from app.services.llm_models import DEFAULT_MODEL, resolve_model_provider
from app.services.sandbox_runtime import PersonalLibrarySandboxSession
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


def personal_library_unavailable_message(error: str | None) -> str:
    """Render a consistent personal-library tool fallback."""
    if error:
        return f"Personal markdown library is unavailable: {error}"
    return "Personal markdown library is unavailable for this chat."


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


def close_sandbox_session(sandbox_session: PersonalLibrarySandboxSession | None) -> None:
    """Release a per-turn personal-library sandbox session."""
    if sandbox_session is None:
        return
    try:
        sandbox_session.close()
    except Exception:
        logger.debug("Ignoring sandbox close failure", exc_info=True)


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
