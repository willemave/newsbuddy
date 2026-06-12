#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMPDIR_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_ROOT"' EXIT
MODE="${1:-all}"

case "$MODE" in
  all)
    RUN_PYTHON_CONTRACTS=true
    RUN_GO_CONTRACTS=true
    ;;
  --python-only)
    RUN_PYTHON_CONTRACTS=true
    RUN_GO_CONTRACTS=false
    ;;
  --go-only)
    RUN_PYTHON_CONTRACTS=false
    RUN_GO_CONTRACTS=true
    ;;
  *)
    echo "Usage: $0 [--python-only|--go-only]" >&2
    exit 2
    ;;
esac

compare_file() {
  local expected="$1"
  local actual="$2"
  if ! cmp -s "$expected" "$actual"; then
    echo "Contract drift detected: $expected"
    diff -u "$expected" "$actual"
    return 1
  fi
}

cd "$REPO_ROOT"

FULL_SCHEMA_TMP="$TMPDIR_ROOT/openapi.json"
AGENT_SCHEMA_TMP="$TMPDIR_ROOT/agent-openapi.json"
IOS_ENUM_TMP="$TMPDIR_ROOT/APIContracts.generated.swift"
IOS_MODELS_TMP="$TMPDIR_ROOT/APIModels.generated.swift"
GO_TARGET_TMP="$TMPDIR_ROOT/go-internal-api"
GO_CONTRACT_TMP="$GO_TARGET_TMP/contracts_gen.go"

if [[ "$RUN_PYTHON_CONTRACTS" == "true" ]]; then
  PYTHONPATH="$REPO_ROOT" uv run python scripts/export_openapi_schema.py \
    --output "$FULL_SCHEMA_TMP" \
    >/dev/null
  compare_file "$REPO_ROOT/docs/library/reference/openapi.json" "$FULL_SCHEMA_TMP"

  PYTHONPATH="$REPO_ROOT" uv run python scripts/generate_ios_contracts.py \
    --output "$IOS_ENUM_TMP" \
    --models-output "$IOS_MODELS_TMP" \
    >/dev/null
  compare_file \
    "$REPO_ROOT/client/newsly/newsly/Models/Generated/APIContracts.generated.swift" \
    "$IOS_ENUM_TMP"
  compare_file \
    "$REPO_ROOT/client/newsly/newsly/Models/Generated/APIModels.generated.swift" \
    "$IOS_MODELS_TMP"
fi

if [[ "$RUN_GO_CONTRACTS" == "true" ]]; then
  PYTHONPATH="$REPO_ROOT" uv run python scripts/export_agent_openapi_schema.py \
    --output "$AGENT_SCHEMA_TMP" \
    >/dev/null
  compare_file "$REPO_ROOT/cli/openapi/agent-openapi.json" "$AGENT_SCHEMA_TMP"

  PYTHONPATH="$REPO_ROOT" uv run python scripts/generate_go_contracts.py \
    --output "$GO_CONTRACT_TMP" \
    >/dev/null
  compare_file "$REPO_ROOT/cli/internal/api/contracts_gen.go" "$GO_CONTRACT_TMP"
fi

echo "Public contract artifacts are up to date."
