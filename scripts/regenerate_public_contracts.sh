#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$REPO_ROOT"

PYTHONPATH="$REPO_ROOT" uv run python scripts/export_openapi_schema.py \
  --output "$REPO_ROOT/docs/library/reference/openapi.json"

PYTHONPATH="$REPO_ROOT" uv run python scripts/generate_ios_contracts.py \
  --output "$REPO_ROOT/client/newsly/newsly/Models/Generated/APIContracts.generated.swift"

"$REPO_ROOT/scripts/generate_agent_cli_artifacts.sh"

echo "Regenerated public OpenAPI, Go CLI, and Swift enum contract artifacts."
