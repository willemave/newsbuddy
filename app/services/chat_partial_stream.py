"""Durable advisory streaming for in-flight chat responses."""

from __future__ import annotations

from collections.abc import AsyncIterable, Awaitable, Callable
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.messages import (
    FinalResultEvent,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
)
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.contracts import MessageProcessingStatus
from app.models.db import ChatMessage
from app.services.chat_turn_runtime import ChatTurnOwnershipLost

logger = get_logger(__name__)


def initialize_chat_stream_attempt(
    db: Session,
    *,
    message_id: int,
    stream_generation: int,
) -> None:
    """Install a monotonic attempt generation before external preparation."""
    message = (
        db.query(ChatMessage)
        .filter(ChatMessage.id == message_id)
        .populate_existing()
        .with_for_update()
        .first()
    )
    if message is None:
        raise ValueError(f"Message {message_id} not found")
    if message.status != MessageProcessingStatus.PROCESSING.value:
        raise ChatTurnOwnershipLost("Chat message is already terminal")

    current_generation = message.stream_generation
    if current_generation is not None and current_generation > stream_generation:
        raise ChatTurnOwnershipLost("A newer chat attempt already owns the message")
    if current_generation is None or current_generation < stream_generation:
        message.partial_text = None
        message.stream_generation = stream_generation
        message.stream_revision = 0
        message.stream_updated_at = None
    db.flush()


class DurableChatPartialWriter:
    """Persist cumulative user-visible text for one attempt generation."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        message_id: int,
        stream_generation: int,
        minimum_interval_seconds: float = 0.25,
    ) -> None:
        self._session_factory = session_factory
        self._message_id = message_id
        self._stream_generation = stream_generation
        self._minimum_interval_seconds = minimum_interval_seconds
        self._started_at = perf_counter()
        self._last_write_at: float | None = None
        self._last_text = ""
        self._disabled = False
        self.write_count = 0
        self.first_partial_ms: float | None = None

    def is_ready(self, *, force: bool = False) -> bool:
        """Return whether materializing a cumulative snapshot can produce a write."""
        if self._disabled:
            return False
        if force or self._last_write_at is None:
            return True
        return perf_counter() - self._last_write_at >= self._minimum_interval_seconds

    def publish(self, text: str, *, force: bool = False) -> bool:
        """Store a newer cumulative snapshot when cadence and ownership permit."""
        if self._disabled or not text.strip() or text == self._last_text:
            return False
        now = perf_counter()
        if (
            not force
            and self._last_write_at is not None
            and now - self._last_write_at < self._minimum_interval_seconds
        ):
            return False

        try:
            with self._session_factory() as db:
                message = (
                    db.query(ChatMessage)
                    .filter(ChatMessage.id == self._message_id)
                    .with_for_update()
                    .first()
                )
                if (
                    message is None
                    or message.status != MessageProcessingStatus.PROCESSING.value
                    or message.stream_generation != self._stream_generation
                ):
                    self._disabled = True
                    return False
                message.partial_text = text
                message.stream_revision = int(message.stream_revision or 0) + 1
                message.stream_updated_at = datetime.now(UTC)
                db.commit()
        except Exception:  # noqa: BLE001
            self._disabled = True
            logger.warning(
                "Disabling advisory chat partials after a persistence failure",
                exc_info=True,
                extra={
                    "message_id": self._message_id,
                    "stream_generation": self._stream_generation,
                },
            )
            return False

        self._last_text = text
        self._last_write_at = now
        self.write_count += 1
        if self.first_partial_ms is None:
            self.first_partial_ms = (now - self._started_at) * 1000
        return True


def build_final_text_event_stream_handler(
    writer: DurableChatPartialWriter,
) -> Callable[[RunContext[Any], AsyncIterable[Any]], Awaitable[None]]:
    """Stream only text from a model response confirmed as the final result."""

    async def handle_events(
        _ctx: RunContext[Any],
        events: AsyncIterable[Any],
    ) -> None:
        text_parts: dict[int, list[str]] = {}
        is_final_text_response = False
        async for event in events:
            changed = False
            force = False
            if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
                text_parts[event.index] = [event.part.content]
                changed = True
            elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                text_parts.setdefault(event.index, []).append(event.delta.content_delta)
                changed = True
            elif isinstance(event, PartEndEvent) and isinstance(event.part, TextPart):
                text_parts[event.index] = [event.part.content]
                changed = True
                force = True
            elif isinstance(event, FinalResultEvent) and event.tool_name is None:
                is_final_text_response = True
                changed = True

            if is_final_text_response and changed and writer.is_ready(force=force):
                writer.publish(
                    "".join(chunk for index in sorted(text_parts) for chunk in text_parts[index]),
                    force=force,
                )

    return handle_events
