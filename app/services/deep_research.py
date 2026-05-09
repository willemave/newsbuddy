"""Deep research service using OpenAI's o4-mini-deep-research model.

This service uses the OpenAI Responses API to perform deep research queries.
Deep research runs as a background task with web search and code interpreter tools.
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter

from openai import APIConnectionError, APIError, RateLimitError
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.observability import build_log_extra
from app.core.settings import get_settings
from app.models.schema import ChatMessage, ChatSession, Content, MessageProcessingStatus
from app.services.langfuse_tracing import langfuse_trace_context
from app.services.llm_models import DEEP_RESEARCH_MODEL
from app.services.vendor_costs import record_vendor_usage_out_of_band

try:
    from langfuse.openai import AsyncOpenAI
except Exception:  # noqa: BLE001
    from openai import AsyncOpenAI

logger = get_logger(__name__)

# Deep research configuration
DEEP_RESEARCH_TIMEOUT = 600.0  # 10 minutes max
POLL_INTERVAL = 2.0  # Poll every 2 seconds
MAX_POLL_ATTEMPTS = 300  # 10 minutes at 2 second intervals


@dataclass
class DeepResearchResult:
    """Result from a deep research query."""

    response_id: str
    status: str
    output_text: str | None
    sources: list[dict] | None
    usage: dict | None
    error: str | None


class DeepResearchClient:
    """Client for OpenAI's deep research Responses API using official SDK."""

    def __init__(self) -> None:
        """Initialize the deep research client."""
        self._settings = get_settings()
        self._client: AsyncOpenAI | None = None

    def _get_client(self) -> AsyncOpenAI:
        """Get or create the OpenAI async client."""
        if self._client is None:
            if not self._settings.openai_api_key:
                logger.error(
                    "Deep research client missing API key",
                    extra=build_log_extra(
                        component="deep_research",
                        operation="init_client",
                        event_name="assistant.turn",
                        status="failed",
                        context_data={"failure_class": "MissingApiKey"},
                    ),
                )
                raise ValueError("OPENAI_API_KEY not configured in settings")
            logger.debug(
                "Creating deep research client",
                extra=build_log_extra(
                    component="deep_research",
                    operation="init_client",
                    event_name="assistant.turn",
                    status="started",
                ),
            )
            self._client = AsyncOpenAI(
                api_key=self._settings.openai_api_key,
                timeout=DEEP_RESEARCH_TIMEOUT,
            )
        return self._client

    async def close(self) -> None:
        """Close the OpenAI client."""
        if self._client:
            await self._client.close()
            self._client = None
            logger.debug(
                "Closed deep research client",
                extra=build_log_extra(
                    component="deep_research",
                    operation="close_client",
                    event_name="assistant.turn",
                    status="completed",
                ),
            )

    async def start_research(
        self,
        query: str,
        context: str | None = None,
    ) -> str:
        """Start a deep research query in background mode.

        Args:
            query: The research query to execute.
            context: Optional context to include with the query.

        Returns:
            The response ID for polling.

        Raises:
            openai.APIError: If the request fails.
            ValueError: If the API key is not configured.
        """
        client = self._get_client()

        # Build the input with optional context
        full_input = query
        if context:
            full_input = f"Context:\n{context}\n\nResearch Query:\n{query}"
            logger.debug(
                "[DeepResearch:CONTEXT] Added context (len=%d) to query",
                len(context),
            )

        logger.info(
            "Deep research request submitted",
            extra=build_log_extra(
                component="deep_research",
                operation="start_research",
                event_name="assistant.turn.llm_started",
                status="started",
                context_data={
                    "model": DEEP_RESEARCH_MODEL,
                    "input_chars": len(full_input),
                    "has_context": context is not None,
                },
            ),
        )

        try:
            # Use the responses API with background mode
            response = await client.responses.create(
                model=DEEP_RESEARCH_MODEL,
                input=full_input,
                reasoning={"summary": "detailed"},
                background=True,
                tools=[
                    {"type": "web_search_preview"},
                    {"type": "code_interpreter", "container": {"type": "auto"}},
                ],
            )

            response_id = response.id
            logger.info(
                "Deep research queued",
                extra=build_log_extra(
                    component="deep_research",
                    operation="start_research",
                    event_name="assistant.turn.llm_started",
                    status="completed",
                    context_data={"response_id": response_id, "response_status": response.status},
                ),
            )
            return response_id

        except RateLimitError as e:
            logger.error(
                "Deep research rate limit exceeded",
                extra=build_log_extra(
                    component="deep_research",
                    operation="start_research",
                    event_name="assistant.turn.llm_started",
                    status="failed",
                    context_data={"failure_class": type(e).__name__},
                ),
            )
            raise
        except APIConnectionError as e:
            logger.error(
                "Deep research connection failed",
                extra=build_log_extra(
                    component="deep_research",
                    operation="start_research",
                    event_name="assistant.turn.llm_started",
                    status="failed",
                    context_data={"failure_class": type(e).__name__},
                ),
            )
            raise
        except APIError as e:
            logger.error(
                "Deep research API error",
                extra=build_log_extra(
                    component="deep_research",
                    operation="start_research",
                    event_name="assistant.turn.llm_started",
                    status="failed",
                    context_data={
                        "failure_class": type(e).__name__,
                        "status_code": getattr(e, "status_code", None),
                    },
                ),
            )
            raise

    async def poll_result(self, response_id: str) -> DeepResearchResult:
        """Poll for the result of a deep research query.

        Args:
            response_id: The response ID from start_research.

        Returns:
            DeepResearchResult with current status and any available output.
        """
        client = self._get_client()

        try:
            response = await client.responses.retrieve(response_id)
        except APIError as e:
            logger.error(
                "Deep research poll failed",
                extra=build_log_extra(
                    component="deep_research",
                    operation="poll_result",
                    event_name="assistant.turn.llm_completed",
                    status="failed",
                    context_data={
                        "response_id": response_id,
                        "status_code": getattr(e, "status_code", None),
                    },
                ),
            )
            raise

        status = response.status or "unknown"

        logger.debug(
            "[DeepResearch:POLL] response_id=%s status=%s",
            response_id,
            status,
        )

        # Extract output text from the response
        output_text = None
        sources = None

        if status in ("succeeded", "completed"):
            # Try to get output_text directly first
            if hasattr(response, "output_text") and response.output_text:
                output_text = response.output_text
            elif hasattr(response, "output") and response.output:
                # Parse output items for message content
                for item in response.output:
                    if hasattr(item, "type") and item.type == "message":
                        if hasattr(item, "content"):
                            for c in item.content:
                                if hasattr(c, "type") and c.type == "output_text":
                                    output_text = getattr(c, "text", "")
                                    break
                        if output_text:
                            break

            # Log token usage if available
            if hasattr(response, "usage") and response.usage:
                usage = response.usage
                logger.info(
                    "Deep research usage received",
                    extra=build_log_extra(
                        component="deep_research",
                        operation="poll_result",
                        event_name="assistant.turn.llm_completed",
                        status="completed",
                        context_data={
                            "response_id": response_id,
                            "input_tokens": getattr(usage, "input_tokens", None),
                            "output_tokens": getattr(usage, "output_tokens", None),
                            "total_tokens": getattr(usage, "total_tokens", None),
                        },
                    ),
                )

        # Build usage dict from response
        usage_dict = None
        if hasattr(response, "usage") and response.usage:
            usage_dict = {
                "input_tokens": getattr(response.usage, "input_tokens", None),
                "output_tokens": getattr(response.usage, "output_tokens", None),
                "total_tokens": getattr(response.usage, "total_tokens", None),
            }

        return DeepResearchResult(
            response_id=response_id,
            status=status,
            output_text=output_text,
            sources=sources,
            usage=usage_dict,
            error=getattr(response, "error", None),
        )

    async def wait_for_completion(
        self,
        response_id: str,
        poll_interval: float = POLL_INTERVAL,
        max_attempts: int = MAX_POLL_ATTEMPTS,
    ) -> DeepResearchResult:
        """Wait for a deep research query to complete.

        Args:
            response_id: The response ID to wait for.
            poll_interval: Seconds between polls.
            max_attempts: Maximum number of poll attempts.

        Returns:
            DeepResearchResult with final status and output.
        """
        start_time = perf_counter()

        for attempt in range(max_attempts):
            result = await self.poll_result(response_id)

            if result.status in ("succeeded", "completed"):
                duration = perf_counter() - start_time
                logger.info(
                    "Deep research completed",
                    extra=build_log_extra(
                        component="deep_research",
                        operation="wait_for_completion",
                        event_name="assistant.turn.llm_completed",
                        status="completed",
                        duration_ms=duration * 1000,
                        context_data={
                            "response_id": response_id,
                            "attempts": attempt + 1,
                            "output_chars": len(result.output_text) if result.output_text else 0,
                        },
                    ),
                )
                return result

            if result.status == "failed":
                duration = perf_counter() - start_time
                logger.error(
                    "Deep research failed",
                    extra=build_log_extra(
                        component="deep_research",
                        operation="wait_for_completion",
                        event_name="assistant.turn.llm_completed",
                        status="failed",
                        duration_ms=duration * 1000,
                        context_data={"response_id": response_id, "error": result.error},
                    ),
                )
                return result

            # Still processing, wait and poll again
            if attempt % 10 == 0:  # Log every 10 attempts (~20 seconds)
                elapsed = perf_counter() - start_time
                logger.info(
                    "Deep research still polling",
                    extra=build_log_extra(
                        component="deep_research",
                        operation="wait_for_completion",
                        event_name="assistant.turn.llm_completed",
                        status="started",
                        duration_ms=elapsed * 1000,
                        context_data={
                            "response_id": response_id,
                            "response_status": result.status,
                            "attempt": attempt + 1,
                        },
                    ),
                )

            await asyncio.sleep(poll_interval)

        # Timeout
        duration = perf_counter() - start_time
        logger.error(
            "Deep research timed out",
            extra=build_log_extra(
                component="deep_research",
                operation="wait_for_completion",
                event_name="assistant.turn.llm_completed",
                status="failed",
                duration_ms=duration * 1000,
                context_data={"response_id": response_id, "max_attempts": max_attempts},
            ),
        )
        return DeepResearchResult(
            response_id=response_id,
            status="timeout",
            output_text=None,
            sources=None,
            usage=None,
            error=f"Research timed out after {duration:.1f} seconds",
        )


