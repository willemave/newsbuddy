#!/usr/bin/env bash
set -euo pipefail

postgres_bin="$(find /usr/lib/postgresql -path '*/bin/postgres' | sort -V | tail -n 1)"
if [[ -z "${postgres_bin:-}" ]]; then
  echo "postgres binary not found" >&2
  exit 1
fi

exec runuser -u postgres -- \
  "$postgres_bin" \
  -D "${PGDATA}" \
  -p "${POSTGRES_PORT:-5432}" \
  -c "listen_addresses=0.0.0.0" \
  -c "unix_socket_directories=/var/run/postgresql"
