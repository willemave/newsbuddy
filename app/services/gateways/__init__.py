"""Infrastructure gateways used by pipeline and service orchestration."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "HttpGateway": ("app.services.gateways.http_gateway", "HttpGateway"),
    "get_http_gateway": ("app.services.gateways.http_gateway", "get_http_gateway"),
    "LlmGateway": ("app.services.gateways.llm_gateway", "LlmGateway"),
    "get_llm_gateway": ("app.services.gateways.llm_gateway", "get_llm_gateway"),
    "TaskQueueGateway": (
        "app.services.gateways.task_queue_gateway",
        "TaskQueueGateway",
    ),
    "get_task_queue_gateway": (
        "app.services.gateways.task_queue_gateway",
        "get_task_queue_gateway",
    ),
}

__all__ = [
    "HttpGateway",
    "LlmGateway",
    "TaskQueueGateway",
    "get_http_gateway",
    "get_llm_gateway",
    "get_task_queue_gateway",
]


def __getattr__(name: str) -> Any:
    """Load exported gateways on first access instead of package import."""
    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value
