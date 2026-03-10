"""Export a CLI-focused OpenAPI schema for the Newsly agent."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from app.main import app

ALLOWED_OPERATIONS: dict[tuple[str, str], dict[str, Any]] = {
    ("/api/jobs/{job_id}", "get"): {
        "operation_id": "getJob",
        "tags": ["jobs"],
    },
    ("/api/agent/search", "post"): {
        "operation_id": "searchAgent",
        "tags": ["search"],
    },
    ("/api/agent/onboarding", "post"): {
        "operation_id": "startOnboarding",
        "tags": ["onboarding"],
    },
    ("/api/agent/onboarding/{run_id}", "get"): {
        "operation_id": "getOnboarding",
        "tags": ["onboarding"],
    },
    ("/api/agent/onboarding/{run_id}/complete", "post"): {
        "operation_id": "completeOnboarding",
        "tags": ["onboarding"],
    },
    ("/api/agent/digests", "post"): {
        "operation_id": "generateDigest",
        "tags": ["digests"],
    },
    ("/api/content/", "get"): {
        "operation_id": "listContent",
        "tags": ["content"],
    },
    ("/api/content/{content_id}", "get"): {
        "operation_id": "getContent",
        "tags": ["content"],
    },
    ("/api/content/submit", "post"): {
        "operation_id": "submitContent",
        "tags": ["content"],
    },
    ("/api/content/daily-digests", "get"): {
        "operation_id": "listDigests",
        "tags": ["digests"],
    },
    ("/api/scrapers/", "get"): {
        "operation_id": "listSources",
        "tags": ["sources"],
    },
    ("/api/scrapers/subscribe", "post"): {
        "operation_id": "subscribeSource",
        "tags": ["sources"],
    },
}


def _normalize_openapi_30_shapes(value: Any) -> Any:
    """Normalize OpenAPI 3.1 numeric exclusivity fields into 3.0-compatible shapes."""
    if isinstance(value, list):
        return [_normalize_openapi_30_shapes(item) for item in value]
    if not isinstance(value, dict):
        return value

    normalized = {key: _normalize_openapi_30_shapes(item) for key, item in value.items()}

    exclusive_minimum = normalized.get("exclusiveMinimum")
    if isinstance(exclusive_minimum, int | float) and not isinstance(exclusive_minimum, bool):
        normalized["minimum"] = exclusive_minimum
        normalized["exclusiveMinimum"] = True

    exclusive_maximum = normalized.get("exclusiveMaximum")
    if isinstance(exclusive_maximum, int | float) and not isinstance(exclusive_maximum, bool):
        normalized["maximum"] = exclusive_maximum
        normalized["exclusiveMaximum"] = True

    for union_key in ("anyOf", "oneOf"):
        union_value = normalized.get(union_key)
        if not isinstance(union_value, list) or len(union_value) != 2:
            continue
        nullable_options = [
            option
            for option in union_value
            if isinstance(option, dict) and option.get("type") == "null"
        ]
        concrete_options = [
            option
            for option in union_value
            if not (isinstance(option, dict) and option.get("type") == "null")
        ]
        if len(nullable_options) != 1 or len(concrete_options) != 1:
            continue

        concrete = concrete_options[0]
        merged = {
            key: item
            for key, item in normalized.items()
            if key not in {"anyOf", "oneOf"}
        }
        if isinstance(concrete, dict):
            merged = {**concrete, **merged}
        merged["nullable"] = True
        normalized = merged
        break

    return normalized


def build_agent_openapi_schema() -> dict[str, Any]:
    """Build a filtered, CLI-focused OpenAPI schema."""
    full_schema = copy.deepcopy(app.openapi())

    filtered_paths: dict[str, dict[str, Any]] = {}
    for (path, method), overrides in ALLOWED_OPERATIONS.items():
        operation = full_schema.get("paths", {}).get(path, {}).get(method)
        if operation is None:
            continue
        operation["operationId"] = overrides["operation_id"]
        operation["tags"] = overrides["tags"]
        filtered_paths.setdefault(path, {})[method] = operation

    schema: dict[str, Any] = {
        **full_schema,
        "openapi": "3.0.3",
        "info": {
            "title": "Newsly Agent CLI API",
            "version": str(full_schema.get("info", {}).get("version", "1.0.0")),
            "description": (
                "Filtered machine-oriented API contract for the standalone "
                "newsly-agent CLI."
            ),
        },
        "paths": filtered_paths,
        "tags": [
            {"name": "jobs", "description": "Async job status routes."},
            {"name": "search", "description": "Provider-backed discovery search."},
            {"name": "onboarding", "description": "Simplified onboarding routes."},
            {"name": "digests", "description": "Daily digest generation and listing."},
            {"name": "content", "description": "Content listing, detail, and submission."},
            {"name": "sources", "description": "Runtime source subscription routes."},
        ],
    }
    return _normalize_openapi_30_shapes(schema)


def export_agent_openapi_schema(output_path: Path) -> Path:
    """Export the CLI-focused OpenAPI schema to JSON."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    schema = build_agent_openapi_schema()
    output_path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output_path


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Export the Newsly agent OpenAPI schema")
    parser.add_argument(
        "--output",
        default="cli/openapi/agent-openapi.json",
        help="Output path for the filtered OpenAPI JSON",
    )
    return parser.parse_args()


def main() -> int:
    """CLI entrypoint."""
    args = parse_args()
    output_path = export_agent_openapi_schema(Path(args.output))
    print(f"Agent OpenAPI schema written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
