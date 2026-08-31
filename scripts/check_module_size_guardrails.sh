#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
config_path="$repo_root/config/module_size_guardrails.json"
default_max_lines=1000

if ! command -v jq >/dev/null 2>&1; then
  echo "module size guardrails require jq" >&2
  exit 1
fi

if ! jq -e '
  type == "object"
  and all(to_entries[]; (.key | type == "string")
    and (.value | type == "number" and floor == . and . > 0))
' "$config_path" >/dev/null; then
  echo "guardrail config must map paths to positive integer line limits" >&2
  exit 1
fi

checked=0
violations=0
explicit_count="$(jq 'length' "$config_path")"

check_file() {
  local relative_path="$1"
  local limit="$2"
  local absolute_path="$repo_root/$relative_path"

  checked=$((checked + 1))
  if [[ ! -f "$absolute_path" ]]; then
    echo "missing guardrail target: $relative_path" >&2
    violations=$((violations + 1))
    return
  fi

  local line_count
  line_count="$(awk 'END { print NR }' "$absolute_path")"
  if ((line_count > limit)); then
    echo "$relative_path: $line_count lines (limit $limit)" >&2
    violations=$((violations + 1))
  fi
}

while IFS=$'\t' read -r relative_path limit; do
  check_file "$relative_path" "$limit"
done < <(jq -r 'to_entries[] | [.key, .value] | @tsv' "$config_path")

check_discovered_file() {
  local absolute_path="$1"
  relative_path="${absolute_path#"$repo_root/"}"
  if [[ "$relative_path" == client/newsly/newsly/Models/Generated/* ]]; then
    return
  fi
  if jq -e --arg path "$relative_path" 'has($path)' "$config_path" >/dev/null; then
    return
  fi
  check_file "$relative_path" "$default_max_lines"
}

while IFS= read -r -d '' absolute_path; do
  check_discovered_file "$absolute_path"
done < <(find "$repo_root/client/newsly/newsly" -type f -name '*.swift' -print0)

while IFS= read -r -d '' absolute_path; do
  check_discovered_file "$absolute_path"
done < <(find "$repo_root/rust/crates" -type f -name '*.rs' -print0)

if ((violations > 0)); then
  echo "module size guardrail check failed with $violations violation(s)" >&2
  exit 1
fi

echo "Module size guardrails OK ($checked files checked, $explicit_count ratcheted)."
