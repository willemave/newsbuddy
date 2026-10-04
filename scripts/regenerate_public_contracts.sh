#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHECK=false
if [[ $# -eq 1 && "$1" == "--check" ]]; then
  CHECK=true
elif [[ $# -ne 0 ]]; then
  echo "Usage: $0 [--check]" >&2
  exit 2
fi

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
DRIFTED=false
sync_artifact() {
  local generated="$1" destination="$2"
  if [[ "$CHECK" == true ]]; then
    if [[ ! -f "$destination" ]] || ! cmp -s "$destination" "$generated"; then
      DRIFTED=true
      echo "Contract drift detected: $destination" >&2
      diff -u "$destination" "$generated" || true
    fi
  else
    mkdir -p "$(dirname "$destination")"
    cp "$generated" "$destination"
  fi
}

sync_artifact "$CONTRACT_TMP_DIR/public.openapi.json" contracts/openapi/public.openapi.json
sync_artifact "$CONTRACT_TMP_DIR/APIContracts.generated.swift" client/newsly/newsly/Models/Generated/APIContracts.generated.swift
sync_artifact "$CONTRACT_TMP_DIR/APIModels.generated.swift" client/newsly/newsly/Models/Generated/APIModels.generated.swift
sync_artifact "$CONTRACT_TMP_DIR/ShareContracts.generated.swift" client/newsly/ShareExtension/Generated/ShareContracts.generated.swift
sync_artifact "$CONTRACT_TMP_DIR/ShareModels.generated.swift" client/newsly/ShareExtension/Generated/ShareModels.generated.swift
if [[ "$DRIFTED" == true ]]; then
  echo "Contract artifacts are stale. Run scripts/regenerate_public_contracts.sh." >&2
  exit 1
fi
if [[ "$CHECK" == true ]]; then
  echo "Rust-owned public contract artifacts are up to date."
else
  echo "Wrote canonical public OpenAPI and generated app/Share Extension Swift contracts."
fi
