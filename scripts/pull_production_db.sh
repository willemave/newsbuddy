#!/usr/bin/env bash
# Pull a full Postgres dump from the production newsly container over SSH.
#
# Usage:
#   scripts/pull_production_db.sh [output_path]
#
# Defaults to ./.local_dumps/newsly_prod_<timestamp>.dump
# Follow up with scripts/load_production_snapshot.py to restore into a local DB.

set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-willem@192.3.250.10}"
REMOTE_CONTAINER="${REMOTE_CONTAINER:-newsly}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

output_path="${1:-}"
if [[ -z "${output_path}" ]]; then
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  output_dir="${PROJECT_ROOT}/.local_dumps"
  mkdir -p "${output_dir}"
  output_path="${output_dir}/newsly_prod_${timestamp}.dump"
fi

echo "Dumping production Postgres from ${REMOTE_HOST} (container: ${REMOTE_CONTAINER})"
echo "Output: ${output_path}"

ssh "${REMOTE_HOST}" \
  "sudo docker exec ${REMOTE_CONTAINER} bash -c 'PGPASSWORD=\"\$POSTGRES_PASSWORD\" pg_dump --format=custom --compress=6 --no-owner --no-privileges -h 127.0.0.1 -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\"'" \
  > "${output_path}"

size=$(du -h "${output_path}" | cut -f1)
echo "Dump complete: ${output_path} (${size})"
