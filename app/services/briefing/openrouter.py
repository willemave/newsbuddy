"""Shared OpenRouter JSON-schema transport for Briefing generation."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Any, Protocol

import httpx
from openai import OpenAI

from app.core.settings import Settings, get_settings
from app.services.llm_models import OPENROUTER_REASONING_CONFIG, openrouter_provider_config


@dataclass(frozen=True)
class StructuredOutputResponse:
    content: str
    usage: dict[str, int | None] | None


class StructuredOutputRequester(Protocol):
    def __call__(
        self,
        *,
        model_spec: str,
        system_prompt: str,
        user_prompt: str,
        schema_name: str,
        schema: dict[str, Any],
        timeout_seconds: int | None = None,
    ) -> StructuredOutputResponse: ...


class BriefingOpenRouterClient:
    """Refresh-scoped, thread-safe structured-output client.

    The underlying synchronous OpenAI/httpx client is safe to share across the
    bounded composition worker pool. The owner must close it after the refresh.
    """

    def __init__(
        self,
        *,
        timeout_seconds: int,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._request_timeout = _request_timeout(
            max(timeout_seconds, self._settings.briefing_taxonomy_llm_timeout_seconds)
        )
        self._lock = Lock()
        self._client: OpenAI | None = None
        self._closed = False

    def request_json_schema(
        self,
        *,
        model_spec: str,
        system_prompt: str,
        user_prompt: str,
        schema_name: str,
        schema: dict[str, Any],
        timeout_seconds: int | None = None,
    ) -> StructuredOutputResponse:
        request_timeout = (
            _request_timeout(timeout_seconds)
            if timeout_seconds is not None
            else self._request_timeout
        )
        client = self._get_or_create_client()
        response = client.chat.completions.create(
            model=model_spec.split(":", 1)[1],
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                },
            },
            extra_body={
                "provider": openrouter_provider_config(),
                "reasoning": OPENROUTER_REASONING_CONFIG,
            },
            timeout=request_timeout,
        )
        content = response.choices[0].message.content
        if not content:
            raise RuntimeError("OpenRouter returned an empty structured response")
        return StructuredOutputResponse(content=content, usage=_response_usage(response))

    def close(self) -> None:
        with self._lock:
            self._closed = True
            client = self._client
            self._client = None
        if client is not None:
            client.close()

    def _get_or_create_client(self) -> OpenAI:
        with self._lock:
            if self._closed:
                raise RuntimeError("Briefing OpenRouter client is closed")
            if self._client is not None:
                return self._client
            api_key = self._settings.openrouter_api_key
            if not api_key:
                raise ValueError("OPENROUTER_API_KEY not configured in settings.")
            http_client = httpx.Client(timeout=self._request_timeout)
            try:
                client = OpenAI(
                    api_key=api_key,
                    base_url="https://openrouter.ai/api/v1",
                    timeout=self._request_timeout,
                    max_retries=0,
                    http_client=http_client,
                )
            except BaseException:
                http_client.close()
                raise
            self._client = client
            return client

    def __enter__(self) -> BriefingOpenRouterClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def request_openrouter_json_schema(
    *,
    model_spec: str,
    system_prompt: str,
    user_prompt: str,
    schema_name: str,
    schema: dict[str, Any],
    timeout_seconds: int,
    settings: Settings | None = None,
    requester: StructuredOutputRequester | None = None,
) -> StructuredOutputResponse:
    """Request strict structured output with the Briefing provider policy."""

    if requester is not None:
        return requester(
            model_spec=model_spec,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema_name=schema_name,
            schema=schema,
            timeout_seconds=timeout_seconds,
        )

    with BriefingOpenRouterClient(
        timeout_seconds=timeout_seconds,
        settings=settings,
    ) as owned_client:
        return owned_client.request_json_schema(
            model_spec=model_spec,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema_name=schema_name,
            schema=schema,
            timeout_seconds=timeout_seconds,
        )


def _request_timeout(timeout_seconds: int) -> httpx.Timeout:
    return httpx.Timeout(
        timeout_seconds,
        connect=10.0,
        read=float(timeout_seconds),
        write=20.0,
        pool=10.0,
    )


def strip_json_code_fence(content: str) -> str:
    stripped = content.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if not lines or not lines[0].strip().startswith("```") or lines[-1].strip() != "```":
        return stripped
    return "\n".join(lines[1:-1]).strip()


def _response_usage(response: object) -> dict[str, int | None] | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    input_tokens = getattr(usage, "prompt_tokens", None)
    output_tokens = getattr(usage, "completion_tokens", None)
    total_tokens = getattr(usage, "total_tokens", None)
    if input_tokens is None and output_tokens is None and total_tokens is None:
        return None
    return {
        "input_tokens": input_tokens,
        "cache_read_tokens": None,
        "cache_write_tokens": None,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }
