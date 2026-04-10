"""ElevenLabs-backed admin conversational streaming service."""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from importlib.util import find_spec
from threading import Event, Lock
from time import sleep
from typing import Any
from uuid import uuid4

from sqlalchemy import Text, cast, or_
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.models.schema import Content, ContentKnowledgeSave
from app.services.exa_client import exa_search
from app.utils.summary_utils import extract_summary_text

logger = get_logger(__name__)

PCM_16KHZ_MIME_TYPE = "audio/pcm;rate=16000;channels=1"
CONNECT_RETRY_ATTEMPTS = 25
CONNECT_RETRY_DELAY_SECONDS = 0.1
MAX_CONTEXT_CHARS = 6_000
MAX_KNOWLEDGE_HITS = 5
MAX_WEB_HITS = 5
MAX_TRANSCRIPT_EXCERPT_CHARS = 280
MAX_SNIPPET_CHARS = 320
MAX_BOOTSTRAP_TITLES = 100
DEFAULT_TRACE_PREVIEW_CHARS = 1200

EventSink = Callable[[dict[str, Any]], None]


@dataclass
class SessionTurn:
    """A single persisted in-memory chat turn."""

    role: str
    text: str
    timestamp: datetime


@dataclass
class SessionState:
    """In-memory conversational session state."""

    session_id: str
    user_id: int
    turns: list[SessionTurn] = field(default_factory=list)
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class AgentConversationRuntime:
    """Live ElevenLabs conversation runtime for one websocket session."""

    session_id: str
    user_id: int
    conversation: Any
    state_lock: Lock = field(default_factory=Lock)
    active_turn_id: str | None = None
    active_emit_event: EventSink | None = None
    response_event: Event = field(default_factory=Event)
    assistant_text: str = ""
    delta_fragments: list[str] = field(default_factory=list)
    audio_chunk_count: int = 0


@dataclass
class KnowledgeHit:
    """Single knowledge hit from user-saved knowledge content."""

    content_id: int
    title: str
    url: str
    source: str | None
    content_type: str
    summary: str | None
    transcript_excerpt: str | None


@dataclass
class WebHit:
    """Single web search hit."""

    title: str
    url: str
    snippet: str | None
    published_date: str | None


_SESSION_STORE: dict[str, SessionState] = {}
_SESSION_LOCK = Lock()


class _NullAudioInterface:
    """Minimal audio interface for text-led conversations.

    The ElevenLabs conversation object requires an audio interface even when the
    user sends only text. This implementation does not capture microphone input,
    but forwards assistant audio bytes through the callback.
    """

    def __init__(self, on_audio_chunk: Callable[[bytes], None]) -> None:
        self._on_audio_chunk = on_audio_chunk

    def start(self, _input_callback: Callable[[bytes], None]) -> None:
        return

    def stop(self) -> None:
        return

    def output(self, audio: bytes) -> None:
        if audio:
            self._on_audio_chunk(audio)

    def interrupt(self) -> None:
        return


# RORO helpers ----------------------------------------------------------------


def elevenlabs_sdk_available() -> bool:
    """Check if the ElevenLabs SDK is importable."""

    return find_spec("elevenlabs") is not None


def build_health_flags() -> dict[str, Any]:
    """Build readiness flags for the admin conversational health endpoint.

    Returns:
        JSON-serializable readiness payload.
    """

    settings = get_settings()
    sdk_available = elevenlabs_sdk_available()
    api_key_configured = bool(settings.elevenlabs_api_key)
    agent_id = settings.elevenlabs_agent_id.strip() if settings.elevenlabs_agent_id else ""
    readiness_reasons: list[str] = []
    if not api_key_configured:
        readiness_reasons.append("missing_elevenlabs_api_key")
    if not sdk_available:
        readiness_reasons.append("missing_elevenlabs_sdk")
    if not agent_id:
        readiness_reasons.append("missing_agent_id")

    return {
        "elevenlabs_api_configured": api_key_configured,
        "elevenlabs_package_available": sdk_available,
        "agent_id": agent_id or None,
        "agent_text_only": settings.elevenlabs_agent_text_only,
        "readiness_reasons": readiness_reasons,
        "ready": len(readiness_reasons) == 0,
    }


