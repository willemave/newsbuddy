#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$REPO_ROOT"

PYTHONPATH="$REPO_ROOT" uv run python scripts/export_openapi_schema.py \
  --output "$REPO_ROOT/docs/library/reference/openapi.json"

PYTHONPATH="$REPO_ROOT" uv run python scripts/generate_ios_contracts.py \
  --output "$REPO_ROOT/client/newsly/newsly/Models/Generated/APIContracts.generated.swift" \
  --models-output "$REPO_ROOT/client/newsly/newsly/Models/Generated/APIModels.generated.swift"

PYTHONPATH="$REPO_ROOT" uv run python scripts/export_agent_openapi_schema.py \
  --output "$REPO_ROOT/cli/openapi/agent-openapi.json"

PYTHONPATH="$REPO_ROOT" uv run python scripts/generate_go_contracts.py \
  --output "$REPO_ROOT/cli/internal/api/contracts_gen.go"

echo "Regenerated public OpenAPI, Go CLI, and Swift contract artifacts."
