#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ $# -ne 0 ]]; then
  echo "Usage: $0" >&2
  exit 2
fi

CONTRACT_TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/newsly-public-contracts-check.XXXXXX")"
trap 'rm -rf -- "$CONTRACT_TMP_DIR"' EXIT

cd "$REPO_ROOT"
export SQLX_OFFLINE="${SQLX_OFFLINE:-true}"
DRIFTED=false
RUST_EXPORT=(
  cargo run --quiet --locked --manifest-path rust/Cargo.toml
  -p newsly-api --bin export_openapi --
)
RUST_CLIENT_CODEGEN=(
  cargo run --quiet --locked --manifest-path rust/Cargo.toml
  -p newsly-contract-codegen --
)
CLIENT_POLICY="contracts/client_codegen_policy.toml"

check_artifact() {
  local expected_path="$1"
  local actual_path="$2"
  local label="$3"

  if [[ -f "$expected_path" ]] && cmp -s "$expected_path" "$actual_path"; then
    return
  fi

  DRIFTED=true
  echo "Contract drift detected: $expected_path ($label)" >&2
  diff -u "$expected_path" "$actual_path" || true
}

"${RUST_EXPORT[@]}" --output "$CONTRACT_TMP_DIR/public.openapi.json"
"${RUST_CLIENT_CODEGEN[@]}" \
  --openapi "$CONTRACT_TMP_DIR/public.openapi.json" \
  --policy "$CLIENT_POLICY" \
  --app-swift-contracts "$CONTRACT_TMP_DIR/APIContracts.generated.swift" \
  --app-swift-models "$CONTRACT_TMP_DIR/APIModels.generated.swift" \
  --share-swift-contracts "$CONTRACT_TMP_DIR/ShareContracts.generated.swift" \
  --share-swift-models "$CONTRACT_TMP_DIR/ShareModels.generated.swift"
check_artifact \
  docs/library/reference/openapi.json \
  "$CONTRACT_TMP_DIR/public.openapi.json" \
  "Rust public OpenAPI"
check_artifact \
  contracts/openapi/public.openapi.json \
  "$CONTRACT_TMP_DIR/public.openapi.json" \
  "Rust contract-corpus OpenAPI"

check_artifact \
  client/newsly/newsly/Models/Generated/APIContracts.generated.swift \
  "$CONTRACT_TMP_DIR/APIContracts.generated.swift" \
  "Rust-generated app Swift enums"
check_artifact \
  client/newsly/newsly/Models/Generated/APIModels.generated.swift \
  "$CONTRACT_TMP_DIR/APIModels.generated.swift" \
  "Rust-generated app Swift models"
check_artifact \
  client/newsly/ShareExtension/Generated/ShareContracts.generated.swift \
  "$CONTRACT_TMP_DIR/ShareContracts.generated.swift" \
  "Rust-generated Share Extension enums"
check_artifact \
  client/newsly/ShareExtension/Generated/ShareModels.generated.swift \
  "$CONTRACT_TMP_DIR/ShareModels.generated.swift" \
  "Rust-generated Share Extension models"

if [[ "$DRIFTED" == true ]]; then
  echo "Contract artifacts are stale. Run scripts/regenerate_public_contracts.sh and commit the resulting diff." >&2
  exit 1
fi

echo "Rust-owned public contract artifacts are up to date."