def create_or_get_session_state(session_id: str | None, user_id: int) -> SessionState:
    """Create or load a session state entry from the in-memory store.

    Args:
        session_id: Optional existing session ID.
        user_id: Selected user ID.

    Returns:
        SessionState object.

    Raises:
        ValueError: If session exists but belongs to a different user.
    """

    prune_session_store()
    normalized_id = (session_id or "").strip() or str(uuid4())

    with _SESSION_LOCK:
        state = _SESSION_STORE.get(normalized_id)
        now = datetime.now(UTC)
        if state is None:
            state = SessionState(session_id=normalized_id, user_id=user_id, updated_at=now)
            _SESSION_STORE[normalized_id] = state
            return state

        if state.user_id != user_id:
            raise ValueError("session_id does not belong to selected user_id")

        state.updated_at = now
        return state


def append_turn(session_id: str, role: str, text: str) -> None:
    """Append a user/assistant turn into session state.

    Args:
        session_id: Session identifier.
        role: Message role (user|assistant).
        text: Turn text.

    Raises:
        ValueError: If role is invalid or session does not exist.
    """

    settings = get_settings()
    max_messages = max(1, settings.admin_conversational_max_turns) * 2

    if role not in {"user", "assistant"}:
        raise ValueError("role must be 'user' or 'assistant'")

    with _SESSION_LOCK:
        state = _SESSION_STORE.get(session_id)
        if state is None:
            raise ValueError("session not found")

        state.turns.append(SessionTurn(role=role, text=text, timestamp=datetime.now(UTC)))
        if len(state.turns) > max_messages:
            state.turns = state.turns[-max_messages:]
        state.updated_at = datetime.now(UTC)


def get_turn_history(session_id: str) -> list[SessionTurn]:
    """Return an immutable snapshot of turn history."""

    with _SESSION_LOCK:
        state = _SESSION_STORE.get(session_id)
        if state is None:
            return []
        return list(state.turns)


def prune_session_store() -> None:
    """Prune expired sessions from in-memory state."""

    settings = get_settings()
    ttl_minutes = max(1, settings.admin_conversational_session_ttl_minutes)
    cutoff = datetime.now(UTC) - timedelta(minutes=ttl_minutes)

    with _SESSION_LOCK:
        expired_ids = [sid for sid, state in _SESSION_STORE.items() if state.updated_at < cutoff]
        for sid in expired_ids:
            _SESSION_STORE.pop(sid, None)


def clear_session_store() -> None:
    """Clear session state (test helper)."""

    with _SESSION_LOCK:
        _SESSION_STORE.clear()