# Global client instance
_client: DeepResearchClient | None = None


def get_deep_research_client() -> DeepResearchClient:
    """Get the global deep research client."""
    global _client
    if _client is None:
        _client = DeepResearchClient()
    return _client


async def close_deep_research_client() -> None:
    """Close the global deep research client.

    Call this during application shutdown to release HTTP connections.
    """
    global _client
    if _client is not None:
        await _client.close()
        _client = None


async def process_deep_research_message(
    session_id: int,
    message_id: int,
    user_prompt: str,
    *,
    source: str = "realtime",
    task_id: int | None = None,
) -> None:
    """Process a deep research message asynchronously.

    This function runs independently after the endpoint returns.
    It submits the research query, polls for completion, and updates
    the message record with the result.

    Args:
        session_id: Chat session ID.
        message_id: ChatMessage ID to update on completion.
        user_prompt: The user's research query.
        source: Request source label (`realtime` or `queue`).
        task_id: Optional queue task identifier.
    """
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

    from app.core.db import get_session_factory

    total_start = perf_counter()
    logger.info(
        "Deep research turn started",
        extra=build_log_extra(
            component="deep_research",
            operation="process_message",
            event_name="chat.turn",
            status="started",
            session_id=session_id,
            message_id=message_id,
            source=source,
            context_data={"prompt_chars": len(user_prompt), "task_id": task_id},
        ),
    )

    SessionLocal = get_session_factory()
    db = SessionLocal()

    try:
        session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
        if not session:
            logger.error("[DeepResearch:ERROR] Session %s not found", session_id)
            return

        # Build context from article if available
        context = None
        if session.content_id:
            content = db.query(Content).filter(Content.id == session.content_id).first()
            if content:
                context = _build_research_context(content)
                logger.info(
                    "Deep research context built",
                    extra=build_log_extra(
                        component="deep_research",
                        operation="build_context",
                        event_name="chat.turn.context_built",
                        status="completed",
                        session_id=session_id,
                        message_id=message_id,
                        user_id=session.user_id,
                        content_id=session.content_id,
                        source=source,
                        context_data={"context_chars": len(context) if context else 0},
                    ),
                )
            else:
                logger.warning(
                    "[DeepResearch:CONTEXT] Content not found content_id=%s",
                    session.content_id,
                )
        elif session.context_snapshot:
            context = session.context_snapshot

        logger.info(
            "Deep research LLM call started",
            extra=build_log_extra(
                component="deep_research",
                operation="llm_call",
                event_name="chat.turn.llm_started",
                status="started",
                session_id=session_id,
                message_id=message_id,
                user_id=session.user_id,
                content_id=session.content_id,
                source=source,
                task_id=task_id,
                context_data={"model": DEEP_RESEARCH_MODEL},
            ),
        )
        # Start the deep research
        client = get_deep_research_client()
        with langfuse_trace_context(
            trace_name="chat.deep_research",
            user_id=session.user_id,
            session_id=session.id,
            metadata={
                "source": source,
                "model_spec": DEEP_RESEARCH_MODEL,
                "content_id": session.content_id,
                "message_id": message_id,
                "task_id": task_id,
            },
            tags=["chat", "deep_research", source],
        ):
            response_id = await client.start_research(user_prompt, context)

            logger.info(
                "[DeepResearch:SUBMITTED] sid=%s mid=%s response_id=%s user_id=%s",
                session_id,
                message_id,
                response_id,
                session.user_id,
            )

            # Wait for completion
            result = await client.wait_for_completion(response_id)

        if result.status in ("succeeded", "completed") and result.output_text:
            # Build the message list with user request and assistant response
            from pydantic_ai.messages import (
                ModelMessagesTypeAdapter,
                ModelRequest,
                ModelResponse,
                TextPart,
                UserPromptPart,
            )

            messages: list[ModelRequest | ModelResponse] = [
                ModelRequest(parts=[UserPromptPart(content=user_prompt)]),
                ModelResponse(parts=[TextPart(content=result.output_text)]),
            ]

            message_json = ModelMessagesTypeAdapter.dump_json(messages).decode("utf-8")

            # Update the message with the result
            db_message = db.query(ChatMessage).filter(ChatMessage.id == message_id).first()
            if db_message:
                db_message.message_list = message_json
                db_message.status = MessageProcessingStatus.COMPLETED.value

                # Update session timestamps
                session.last_message_at = datetime.now(UTC)
                session.updated_at = datetime.now(UTC)
                db.commit()

                total_ms = (perf_counter() - total_start) * 1000
                logger.info(
                    "Deep research turn completed",
                    extra=build_log_extra(
                        component="deep_research",
                        operation="process_message",
                        event_name="chat.turn",
                        status="completed",
                        duration_ms=total_ms,
                        session_id=session_id,
                        message_id=message_id,
                        user_id=session.user_id,
                        content_id=session.content_id,
                        source=source,
                        task_id=task_id,
                        context_data={
                            "output_chars": len(result.output_text),
                            "model": DEEP_RESEARCH_MODEL,
                        },
                    ),
                )
                if result.usage:
                    record_vendor_usage_out_of_band(
                        provider="deep_research",
                        model=DEEP_RESEARCH_MODEL,
                        feature="chat",
                        operation="chat.deep_research",
                        source=source,
                        usage=result.usage,
                        task_id=task_id,
                        content_id=session.content_id,
                        session_id=session_id,
                        message_id=message_id,
                        user_id=session.user_id,
                        metadata={"response_id": response_id},
                    )
        else:
            # Research failed or timed out
            error_msg = result.error or f"Research failed with status: {result.status}"
            _update_message_failed(db, message_id, error_msg)

            total_ms = (perf_counter() - total_start) * 1000
            logger.error(
                "Deep research turn failed",
                extra=build_log_extra(
                    component="deep_research",
                    operation="process_message",
                    event_name="chat.turn",
                    status="failed",
                    duration_ms=total_ms,
                    session_id=session_id,
                    message_id=message_id,
                    user_id=session.user_id,
                    content_id=session.content_id,
                    source=source,
                    task_id=task_id,
                    context_data={"failure_class": result.status, "response_id": response_id},
                ),
            )

    except Exception as exc:
        total_ms = (perf_counter() - total_start) * 1000
        logger.exception(
            "Deep research turn raised exception",
            extra=build_log_extra(
                component="deep_research",
                operation="process_message",
                event_name="chat.turn",
                status="failed",
                duration_ms=total_ms,
                session_id=session_id,
                message_id=message_id,
                source=source,
                task_id=task_id,
                context_data={"failure_class": type(exc).__name__},
            ),
        )
        try:
            _update_message_failed(db, message_id, str(exc))
        except Exception as update_exc:
            logger.error(
                "[DeepResearch:UPDATE_FAILED] mid=%s error=%s",
                message_id,
                update_exc,
            )
    finally:
        db.close()


