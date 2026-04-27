#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMPDIR_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_ROOT"' EXIT

compare_file() {
  local expected="$1"
  local actual="$2"
  if ! cmp -s "$expected" "$actual"; then
    echo "Contract drift detected: $expected"
    diff -u "$expected" "$actual"
    return 1
  fi
}

compare_dir() {
  local expected="$1"
  local actual="$2"
  if ! diff -ru "$expected" "$actual"; then
    echo "Contract drift detected: $expected"
    return 1
  fi
}

compare_generated_go_dir() {
  local expected="$1"
  local actual="$2"
  local generated_file
  while IFS= read -r generated_file; do
    local filename
    filename="$(basename "$generated_file")"
    compare_file "$expected/$filename" "$actual/$filename"
  done < <(find "$actual" -maxdepth 1 -type f -name '*_gen.go' | sort)
}

cd "$REPO_ROOT"

FULL_SCHEMA_TMP="$TMPDIR_ROOT/openapi.json"
AGENT_SCHEMA_TMP="$TMPDIR_ROOT/agent-openapi.json"
IOS_ENUM_TMP="$TMPDIR_ROOT/APIContracts.generated.swift"
IOS_OPENAPI_TMP="$TMPDIR_ROOT/swift-openapi"
GO_TARGET_TMP="$TMPDIR_ROOT/go-internal-api"

PYTHONPATH="$REPO_ROOT" uv run python scripts/export_openapi_schema.py \
  --output "$FULL_SCHEMA_TMP" \
  >/dev/null
compare_file "$REPO_ROOT/docs/library/reference/openapi.json" "$FULL_SCHEMA_TMP"

PYTHONPATH="$REPO_ROOT" uv run python scripts/generate_ios_contracts.py \
  --output "$IOS_ENUM_TMP" \
  >/dev/null
compare_file \
  "$REPO_ROOT/client/newsly/newsly/Models/Generated/APIContracts.generated.swift" \
  "$IOS_ENUM_TMP"

OPENAPI_PATH="$FULL_SCHEMA_TMP" OUTPUT_DIRECTORY="$IOS_OPENAPI_TMP" \
  "$REPO_ROOT/scripts/generate_ios_openapi_artifacts.sh" \
  >/dev/null
compare_dir "$REPO_ROOT/client/newsly/OpenAPI/Generated" "$IOS_OPENAPI_TMP"

AGENT_OPENAPI_OUTPUT="$AGENT_SCHEMA_TMP" GO_TARGET_DIR="$GO_TARGET_TMP" \
  "$REPO_ROOT/scripts/generate_agent_cli_artifacts.sh" \
  >/dev/null
compare_file "$REPO_ROOT/cli/openapi/agent-openapi.json" "$AGENT_SCHEMA_TMP"
compare_generated_go_dir "$REPO_ROOT/cli/internal/api" "$GO_TARGET_TMP"

echo "Public contract artifacts are up to date."