def search_knowledge(
    db: Session,
    user_id: int,
    query: str,
    limit: int = MAX_KNOWLEDGE_HITS,
) -> list[KnowledgeHit]:
    """Search user-saved knowledge content with deterministic SQL matching.

    Args:
        db: Active SQLAlchemy session.
        user_id: Target user identifier.
        query: Free-text user query.
        limit: Maximum number of hits to return.

    Returns:
        Ordered knowledge hits from saved knowledge content only.
    """

    normalized_query = query.strip()
    if not normalized_query:
        return []

    max_hits = max(1, min(limit, 20))
    pattern = f"%{normalized_query}%"

    base_query = (
        db.query(Content)
        .join(ContentKnowledgeSave, ContentKnowledgeSave.content_id == Content.id)
        .filter(ContentKnowledgeSave.user_id == user_id)
    )
    rows = (
        base_query
        .filter(
            or_(
                Content.title.ilike(pattern),
                Content.source.ilike(pattern),
                Content.url.ilike(pattern),
                cast(Content.content_metadata, Text).ilike(pattern),
            )
        )
        .order_by(ContentKnowledgeSave.saved_at.desc())
        .limit(max_hits)
        .all()
    )
    if not rows:
        rows = (
            base_query
            .order_by(ContentKnowledgeSave.saved_at.desc())
            .limit(max_hits)
            .all()
        )

    hits: list[KnowledgeHit] = []
    for content in rows:
        metadata = content.content_metadata if isinstance(content.content_metadata, dict) else {}
        summary = _extract_summary(metadata)
        transcript_excerpt = _extract_transcript_excerpt(metadata)
        hits.append(
            KnowledgeHit(
                content_id=int(content.id),
                title=str(content.title or "Untitled"),
                url=str(content.url or ""),
                source=str(content.source) if content.source else None,
                content_type=str(content.content_type or "unknown"),
                summary=summary,
                transcript_excerpt=transcript_excerpt,
            )
        )

    return hits


def search_web(query: str, limit: int = MAX_WEB_HITS) -> list[WebHit]:
    """Search the web via Exa and return normalized hits.

    Args:
        query: User query text.
        limit: Maximum number of web hits.

    Returns:
        Web hits. Empty list when Exa is unavailable or no results found.
    """

    normalized_query = query.strip()
    if not normalized_query:
        return []

    max_hits = max(1, min(limit, 20))
    results = exa_search(normalized_query, num_results=max_hits)
    return [
        WebHit(
            title=result.title,
            url=result.url,
            snippet=_clean_snippet(result.snippet),
            published_date=result.published_date,
        )
        for result in results
    ]


def build_available_knowledge_context(
    db: Session,
    user_id: int,
    limit: int = MAX_BOOTSTRAP_TITLES,
) -> str:
    """Build a startup context listing known saved titles for the user."""

    max_titles = max(1, min(limit, MAX_BOOTSTRAP_TITLES))
    rows = (
        db.query(Content.title, Content.source, Content.content_type, Content.url)
        .join(ContentKnowledgeSave, ContentKnowledgeSave.content_id == Content.id)
        .filter(ContentKnowledgeSave.user_id == user_id)
        .order_by(ContentKnowledgeSave.saved_at.desc())
        .limit(max_titles)
        .all()
    )

    lines = [
        "Known user knowledge catalog (most recent first).",
        "Use this list to ground answers about what the user has saved to knowledge.",
    ]
    if not rows:
        lines.append("No saved knowledge found for this user.")
    else:
        for idx, row in enumerate(rows, start=1):
            title = str(row.title or "Untitled")
            source = str(row.source or "unknown")
            content_type = str(row.content_type or "unknown")
            url = str(row.url or "")
            lines.append(f"- [{idx}] {title} | source={source} | type={content_type} | url={url}")

    context = "\n".join(lines).strip()
    if len(context) > MAX_CONTEXT_CHARS:
        context = context[-MAX_CONTEXT_CHARS:]
    return context


# ElevenLabs runtime lifecycle --------------------------------------------------


