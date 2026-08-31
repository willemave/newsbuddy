#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

cargo fetch --manifest-path rust/Cargo.toml --locked
uv sync --project python/document_extractor --frozen --group dev
uv sync --project python/evals --frozen --group dev
npm ci
xcodebuild \
  -resolvePackageDependencies \
  -project client/newsly/newsly.xcodeproj \
  -scheme newsly \
  >/dev/null
