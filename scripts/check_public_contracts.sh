#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-all}"

case "$MODE" in
  all)
    PYTHON_CHECK_ARGS=()
    ;;
  --python-only)
    PYTHON_CHECK_ARGS=(--python-only)
    ;;
  --go-only)
    PYTHON_CHECK_ARGS=(--go-only)
    ;;
  *)
    echo "Usage: $0 [--python-only|--go-only]" >&2
    exit 2
    ;;
esac

cd "$REPO_ROOT"

PYTHONPATH="$REPO_ROOT" uv run python scripts/regenerate_public_contracts.py --check "${PYTHON_CHECK_ARGS[@]+"${PYTHON_CHECK_ARGS[@]}"}"