def start_agent_session(
    session_id: str,
    user_id: int,
    bootstrap_context: str | None = None,
) -> AgentConversationRuntime:
    """Start a persistent ElevenLabs conversation runtime for one websocket session.

    Args:
        session_id: In-memory session ID.
        user_id: Selected user ID.
        bootstrap_context: Optional catalog context sent once on session start.

    Returns:
        Running `AgentConversationRuntime`.

    Raises:
        RuntimeError: If ElevenLabs configuration is invalid or session startup fails.
    """

    settings = get_settings()
    _ensure_elevenlabs_ready()

    elevenlabs_client_cls, conversation_cls, initiation_data_cls = _import_sdk_symbols()
    client = elevenlabs_client_cls(api_key=settings.elevenlabs_api_key)

    runtime = AgentConversationRuntime(session_id=session_id, user_id=user_id, conversation=None)

    config_kwargs: dict[str, Any] = {
        "dynamic_variables": {
            "user_id": str(user_id),
            "session_id": session_id,
        }
    }
    if settings.elevenlabs_agent_text_only:
        config_kwargs["extra_body"] = {"text_only": True}

    initiation_data = initiation_data_cls(**config_kwargs)

    audio_interface = _NullAudioInterface(
        on_audio_chunk=lambda audio: _handle_audio_chunk(runtime, audio)
    )
    conversation = conversation_cls(
        client=client,
        agent_id=settings.elevenlabs_agent_id,
        user_id=str(user_id),
        requires_auth=True,
        audio_interface=audio_interface,
        config=initiation_data,
        callback_agent_response=lambda text: _handle_agent_response(runtime, text),
        callback_agent_chat_response_part=lambda text, part_type: _handle_agent_chat_response_part(
            runtime,
            text,
            part_type,
        ),
    )
    runtime.conversation = conversation

    history = get_turn_history(session_id)
    context_text = _build_context_text(history)

    try:
        conversation.start_session()
        if context_text:
            _send_with_retry(conversation.send_contextual_update, context_text)
        if bootstrap_context:
            _send_with_retry(conversation.send_contextual_update, bootstrap_context)
        _log_trace(
            operation="agent_session_start",
            session_id=session_id,
            user_id=user_id,
            context_data={
                "history_context_chars": len(context_text),
                "bootstrap_context_chars": len(bootstrap_context or ""),
                "bootstrap_context_preview": _truncate_for_trace(bootstrap_context or ""),
            },
        )
    except Exception as exc:  # noqa: BLE001
        with contextlib.suppress(Exception):
            conversation.end_session()
        with contextlib.suppress(Exception):
            conversation.wait_for_session_end()
        logger.exception(
            "Admin conversational session startup failed",
            extra={
                "component": "admin_conversational",
                "operation": "agent_session_start",
                "item_id": session_id,
                "context_data": {
                    "user_id": user_id,
                    "error": str(exc),
                },
            },
        )
        raise

    return runtime


def close_agent_session(runtime: AgentConversationRuntime) -> None:
    """Close and clean up a persistent ElevenLabs runtime."""

    with contextlib.suppress(Exception):
        runtime.conversation.end_session()
    with contextlib.suppress(Exception):
        runtime.conversation.wait_for_session_end()


