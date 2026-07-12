"""Shared OpenRouter JSON-schema transport for Briefing generation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from openai import OpenAI

from app.core.settings import Settings, get_settings
from app.services.llm_models import OPENROUTER_REASONING_CONFIG, openrouter_provider_config


@dataclass(frozen=True)
class OpenRouterStructuredResponse:
    content: str
    usage: dict[str, int | None] | None


def request_openrouter_json_schema(
    *,
    model_spec: str,
    system_prompt: str,
    user_prompt: str,
    schema_name: str,
    schema: dict[str, Any],
    timeout_seconds: int,
    settings: Settings | None = None,
) -> OpenRouterStructuredResponse:
    """Request strict structured output with the Briefing provider policy."""

    settings = settings or get_settings()
    api_key = settings.openrouter_api_key
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not configured in settings.")

    request_timeout = httpx.Timeout(
        timeout_seconds,
        connect=10.0,
        read=float(timeout_seconds),
        write=20.0,
        pool=10.0,
    )
    client = OpenAI(
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        timeout=request_timeout,
        max_retries=0,
        http_client=httpx.Client(timeout=request_timeout),
    )
    try:
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
    finally:
        client.close()

    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("OpenRouter returned an empty structured response")
    return OpenRouterStructuredResponse(content=content, usage=_response_usage(response))


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
