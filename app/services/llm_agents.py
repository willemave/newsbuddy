"""Factory helpers for pydantic-ai agents."""

from __future__ import annotations

from typing import Any, TypeVar, cast

from pydantic_ai import Agent, NativeOutput, PromptedOutput, TextOutput, ToolOutput

from app.services.llm_models import build_pydantic_model

OutputT = TypeVar("OutputT")
OPENROUTER_STRUCTURED_OUTPUT_RETRIES = 2


def _resolve_output_type(model_spec: str, output_type: Any) -> Any:
    """Use OpenRouter's native JSON-schema output for structured responses."""
    if not model_spec.startswith("openrouter:") or output_type is str:
        return output_type
    if isinstance(output_type, NativeOutput | PromptedOutput | TextOutput | ToolOutput):
        return output_type
    return NativeOutput(output_type, strict=True)


def _resolve_output_retries(model_spec: str, output_type: Any) -> int | None:
    """Allow one extra validation repair for OpenRouter structured JSON output."""
    if not model_spec.startswith("openrouter:") or output_type is str:
        return None
    if isinstance(output_type, TextOutput):
        return None
    return OPENROUTER_STRUCTURED_OUTPUT_RETRIES


def _build_agent(model_spec: str, output_type: Any, system_prompt: str) -> Agent[None, Any]:
    """Build a simple Agent with no dependencies."""
    model, model_settings = build_pydantic_model(model_spec)
    output_retries = _resolve_output_retries(model_spec, output_type)
    agent_kwargs: dict[str, Any] = {}
    if output_retries is not None:
        agent_kwargs["output_retries"] = output_retries
    return Agent(
        model,
        output_type=_resolve_output_type(model_spec, output_type),
        system_prompt=system_prompt,
        model_settings=model_settings,
        **agent_kwargs,
    )


def get_basic_agent[OutputT](
    model_spec: str, output_type: type[OutputT], system_prompt: str
) -> Agent[None, OutputT]:
    """Return a new agent for an arbitrary task."""
    agent = _build_agent(model_spec, output_type, system_prompt)
    return cast(Agent[None, OutputT], agent)