def stream_agent_turn(
    runtime: AgentConversationRuntime,
    user_text: str,
    turn_id: str,
    emit_event: EventSink,
    knowledge_hits: list[KnowledgeHit] | None = None,
    web_hits: list[WebHit] | None = None,
) -> None:
    """Run one conversational turn on an existing runtime and emit streaming events.

    Args:
        runtime: Open ElevenLabs runtime.
        user_text: User input.
        turn_id: Client-provided turn ID.
        emit_event: Event sink callback.
        knowledge_hits: Knowledge-library hits for this user turn.
        web_hits: Exa web hits for this user turn.

    Raises:
        RuntimeError: If the turn fails.
        TimeoutError: If model response exceeds configured timeout.
    """

    settings = get_settings()

    append_turn(runtime.session_id, "user", user_text)
    turn_context = _build_turn_context(user_text, knowledge_hits or [], web_hits or [])
    local_response = _build_local_knowledge_response(user_text, knowledge_hits or [])
    if local_response:
        append_turn(runtime.session_id, "assistant", local_response)
        emit_event({"type": "assistant_final", "turn_id": turn_id, "text": local_response})
        _log_trace(
            operation="turn_response_local",
            session_id=runtime.session_id,
            user_id=runtime.user_id,
            context_data={
                "turn_id": turn_id,
                "assistant_text": _truncate_for_trace(local_response),
                "knowledge_hit_count": len(knowledge_hits or []),
            },
        )
        emit_event({"type": "audio_end", "turn_id": turn_id, "total_chunks": 0})
        return

    effective_user_message = _build_enriched_user_message(
        user_text=user_text,
        knowledge_hits=knowledge_hits or [],
        web_hits=web_hits or [],
    )
    _log_trace(
        operation="turn_context",
        session_id=runtime.session_id,
        user_id=runtime.user_id,
        context_data={
            "turn_id": turn_id,
            "user_text": _truncate_for_trace(user_text),
            "knowledge_hit_count": len(knowledge_hits or []),
            "knowledge_titles": [hit.title for hit in (knowledge_hits or [])],
            "web_hit_count": len(web_hits or []),
            "web_titles": [hit.title for hit in (web_hits or [])],
            "turn_context_preview": _truncate_for_trace(turn_context),
            "effective_user_message_preview": _truncate_for_trace(effective_user_message),
        },
    )

    _begin_turn(runtime, turn_id, emit_event)
    try:
        if turn_context:
            _send_with_retry(runtime.conversation.send_contextual_update, turn_context)
        _send_with_retry(runtime.conversation.send_user_message, effective_user_message)

        timeout_seconds = max(1, settings.elevenlabs_agent_turn_timeout_seconds)
        if not runtime.response_event.wait(timeout=timeout_seconds):
            raise TimeoutError("Timed out waiting for assistant response")

        assistant_text, had_final_text, audio_chunk_count = _collect_turn_result(runtime)
        if assistant_text and not had_final_text:
            emit_event({"type": "assistant_final", "turn_id": turn_id, "text": assistant_text})
        if not assistant_text:
            raise RuntimeError("Assistant returned no content")

        append_turn(runtime.session_id, "assistant", assistant_text)
        _log_trace(
            operation="turn_response",
            session_id=runtime.session_id,
            user_id=runtime.user_id,
            context_data={
                "turn_id": turn_id,
                "assistant_text": _truncate_for_trace(assistant_text),
                "audio_chunk_count": audio_chunk_count,
            },
        )
        emit_event({"type": "audio_end", "turn_id": turn_id, "total_chunks": audio_chunk_count})
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Admin conversational turn failed",
            extra={
                "component": "admin_conversational",
                "operation": "agent_turn",
                "item_id": runtime.session_id,
                "context_data": {
                    "user_id": runtime.user_id,
                    "turn_id": turn_id,
                    "error": str(exc),
                },
            },
        )
        raise
    finally:
        _end_turn(runtime)


# Private helpers ---------------------------------------------------------------


def _truncate_for_trace(text: str | None) -> str:
    """Truncate trace payload strings to a configured safe size."""

    if not text:
        return ""
    settings = get_settings()
    max_chars = max(200, int(settings.admin_conversational_trace_max_chars or 0))
    max_chars = min(max_chars, 10_000)
    return text[:max_chars].strip()


def _log_trace(
    operation: str,
    session_id: str,
    user_id: int,
    context_data: dict[str, Any],
) -> None:
    """Emit structured trace logs for admin conversational turns."""

    settings = get_settings()
    if not settings.admin_conversational_trace_logging:
        return

    logger.info(
        "Admin conversational trace",
        extra={
            "component": "admin_conversational",
            "operation": operation,
            "item_id": session_id,
            "context_data": {"user_id": user_id, **context_data},
        },
    )


def serialize_knowledge_hits(hits: list[KnowledgeHit]) -> list[dict[str, Any]]:
    """Serialize knowledge hits for websocket payloads."""

    return [
        {
            "content_id": hit.content_id,
            "title": hit.title,
            "url": hit.url,
            "source": hit.source,
            "content_type": hit.content_type,
            "summary": hit.summary,
            "transcript_excerpt": hit.transcript_excerpt,
        }
        for hit in hits
    ]


