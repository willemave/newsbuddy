"""Langfuse bootstrap and tracing helpers."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.models.instrumented import InstrumentationSettings

from app.core.logging import get_logger
from app.core.settings import get_settings

Langfuse: Any | None
propagate_attributes: Any | None

try:
    from langfuse import Langfuse as _Langfuse
    from langfuse import propagate_attributes as _propagate_attributes

    Langfuse = _Langfuse
    propagate_attributes = _propagate_attributes
except Exception:  # noqa: BLE001
    Langfuse = None
    propagate_attributes = None

logger = get_logger(__name__)

_LANGFUSE_CLIENT: Any | None = None
_LANGFUSE_INITIALIZED = False
_LANGFUSE_READY = False
_LANGFUSE_INIT_LOCK = Lock()


def _as_int(value: object) -> int | None:
    """Convert usage values to integers when possible."""
    if value is None:
        return None
    if not isinstance(value, (int, float, str, bytes, bytearray)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_optional_string(value: str | int | None) -> str | None:
    """Normalize optional IDs to non-empty strings."""
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    return normalized


def _normalize_metadata(metadata: dict[str, Any] | None) -> dict[str, str] | None:
    """Convert context metadata to Langfuse-safe string values."""
    if not metadata:
        return None

    normalized: dict[str, str] = {}
    for key, value in metadata.items():
        if value is None:
            continue
        if isinstance(value, (dict, list, tuple, set)):
            normalized[str(key)] = json.dumps(value, ensure_ascii=False, default=str)
        else:
            normalized[str(key)] = str(value)

    if not normalized:
        return None
    return normalized


def _normalize_tags(tags: list[str] | None) -> list[str] | None:
    """Normalize tags for Langfuse context propagation."""
    if not tags:
        return None
    normalized = [tag.strip() for tag in tags if isinstance(tag, str) and tag.strip()]
    if not normalized:
        return None
    return normalized


def _first_non_none(primary: object, fallback: object) -> object:
    """Return the first value unless it is None."""
    if primary is not None:
        return primary
    return fallback


def initialize_langfuse_tracing() -> bool:
    """Initialize Langfuse and instrument pydantic-ai globally.

    Returns:
        True when tracing is active, otherwise False.
    """
    global _LANGFUSE_CLIENT, _LANGFUSE_INITIALIZED, _LANGFUSE_READY

    if _LANGFUSE_INITIALIZED:
        return _LANGFUSE_READY

    with _LANGFUSE_INIT_LOCK:
        if _LANGFUSE_INITIALIZED:
            return _LANGFUSE_READY

        try:
            settings = get_settings()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Langfuse settings unavailable; tracing disabled: %s", exc)
            _LANGFUSE_INITIALIZED = True
            _LANGFUSE_READY = False
            return False

        if not settings.langfuse_enabled:
            logger.info("Langfuse tracing disabled via settings")
            _LANGFUSE_INITIALIZED = True
            _LANGFUSE_READY = False
            return False

        if not settings.langfuse_public_key or not settings.langfuse_secret_key:
            logger.info("Langfuse keys are not configured; tracing disabled")
            _LANGFUSE_INITIALIZED = True
            _LANGFUSE_READY = False
            return False

        if Langfuse is None:
            logger.warning("Langfuse package unavailable; tracing disabled")
            _LANGFUSE_INITIALIZED = True
            _LANGFUSE_READY = False
            return False

        try:
            _LANGFUSE_CLIENT = Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                host=settings.langfuse_host,
                sample_rate=settings.langfuse_sample_rate,
                tracing_enabled=True,
            )
            Agent.instrument_all(
                InstrumentationSettings(
                    include_content=settings.langfuse_include_content,
                    include_binary_content=settings.langfuse_include_binary_content,
                    version=settings.langfuse_instrumentation_version,
                )
            )
            _LANGFUSE_INITIALIZED = True
            _LANGFUSE_READY = True
            logger.info("Langfuse tracing initialized")
        except Exception:  # noqa: BLE001
            _LANGFUSE_CLIENT = None
            _LANGFUSE_INITIALIZED = True
            _LANGFUSE_READY = False
            logger.exception("Failed to initialize Langfuse tracing")

        return _LANGFUSE_READY


def flush_langfuse_tracing() -> None:
    """Flush buffered Langfuse events."""
    if not _LANGFUSE_READY:
        return
    if _LANGFUSE_CLIENT is None:
        return

    try:
        _LANGFUSE_CLIENT.flush()
    except Exception:  # noqa: BLE001
        logger.exception("Failed to flush Langfuse events")


def extract_google_usage_details(response: object) -> dict[str, int] | None:
    """Extract token usage from Google GenAI responses when available."""
    usage_metadata = getattr(response, "usage_metadata", None)
    if usage_metadata is None:
        return None

    input_tokens = _as_int(
        _first_non_none(
            getattr(usage_metadata, "prompt_token_count", None),
            getattr(usage_metadata, "input_token_count", None),
        )
    )
    output_tokens = _as_int(
        _first_non_none(
            getattr(usage_metadata, "candidates_token_count", None),
            getattr(usage_metadata, "output_token_count", None),
        )
    )
    total_tokens = _as_int(getattr(usage_metadata, "total_token_count", None))

    details: dict[str, int] = {}
    if input_tokens is not None:
        details["input"] = input_tokens
    if output_tokens is not None:
        details["output"] = output_tokens
    if total_tokens is not None:
        details["total"] = total_tokens

    if not details:
        return None
    return details


@contextmanager
def langfuse_trace_context(
    *,
    trace_name: str | None = None,
    user_id: str | int | None = None,
    session_id: str | int | None = None,
    metadata: dict[str, Any] | None = None,
    tags: list[str] | None = None,
) -> Iterator[None]:
    """Attach attributes to the current Langfuse trace context.

    Args:
        trace_name: Optional root trace name.
        user_id: Optional user identifier.
        session_id: Optional session identifier.
        metadata: Optional metadata for grouping/filtering.
        tags: Optional tags for grouping/filtering.
    """
    if not _LANGFUSE_INITIALIZED:
        initialize_langfuse_tracing()

    if not _LANGFUSE_READY:
        yield
        return

    if propagate_attributes is None:
        yield
        return

    normalized_user_id = _normalize_optional_string(user_id)
    normalized_session_id = _normalize_optional_string(session_id)
    normalized_metadata = _normalize_metadata(metadata)
    normalized_tags = _normalize_tags(tags)

    with propagate_attributes(
        trace_name=trace_name,
        user_id=normalized_user_id,
        session_id=normalized_session_id,
        metadata=normalized_metadata,
        tags=normalized_tags,
    ):
        yield


@contextmanager
def langfuse_generation_context(
    *,
    name: str,
    model: str,
    input_data: Any | None = None,
    metadata: dict[str, Any] | None = None,
) -> Iterator[Any | None]:
    """Create a manual Langfuse generation span for non-instrumented SDK calls.

    Args:
        name: Generation operation name.
        model: Model identifier.
        input_data: Optional prompt/input payload.
        metadata: Optional metadata for filtering/debugging.

    Yields:
        Generation object when available, otherwise None.
    """
    if not _LANGFUSE_INITIALIZED:
        initialize_langfuse_tracing()

    if not _LANGFUSE_READY or _LANGFUSE_CLIENT is None:
        yield None
        return

    normalized_metadata = _normalize_metadata(metadata)
    with _LANGFUSE_CLIENT.start_as_current_observation(
        name=name,
        as_type="generation",
        model=model,
        input=input_data,
        metadata=normalized_metadata,
    ) as generation:
        try:
            yield generation
        except Exception as exc:  # noqa: BLE001
            try:
                generation.update(level="ERROR", status_message=str(exc))
            except Exception:  # noqa: BLE001
                logger.exception("Failed to update Langfuse generation error status")
            raise
