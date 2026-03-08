"""Export FastAPI OpenAPI schema to a checked-in JSON file."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.main import app


def export_openapi_schema(output_path: Path) -> Path:
    """Export OpenAPI schema to JSON.

    Args:
        output_path: Target path for JSON output.

    Returns:
        Written output path.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    schema = app.openapi()
    output_path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output_path


def parse_args() -> argparse.Namespace:
    """Parse script arguments."""
    parser = argparse.ArgumentParser(description="Export OpenAPI schema to JSON")
    parser.add_argument(
        "--output",
        default="docs/library/reference/openapi.json",
        help="Output path for OpenAPI JSON",
    )
    return parser.parse_args()


def main() -> int:
    """CLI entrypoint."""
    args = parse_args()
    output_path = export_openapi_schema(Path(args.output))
    print(f"OpenAPI schema written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