def serialize_web_hits(hits: list[WebHit]) -> list[dict[str, Any]]:
    """Serialize web hits for websocket payloads."""

    return [
        {
            "title": hit.title,
            "url": hit.url,
            "snippet": hit.snippet,
            "published_date": hit.published_date,
        }
        for hit in hits
    ]


def _extract_summary(metadata: dict[str, Any]) -> str | None:
    """Extract a concise summary text from content metadata."""

    summary_payload = metadata.get("summary")
    summary = extract_summary_text(summary_payload)
    if not summary:
        return None
    trimmed = str(summary).strip()
    return trimmed or None


def _extract_transcript_excerpt(metadata: dict[str, Any]) -> str | None:
    """Extract a bounded transcript excerpt when available."""

    transcript = metadata.get("transcript") or metadata.get("excerpt")
    if not isinstance(transcript, str):
        return None
    excerpt = transcript.strip()
    if not excerpt:
        return None
    if len(excerpt) > MAX_TRANSCRIPT_EXCERPT_CHARS:
        return excerpt[:MAX_TRANSCRIPT_EXCERPT_CHARS].rstrip() + "..."
    return excerpt


def _clean_snippet(snippet: str | None) -> str | None:
    """Normalize and truncate web snippets."""

    if snippet is None:
        return None
    trimmed = str(snippet).strip()
    if not trimmed:
        return None
    if len(trimmed) > MAX_SNIPPET_CHARS:
        return trimmed[:MAX_SNIPPET_CHARS].rstrip() + "..."
    return trimmed


def _build_turn_context(
    user_text: str,
    knowledge_hits: list[KnowledgeHit],
    web_hits: list[WebHit],
) -> str:
    """Build per-turn tool context injected into ElevenLabs conversation."""

    lines = [
        "Tool results for the latest user message.",
        f"Latest user message: {user_text.strip()}",
        "Priority rules:",
        "- SearchKnowledge entries are trusted records from THIS USER'S saved knowledge.",
        "- If SearchKnowledge has entries, you DO have access to user-specific saved articles.",
        (
            "- For questions about saved history/last read item, prioritize "
            "SearchKnowledge over SearchWeb."
        ),
        "- Do not claim you lack access to saved knowledge when SearchKnowledge contains hits.",
        "",
        "SearchKnowledge results:",
    ]

    if not knowledge_hits:
        lines.append("- none")
    else:
        for idx, hit in enumerate(knowledge_hits, start=1):
            source = hit.source or "unknown"
            lines.append(f"- [{idx}] {hit.title} | source={source} | type={hit.content_type}")
            lines.append(f"  url={hit.url}")
            if hit.summary:
                lines.append(f"  summary={hit.summary}")
            if hit.transcript_excerpt:
                lines.append(f"  transcript_excerpt={hit.transcript_excerpt}")
        lines.append("Interpretation hint: [K1] is the most recent saved item in this result set.")

    lines.append("")
    lines.append("SearchWeb results:")
    if not web_hits:
        lines.append("- none")
    else:
        for idx, hit in enumerate(web_hits, start=1):
            lines.append(f"- [{idx}] {hit.title}")
            lines.append(f"  url={hit.url}")
            if hit.published_date:
                lines.append(f"  published_date={hit.published_date}")
            if hit.snippet:
                lines.append(f"  snippet={hit.snippet}")

    context = "\n".join(lines).strip()
    if len(context) > MAX_CONTEXT_CHARS:
        context = context[-MAX_CONTEXT_CHARS:]
    return context


def _build_enriched_user_message(
    user_text: str,
    knowledge_hits: list[KnowledgeHit],
    web_hits: list[WebHit],
) -> str:
    """Build a robust user message that includes tool context inline."""

    if not knowledge_hits and not web_hits:
        return user_text

    context_block = _build_turn_context(
        user_text=user_text,
        knowledge_hits=knowledge_hits,
        web_hits=web_hits,
    )
    lines = [
        "Follow these instructions before answering:",
        "- Treat SearchKnowledge entries as available user saved knowledge.",
        "- If the question references saved knowledge/history, answer from SearchKnowledge first.",
        "- Do not claim you lack access when SearchKnowledge entries are present.",
        "",
        context_block,
        "",
        f"User question: {user_text}",
    ]
    merged = "\n".join(lines).strip()
    if len(merged) > MAX_CONTEXT_CHARS:
        merged = merged[-MAX_CONTEXT_CHARS:]
    return merged


