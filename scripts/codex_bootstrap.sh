#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

uv sync --frozen
npm ci
xcodebuild \
  -resolvePackageDependencies \
  -project client/newsly/newsly.xcodeproj \
  -scheme newsly \
  >/dev/null

