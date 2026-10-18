"""Pinned OpenRouter decision transport and metered billing contract."""

import math
from typing import Any

OPENROUTER_ROUTE: dict[str, Any] = {
    "name": "openrouter",
    "endpoint": "https://openrouter.ai/api/alpha/decisions",
    "key_env": "OPENROUTER_API_KEY",
    "request_model": "typesafe/jev-1.14",
    "response_model": "typesafe/jev-1.14-20261014",
    "provider": "TypeSafe",
    "input_rate_usd": 0.042 / 1_000_000,
    "output_rate_usd": 0.0,
    "billing": "reported_usage_cost",
}
OPENROUTER_PROVIDER: dict[str, Any] = {
    "only": ["typesafe"],
    "allow_fallbacks": False,
    "require_parameters": True,
    "data_collection": "deny",
    "zdr": True,
}


def validate_transport(manifest: dict[str, Any]) -> None:
    if manifest.get("schema_version") != 2 or manifest.get("transport") != OPENROUTER_ROUTE:
        raise ValueError("Inference requires a migrated, pinned OpenRouter manifest")
    if manifest["model"] != OPENROUTER_ROUTE["request_model"]:
        raise ValueError("Request model differs from pinned OpenRouter route")
    for job in manifest["jobs"]:
        request = job["request"]
        if (
            request.get("model") != manifest["model"]
            or request.get("provider") != OPENROUTER_PROVIDER
        ):
            raise ValueError("Job differs from pinned OpenRouter route")


def reported_cost(usage: dict[str, Any]) -> float:
    cost = usage.get("cost")
    if (
        isinstance(cost, bool)
        or not isinstance(cost, (int, float))
        or not math.isfinite(cost)
        or cost < 0
    ):
        raise ValueError("Missing/invalid OpenRouter metered cost")
    if usage.get("is_byok", False) is not False:
        raise ValueError("Unexpected BYOK billing")
    expected = usage["input_tokens"] * float(OPENROUTER_ROUTE["input_rate_usd"])
    if not math.isclose(cost, expected, rel_tol=1e-6, abs_tol=1e-12):
        raise ValueError("OpenRouter price differs from frozen rate")
    return float(cost)
