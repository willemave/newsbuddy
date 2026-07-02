#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$REPO_ROOT"

PYTHONPATH="$REPO_ROOT" uv run python scripts/regenerate_public_contracts.py

echo "Regenerated public OpenAPI, Go CLI, and Swift contract artifacts."