def _build_local_knowledge_response(
    user_text: str,
    knowledge_hits: list[KnowledgeHit],
) -> str | None:
    """Build deterministic answers for saved-knowledge/history intents using knowledge hits."""

    if not knowledge_hits:
        return None
    if not _is_saved_knowledge_intent(user_text):
        return None

    query = user_text.lower()
    if "last" in query or "most recent" in query:
        hit = knowledge_hits[0]
        summary = f" Summary: {hit.summary}" if hit.summary else ""
        return (
            "Your most recent saved knowledge item in this result set is "
            f"\"{hit.title}\" ({hit.url})."
            f"{summary}"
        )

    lines = ["Here are items I can access from your saved knowledge:"]
    for idx, hit in enumerate(knowledge_hits, start=1):
        line = f"{idx}. {hit.title} ({hit.url})"
        if hit.summary:
            line += f" - {hit.summary}"
        lines.append(line)
    return "\n".join(lines)


def _is_saved_knowledge_intent(user_text: str) -> bool:
    """Return true when user asks about personal saved-knowledge history."""

    text = user_text.lower().strip()
    if not text:
        return False
    keywords = [
        "favorite",
        "favourite",
        "saved",
        "bookmarked",
        "my article",
        "my podcast",
        "what did i read",
        "last article",
        "most recent article",
        "what have i read",
        "what i read",
    ]
    return any(keyword in text for keyword in keywords)


def _ensure_elevenlabs_ready() -> None:
    """Validate ElevenLabs readiness and raise a precise runtime error if unavailable."""

    health = build_health_flags()
    if health["ready"]:
        return

    reasons = ", ".join(health.get("readiness_reasons", []))
    raise RuntimeError(
        "ElevenLabs is not ready"
        + (f" ({reasons})" if reasons else "")
        + ". Configure ELEVENLABS_API_KEY (or ELEVENLABS) and install the elevenlabs package."
    )


def _begin_turn(runtime: AgentConversationRuntime, turn_id: str, emit_event: EventSink) -> None:
    """Initialize runtime state for a new user turn."""

    with runtime.state_lock:
        runtime.active_turn_id = turn_id
        runtime.active_emit_event = emit_event
        runtime.assistant_text = ""
        runtime.delta_fragments.clear()
        runtime.audio_chunk_count = 0
        runtime.response_event.clear()


def _end_turn(runtime: AgentConversationRuntime) -> None:
    """Clear active turn state so late callbacks are ignored."""

    with runtime.state_lock:
        runtime.active_turn_id = None
        runtime.active_emit_event = None


def _collect_turn_result(runtime: AgentConversationRuntime) -> tuple[str, bool, int]:
    """Collect assistant output and audio count for the active turn."""

    with runtime.state_lock:
        response_text = runtime.assistant_text.strip()
        delta_text = "".join(runtime.delta_fragments).strip()
        audio_chunk_count = runtime.audio_chunk_count

    assistant_text = response_text or delta_text
    return assistant_text, bool(response_text), audio_chunk_count


def _resolve_active_turn(runtime: AgentConversationRuntime) -> tuple[str, EventSink] | None:
    """Return active turn identifiers for callback handlers."""

    with runtime.state_lock:
        if runtime.active_turn_id is None or runtime.active_emit_event is None:
            return None
        return runtime.active_turn_id, runtime.active_emit_event


