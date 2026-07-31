#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CODEX_BIN="${CODEX_BIN:-codex}"
MODEL="${MODEL:-gpt-5.6-luna}"

run_codex_prompt() {
  local prompt="$1"
  "$CODEX_BIN" exec \
    --cd "$REPO_ROOT" \
    --model "$MODEL" \
    --full-auto \
    - <<<"$prompt"
}

regenerate_overview() {
  local source_dir="$1"
  local output_file="$2"
  local prompt

  printf -v prompt '%s\n%s\n%s\n%s' \
    "Inspect the top-level folder \`$source_dir\` in this repository and update \`$output_file\`." \
    "Use the current source tree and the existing markdown file as context." \
    "Write concise markdown that covers the folder's purpose, key files, important subfolders, and notable runtime or build behavior." \
    "Keep the result factual and markdown-only."

  printf 'Regenerating %s\n' "$output_file"
  run_codex_prompt "$prompt"
}

for source_dir in app admin cli client config docker migrations scripts tests; do
  if [[ ! -d "$REPO_ROOT/$source_dir" ]]; then
    continue
  fi
  regenerate_overview "$source_dir" "docs/codebase/$source_dir/00-overview.md"
done
