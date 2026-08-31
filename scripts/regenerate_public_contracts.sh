#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
case "${1:-all}" in
  all | --public-only)
    ;;
  *)
    echo "Usage: $0 [--public-only]" >&2
    exit 2
    ;;
esac

CONTRACT_TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/newsly-public-contracts.XXXXXX")"
trap 'rm -rf -- "$CONTRACT_TMP_DIR"' EXIT

cd "$REPO_ROOT"
export SQLX_OFFLINE="${SQLX_OFFLINE:-true}"
RUST_EXPORT=(
  cargo run --quiet --locked --manifest-path rust/Cargo.toml
  -p newsly-api --bin export_openapi --
)
RUST_CLIENT_CODEGEN=(
  cargo run --quiet --locked --manifest-path rust/Cargo.toml
  -p newsly-contract-codegen --
)
CLIENT_POLICY="contracts/client_codegen_policy.toml"

"${RUST_EXPORT[@]}" --output "$CONTRACT_TMP_DIR/public.openapi.json"
"${RUST_CLIENT_CODEGEN[@]}" \
  --openapi "$CONTRACT_TMP_DIR/public.openapi.json" \
  --policy "$CLIENT_POLICY" \
  --app-swift-contracts "$CONTRACT_TMP_DIR/APIContracts.generated.swift" \
  --app-swift-models "$CONTRACT_TMP_DIR/APIModels.generated.swift" \
  --share-swift-contracts "$CONTRACT_TMP_DIR/ShareContracts.generated.swift" \
  --share-swift-models "$CONTRACT_TMP_DIR/ShareModels.generated.swift"
mkdir -p docs/library/reference contracts/openapi
cp "$CONTRACT_TMP_DIR/public.openapi.json" docs/library/reference/openapi.json
cp "$CONTRACT_TMP_DIR/public.openapi.json" contracts/openapi/public.openapi.json
mkdir -p client/newsly/newsly/Models/Generated client/newsly/ShareExtension/Generated
cp "$CONTRACT_TMP_DIR/APIContracts.generated.swift" \
  client/newsly/newsly/Models/Generated/APIContracts.generated.swift
cp "$CONTRACT_TMP_DIR/APIModels.generated.swift" \
  client/newsly/newsly/Models/Generated/APIModels.generated.swift
cp "$CONTRACT_TMP_DIR/ShareContracts.generated.swift" \
  client/newsly/ShareExtension/Generated/ShareContracts.generated.swift
cp "$CONTRACT_TMP_DIR/ShareModels.generated.swift" \
  client/newsly/ShareExtension/Generated/ShareModels.generated.swift
echo "Wrote Rust public OpenAPI: docs/library/reference/openapi.json"
echo "Wrote Rust migration OpenAPI: contracts/openapi/public.openapi.json"
echo "Wrote Rust-generated app and Share Extension Swift contracts."
