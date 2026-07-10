"""Shared LLM failure classification helpers."""

from __future__ import annotations

from collections.abc import Iterator

import httpx
from openai import APIConnectionError, APITimeoutError

LLM_TRANSIENT_STATUS_CODES = {408, 429}
LLM_UNAVAILABLE_STATUS_CODES = {408}
LLM_NETWORK_ERRORS = (
    APIConnectionError,
    APITimeoutError,
    ConnectionError,
    TimeoutError,
    httpx.TransportError,
)


def iter_exception_chain(exc: Exception) -> Iterator[Exception]:
    """Yield an exception and each explicit or implicit cause once."""

    current: BaseException | None = exc
    seen: set[int] = set()
    while isinstance(current, Exception) and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def exception_status_code(exc: Exception) -> int | None:
    """Return a typed HTTP status found anywhere in an exception chain."""

    for error in iter_exception_chain(exc):
        raw_status = getattr(error, "status_code", None)
        if isinstance(raw_status, int):
            return raw_status
        response = getattr(error, "response", None)
        response_status = getattr(response, "status_code", None)
        if isinstance(response_status, int):
            return response_status
    return None


def is_llm_error_retryable(exc: Exception) -> bool:
    """Classify provider failures for queue retry decisions.

    Network failures, timeouts, 408, 429, and 5xx responses are transient. Other
    typed 4xx responses are permanent. Unknown errors default to retryable so an
    unrecognized provider wrapper does not prematurely terminate queued work.
    """

    if any(isinstance(error, LLM_NETWORK_ERRORS) for error in iter_exception_chain(exc)):
        return True
    status_code = exception_status_code(exc)
    if status_code is None:
        return True
    if status_code in LLM_TRANSIENT_STATUS_CODES or status_code >= 500:
        return True
    return not 400 <= status_code < 500


def is_llm_unavailable_error(exc: Exception) -> bool:
    """Return true when an LLM failure is transient provider unavailability."""

    if any(isinstance(error, LLM_NETWORK_ERRORS) for error in iter_exception_chain(exc)):
        return True
    status_code = exception_status_code(exc)
    return status_code is not None and (
        status_code >= 500 or status_code in LLM_UNAVAILABLE_STATUS_CODES
    )
