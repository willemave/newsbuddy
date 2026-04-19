#!/usr/bin/env bash
set -euo pipefail

export APP_HOME="${APP_HOME:-/app}"
export NEWSLY_DATA_ROOT="${NEWSLY_DATA_ROOT:-/data}"
export NEWSLY_APP_DATA_ROOT="${NEWSLY_APP_DATA_ROOT:-${NEWSLY_DATA_ROOT}}"
export PGDATA="${PGDATA:-${NEWSLY_DATA_ROOT}/postgres}"
export POSTGRES_DB="${POSTGRES_DB:-newsly}"
export POSTGRES_USER="${POSTGRES_USER:-newsly}"
export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-newsly}"
export POSTGRES_PORT="${POSTGRES_PORT:-5432}"
export PORT="${PORT:-8000}"
export NEWSLY_BOOTSTRAP_READY_FILE="${NEWSLY_BOOTSTRAP_READY_FILE:-/tmp/newsly-bootstrap.ready}"

mkdir -p "${PGDATA}" "${NEWSLY_APP_DATA_ROOT}" /var/run/postgresql
chown -R postgres:postgres "${PGDATA}" /var/run/postgresql
chmod 700 "${PGDATA}"

export MEDIA_BASE_DIR="${MEDIA_BASE_DIR:-${NEWSLY_APP_DATA_ROOT}/media}"
export LOGS_BASE_DIR="${LOGS_BASE_DIR:-${NEWSLY_APP_DATA_ROOT}/logs}"
export IMAGES_BASE_DIR="${IMAGES_BASE_DIR:-${NEWSLY_APP_DATA_ROOT}/images}"
export CONTENT_BODY_LOCAL_ROOT="${CONTENT_BODY_LOCAL_ROOT:-${NEWSLY_APP_DATA_ROOT}/content_bodies}"
export PODCAST_SCRATCH_DIR="${PODCAST_SCRATCH_DIR:-${NEWSLY_APP_DATA_ROOT}/scratch}"
export PERSONAL_MARKDOWN_ROOT="${PERSONAL_MARKDOWN_ROOT:-${NEWSLY_APP_DATA_ROOT}/personal_markdown}"
export NEWSLY_RUNTIME_MODE="${NEWSLY_RUNTIME_MODE:-full}"
export DATABASE_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@127.0.0.1:${POSTGRES_PORT}/${POSTGRES_DB}"

mkdir -p \
  "${MEDIA_BASE_DIR}" \
  "${LOGS_BASE_DIR}" \
  "${IMAGES_BASE_DIR}" \
  "${CONTENT_BODY_LOCAL_ROOT}" \
  "${PODCAST_SCRATCH_DIR}" \
  "${PERSONAL_MARKDOWN_ROOT}"

postgres_bin_dir="$(dirname "$(find /usr/lib/postgresql -path '*/bin/postgres' | sort -V | tail -n 1)")"
if [[ -z "${postgres_bin_dir:-}" || ! -x "${postgres_bin_dir}/initdb" ]]; then
  echo "postgres binaries not found" >&2
  exit 1
fi

if [[ ! -s "${PGDATA}/PG_VERSION" ]]; then
  runuser -u postgres -- "${postgres_bin_dir}/initdb" \
    -D "${PGDATA}" \
    --encoding=UTF8 \
    --locale=C.UTF-8 \
    --auth-local=trust \
    --auth-host=scram-sha-256
fi

if ! grep -q "listen_addresses = '0.0.0.0'" "${PGDATA}/postgresql.conf"; then
  cat >>"${PGDATA}/postgresql.conf" <<EOF
listen_addresses = '0.0.0.0'
port = ${POSTGRES_PORT}
unix_socket_directories = '/var/run/postgresql'
EOF
fi

if ! grep -q "0.0.0.0/0" "${PGDATA}/pg_hba.conf"; then
  cat >>"${PGDATA}/pg_hba.conf" <<EOF
host all all 0.0.0.0/0 scram-sha-256
host all all ::/0 scram-sha-256
EOF
fi

rm -f "${NEWSLY_BOOTSTRAP_READY_FILE}"

supervisord_conf="/app/docker/supervisord.conf"
case "${NEWSLY_RUNTIME_MODE}" in
  full)
    supervisord_conf="/app/docker/supervisord.conf"
    ;;
  server)
    supervisord_conf="/app/docker/supervisord.server.conf"
    ;;
  *)
    echo "unsupported NEWSLY_RUNTIME_MODE: ${NEWSLY_RUNTIME_MODE}" >&2
    exit 1
    ;;
esac

exec /usr/bin/supervisord -c "${supervisord_conf}"
