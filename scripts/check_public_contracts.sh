#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ $# -ne 0 ]]; then
  echo "Usage: $0" >&2
  exit 2
fi
exec "$REPO_ROOT/scripts/regenerate_public_contracts.sh" --check