def _handle_agent_response(runtime: AgentConversationRuntime, text: str) -> None:
    """Handle final assistant response callback from ElevenLabs."""

    active_turn = _resolve_active_turn(runtime)
    if active_turn is None:
        return

    turn_id, emit_event = active_turn
    normalized_text = text.strip()
    with runtime.state_lock:
        runtime.assistant_text = normalized_text

    if normalized_text:
        emit_event({"type": "assistant_final", "turn_id": turn_id, "text": normalized_text})
    runtime.response_event.set()


def _handle_agent_chat_response_part(
    runtime: AgentConversationRuntime,
    text: str,
    part_type: Any,
) -> None:
    """Handle incremental assistant text deltas."""

    active_turn = _resolve_active_turn(runtime)
    if active_turn is None:
        return

    turn_id, emit_event = active_turn
    part_value = str(getattr(part_type, "value", part_type)).lower()

    if part_value in {"delta", "start"} and text:
        with runtime.state_lock:
            runtime.delta_fragments.append(text)
        emit_event({"type": "assistant_delta", "turn_id": turn_id, "text_delta": text})

    if part_value == "stop":
        runtime.response_event.set()


def _handle_audio_chunk(runtime: AgentConversationRuntime, audio: bytes) -> None:
    """Forward assistant audio chunks as raw bytes events."""

    if not audio:
        return

    active_turn = _resolve_active_turn(runtime)
    if active_turn is None:
        return

    turn_id, emit_event = active_turn
    with runtime.state_lock:
        seq = runtime.audio_chunk_count
        runtime.audio_chunk_count += 1

    emit_event(
        {
            "type": "audio_chunk_raw",
            "turn_id": turn_id,
            "seq": seq,
            "mime_type": PCM_16KHZ_MIME_TYPE,
            "audio_bytes": audio,
        }
    )


def _build_context_text(turns: list[SessionTurn]) -> str:
    """Build bounded contextual update text from recent turns."""

    if not turns:
        return ""

    settings = get_settings()
    max_messages = max(1, settings.admin_conversational_max_turns) * 2
    recent = turns[-max_messages:]

    lines = [
        "Conversation continuation instructions:",
        "- You are continuing an existing chat.",
        "- Do not repeat opening greetings like 'Hello! How can I help you today?'.",
        "- Answer the latest user message directly and concretely.",
        "",
        "Recent conversation history:",
    ]
    for turn in recent:
        role_label = "User" if turn.role == "user" else "Assistant"
        lines.append(f"{role_label}: {turn.text.strip()}")

    context = "\n".join(lines).strip()
    if len(context) > MAX_CONTEXT_CHARS:
        context = context[-MAX_CONTEXT_CHARS:]
    return context


def _send_with_retry(send_callable: Callable[[str], None], text: str) -> None:
    """Retry conversational send operations until websocket transport is ready."""

    last_error: Exception | None = None
    for _ in range(CONNECT_RETRY_ATTEMPTS):
        try:
            send_callable(text)
            return
        except RuntimeError as exc:
            lowered = str(exc).lower()
            if "not connected" not in lowered and "session not started" not in lowered:
                raise
            last_error = exc
            sleep(CONNECT_RETRY_DELAY_SECONDS)

    if last_error is not None:
        raise RuntimeError("Conversation transport did not become ready") from last_error
    raise RuntimeError("Conversation transport did not become ready")


def _import_sdk_symbols() -> tuple[Any, Any, Any]:
    """Import ElevenLabs SDK symbols lazily.

    Returns:
        Tuple of (ElevenLabs client class, Conversation class,
        ConversationInitiationData class).

    Raises:
        RuntimeError: If SDK import fails.
    """

    try:
        from elevenlabs.client import ElevenLabs
        from elevenlabs.conversational_ai.conversation import (
            Conversation,
            ConversationInitiationData,
        )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("Failed to import elevenlabs SDK") from exc

    return ElevenLabs, Conversation, ConversationInitiationData
