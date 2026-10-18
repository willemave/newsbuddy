#!/usr/bin/env bash
set -euo pipefail

data_root=/data
export PORT="${PORT:-8000}"
export NEWSLY_RUNTIME_MODE="${NEWSLY_RUNTIME_MODE:-api}"

export MEDIA_BASE_DIR="${MEDIA_BASE_DIR:-${data_root}/media}"
export LOGS_BASE_DIR="${LOGS_BASE_DIR:-${data_root}/logs}"
export IMAGES_BASE_DIR="${IMAGES_BASE_DIR:-${data_root}/images}"
export CONTENT_BODY_LOCAL_ROOT="${CONTENT_BODY_LOCAL_ROOT:-${data_root}/content_bodies}"
export PODCAST_SCRATCH_DIR="${PODCAST_SCRATCH_DIR:-${data_root}/scratch}"
export PERSONAL_MARKDOWN_ROOT="${PERSONAL_MARKDOWN_ROOT:-${data_root}/personal_markdown}"

mkdir -p \
  "${data_root}" \
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
    export NEWSLY_BRIEFING_REFRESH_CONSUMERS="${NEWSLY_BRIEFING_REFRESH_CONSUMERS:-2}"
    case "${NEWSLY_BRIEFING_REFRESH_CONSUMERS}" in
      1|2) ;;
      *) echo "NEWSLY_BRIEFING_REFRESH_CONSUMERS must be 1 or 2" >&2; exit 2 ;;
    esac
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
