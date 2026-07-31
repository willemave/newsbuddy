#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCHITECTURE_FILE="docs/architecture.md"
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

sections=()
while IFS= read -r section; do
  sections+=("$section")
done < <(
  rg -n '^## [0-9]+\.' "$REPO_ROOT/$ARCHITECTURE_FILE" \
    | cut -d: -f2- \
    | sed 's/^## //'
)

today="$(date +%F)"
first_section=1
for section in "${sections[@]}"; do
  printf 'Regenerating %s\n' "$section"
  if [[ "$first_section" -eq 1 ]]; then
    printf -v prompt '%s\n%s\n%s\n%s\n%s' \
      "Refresh the top matter and the \"$section\" section in \`$ARCHITECTURE_FILE\`." \
      "Use the current repository as the source of truth." \
      "Set \`Last Updated\` to \`$today\`." \
      "Preserve the existing heading structure and style." \
      "Only update the top matter and that section."
    first_section=0
  else
    printf -v prompt '%s\n%s\n%s\n%s' \
      "Refresh the \"$section\" section in \`$ARCHITECTURE_FILE\`." \
      "Use the current repository as the source of truth." \
      "Rewrite only that section and its nested subsections." \
      "Preserve the existing heading structure and style."
  fi
  run_codex_prompt "$prompt"
done
