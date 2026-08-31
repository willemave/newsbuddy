#!/usr/bin/env bash
set -euo pipefail

export APP_HOME="${APP_HOME:-/app}"
export NEWSLY_DATA_ROOT="${NEWSLY_DATA_ROOT:-/data}"
export NEWSLY_APP_DATA_ROOT="${NEWSLY_APP_DATA_ROOT:-${NEWSLY_DATA_ROOT}}"
export PORT="${PORT:-8000}"
export NEWSLY_RUNTIME_MODE="${NEWSLY_RUNTIME_MODE:-api}"

export MEDIA_BASE_DIR="${MEDIA_BASE_DIR:-${NEWSLY_APP_DATA_ROOT}/media}"
export LOGS_BASE_DIR="${LOGS_BASE_DIR:-${NEWSLY_APP_DATA_ROOT}/logs}"
export IMAGES_BASE_DIR="${IMAGES_BASE_DIR:-${NEWSLY_APP_DATA_ROOT}/images}"
export CONTENT_BODY_LOCAL_ROOT="${CONTENT_BODY_LOCAL_ROOT:-${NEWSLY_APP_DATA_ROOT}/content_bodies}"
export PODCAST_SCRATCH_DIR="${PODCAST_SCRATCH_DIR:-${NEWSLY_APP_DATA_ROOT}/scratch}"
export PERSONAL_MARKDOWN_ROOT="${PERSONAL_MARKDOWN_ROOT:-${NEWSLY_APP_DATA_ROOT}/personal_markdown}"

mkdir -p \
  "${NEWSLY_APP_DATA_ROOT}" \
  "${MEDIA_BASE_DIR}" \
  "${LOGS_BASE_DIR}" \
  "${IMAGES_BASE_DIR}" \
  "${CONTENT_BODY_LOCAL_ROOT}" \
  "${PODCAST_SCRATCH_DIR}" \
  "${PERSONAL_MARKDOWN_ROOT}"

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "DATABASE_URL is required for NEWSLY_RUNTIME_MODE=${NEWSLY_RUNTIME_MODE}" >&2
  exit 1
fi

case "${NEWSLY_RUNTIME_MODE}" in
  api)
    export NEWSLY_RUST_BIND_ADDR="${NEWSLY_RUST_BIND_ADDR:-0.0.0.0:${PORT}}"
    exec /usr/local/bin/newsly-api
    ;;
  workers)
    exec /usr/bin/supervisord -c /app/docker/supervisord.workers.conf
    ;;
  scheduler)
    exec /usr/local/bin/newsly-scheduler
    ;;
  migrate)
    if [[ "${NEWSLY_SQLX_BASELINE_ADOPTION:-false}" == "true" ]]; then
      if [[ "${NEWSLY_MAINTENANCE_BARRIER_CONFIRMED:-false}" != "true" ]]; then
        echo "SQLx baseline adoption requires a confirmed maintenance barrier" >&2
        exit 1
      fi
      exec /usr/local/bin/newsly-db baseline --maintenance-barrier-confirmed
    fi
    if [[ "${NEWSLY_MAINTENANCE_BARRIER_CONFIRMED:-false}" == "true" ]]; then
      exec /usr/local/bin/newsly-db migrate --maintenance-barrier-confirmed
    fi
    exec /usr/local/bin/newsly-db migrate
    ;;
  *)
    echo "unsupported NEWSLY_RUNTIME_MODE: ${NEWSLY_RUNTIME_MODE}" >&2
    exit 1
    ;;
esac
