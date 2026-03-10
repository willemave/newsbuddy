#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$REPO_ROOT"

PYTHONPATH="$REPO_ROOT" uv run python "$REPO_ROOT/scripts/export_agent_openapi_schema.py" \
  --output "$REPO_ROOT/cli/openapi/agent-openapi.json"

cd "$REPO_ROOT/cli"
go run github.com/ogen-go/ogen/cmd/ogen@v1.20.1 \
  --clean \
  --target internal/api \
  --package api \
  openapi/agent-openapi.json

gofmt -w internal/api