def _build_research_context(content: Content) -> str | None:
    """Build context string from content for research."""
    if not content:
        return None

    parts = []

    if content.title:
        parts.append(f"Article Title: {content.title}")

    if content.url:
        parts.append(f"URL: {content.url}")

    if content.source:
        parts.append(f"Source: {content.source}")

    if content.content_metadata:
        metadata = content.content_metadata
        summary = metadata.get("summary", {})

        overview = (
            summary.get("summary")
            or summary.get("overview")
            or summary.get("hook")
            or summary.get("takeaway")
        )
        if overview:
            parts.append(f"\nOverview: {overview}")

        bullet_points = summary.get("key_points") or summary.get("bullet_points")
        if bullet_points:
            points = [
                bp.get("text", "") if isinstance(bp, dict) else str(bp)
                for bp in bullet_points
                if isinstance(bp, (dict, str))
            ]
        else:
            bullet_points = summary.get("insights", [])
            points = [
                ins.get("insight", "")
                for ins in bullet_points
                if isinstance(ins, dict) and ins.get("insight")
            ]
        if points:
            parts.append("\nKey Points:")
            for point in points[:5]:
                parts.append(f"  - {point}")

    context = "\n".join(parts) if parts else None

    if context:
        logger.debug(
            "[DeepResearch:CONTEXT] Built context with %d parts, len=%d",
            len(parts),
            len(context),
        )

    return context


def _update_message_failed(db: Session, message_id: int, error: str) -> None:
    """Mark a message as failed."""
    db_message = db.query(ChatMessage).filter(ChatMessage.id == message_id).first()
    if db_message:
        db_message.status = MessageProcessingStatus.FAILED.value
        db_message.error = error
        db.commit()
        logger.debug("[DeepResearch:DB] Updated message %s to failed", message_id)
