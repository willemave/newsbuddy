"""Shared LLM failure classification helpers."""

from __future__ import annotations

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError
from pydantic_ai.exceptions import ModelHTTPError

LLM_UNAVAILABLE_STATUS_CODES = {408}


def is_llm_unavailable_error(exc: Exception) -> bool:
    """Return true when an LLM failure is transient provider unavailability."""

    if isinstance(
        exc,
        (
            APIConnectionError,
            APITimeoutError,
            TimeoutError,
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ReadTimeout,
            httpx.RemoteProtocolError,
        ),
    ):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code >= 500 or exc.status_code in LLM_UNAVAILABLE_STATUS_CODES
    if isinstance(exc, ModelHTTPError):
        status_code = int(getattr(exc, "status_code", 0) or 0)
        return status_code >= 500 or status_code in LLM_UNAVAILABLE_STATUS_CODES
    return False
