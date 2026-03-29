"""Factory helpers for pydantic-ai agents."""

from __future__ import annotations

from typing import Any, TypeVar, cast

from pydantic_ai import Agent

from app.services.llm_models import build_pydantic_model

OutputT = TypeVar("OutputT")


def _build_agent(model_spec: str, output_type: type[Any], system_prompt: str) -> Agent[None, Any]:
    """Build a simple Agent with no dependencies."""
    model, model_settings = build_pydantic_model(model_spec)
    return Agent(
        model,
        deps_type=None,
        output_type=output_type,
        system_prompt=system_prompt,
        model_settings=model_settings,
    )


def get_basic_agent[OutputT](
    model_spec: str, output_type: type[OutputT], system_prompt: str
) -> Agent[None, OutputT]:
    """Return a new agent for an arbitrary task."""
    agent = _build_agent(model_spec, output_type, system_prompt)
    return cast(Agent[None, OutputT], agent)
