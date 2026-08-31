#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_PATH="docs/library/reference/openapi.json"

if [[ "${1:-}" == "--output" && -n "${2:-}" && -z "${3:-}" ]]; then
  OUTPUT_PATH="$2"
elif [[ "$#" -ne 0 ]]; then
  echo "Usage: $0 [--output PATH]" >&2
  exit 2
fi

cd "$REPO_ROOT"
export SQLX_OFFLINE="${SQLX_OFFLINE:-true}"
cargo run --quiet --locked --manifest-path rust/Cargo.toml \
  -p newsly-api --bin export_openapi -- --output "$OUTPUT_PATH"
echo "Wrote Rust public OpenAPI: $OUTPUT_PATH"
