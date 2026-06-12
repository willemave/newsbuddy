#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENT_OPENAPI_OUTPUT="${AGENT_OPENAPI_OUTPUT:-$REPO_ROOT/cli/openapi/agent-openapi.json}"
GO_TARGET_DIR="${GO_TARGET_DIR:-$REPO_ROOT/cli/internal/api}"
GO_CONTRACTS_OUTPUT="${GO_CONTRACTS_OUTPUT:-$GO_TARGET_DIR/contracts_gen.go}"

cd "$REPO_ROOT"

PYTHONPATH="$REPO_ROOT" uv run python "$REPO_ROOT/scripts/export_agent_openapi_schema.py" \
  --output "$AGENT_OPENAPI_OUTPUT"

mkdir -p "$GO_TARGET_DIR"
rm -f "$GO_TARGET_DIR"/oas_*_gen.go "$GO_TARGET_DIR"/datetime.go

PYTHONPATH="$REPO_ROOT" uv run python "$REPO_ROOT/scripts/generate_go_contracts.py" \
  --output "$GO_CONTRACTS_OUTPUT"
