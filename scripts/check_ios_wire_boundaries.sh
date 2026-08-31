#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_root="$repo_root/client/newsly/newsly"
allowlist="$repo_root/config/ios_domain_codable_allowlist.txt"

declaration_pattern='^[[:space:]]*(private[[:space:]]+)?struct[[:space:]]+[A-Za-z0-9_]+(Request|Response)[[:space:]]*:[^{]*(Codable|Encodable|Decodable)'

if rg -n "$declaration_pattern" \
  "$source_root/Services" "$source_root/Repositories"; then
  echo "handwritten network DTO declared in a transport-owning directory" >&2
  exit 1
fi

declared_tmp="$(mktemp)"
allowed_tmp="$(mktemp)"
trap 'rm -f "$declared_tmp" "$allowed_tmp"' EXIT

rg --no-filename "$declaration_pattern" "$source_root" \
  -g '*.swift' \
  -g '!**/Models/Generated/**' \
  | sed -E 's/^[[:space:]]*(private[[:space:]]+)?struct[[:space:]]+([A-Za-z0-9_]+).*/\2/' \
  | LC_ALL=C sort -u >"$declared_tmp"

sed '/^[[:space:]]*#/d; /^[[:space:]]*$/d' "$allowlist" \
  | LC_ALL=C sort -u >"$allowed_tmp"

if ! diff -u "$allowed_tmp" "$declared_tmp"; then
  echo "iOS Request/Response Codable allowlist drifted" >&2
  echo "Use generated API* contracts at HTTP boundaries; allowlist only mapped domain/cache types." >&2
  exit 1
fi

echo "iOS wire boundary guard OK."
