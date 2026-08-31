#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd "${script_dir}/.." && pwd)"
rust_manifest="${repository_root}/rust/Cargo.toml"
rust_target="${repository_root}/rust/target/debug"
# shellcheck source=scripts/lib/rust_runtime.sh
source "${script_dir}/lib/rust_runtime.sh"
extractor_binary="${repository_root}/python/document_extractor/.venv/bin/newsly-document-extractor"

usage() {
  cat <<'EOF'
Usage: scripts/start_services.sh <command> [options]

Commands:
  all        Start the Rust API, every native worker, the scheduler, and Crawl4AI extractor
  server     Start only the Rust API
  workers    Start every native Rust queue worker
  scheduler  Start the native Rust scheduler (including queue recovery and scraper fan-out)
  extractor  Start only the isolated Python Crawl4AI document extractor
  migrate    Apply SQLx migrations with newsly-db

Options:
  --env-file PATH  Load process settings from PATH instead of the first local .env candidate
  --port PORT      API bind port (server/all; default 8000)
  --local-e2e      Force bounded SQLx pools for a complete local end-to-end stack
  --skip-migrate   Do not apply SQLx migrations before starting the API
  --debug          Use debug-level, human-readable Rust logs
  -h, --help       Show this help

Python is deliberately limited to the database-free document extractor. Model and embedding
evaluation pipelines live under python/evals and are not part of the application runtime.
The local launcher gives each process a conservative PostgreSQL pool by default. Explicit
NEWSLY_*_DATABASE_MAX_CONNECTIONS values take precedence unless --local-e2e is selected.
EOF
}

load_environment() {
  local env_file="$1"
  newsly_load_dotenv "${env_file}"
  newsly_normalize_database_environment

  export ENVIRONMENT="${ENVIRONMENT:-development}"
  export NEWSLY_RUST_LOG_FORMAT="${NEWSLY_RUST_LOG_FORMAT:-pretty}"
  export DOCUMENT_EXTRACTOR_SHARED_SECRET="${DOCUMENT_EXTRACTOR_SHARED_SECRET:-local-extractor-secret}"
  export NEWSLY_DOCUMENT_EXTRACTOR_SHARED_SECRET="${NEWSLY_DOCUMENT_EXTRACTOR_SHARED_SECRET:-${DOCUMENT_EXTRACTOR_SHARED_SECRET}}"
  export NEWSLY_DOCUMENT_EXTRACTOR_URL="${NEWSLY_DOCUMENT_EXTRACTOR_URL:-${DOCUMENT_EXTRACTOR_URL:-http://127.0.0.1:8200}}"

  # A full native stack has one PostgreSQL listener per queue process in addition
  # to its SQLx pool. Production sets capacity explicitly; the local launcher
  # defaults to one pooled connection per background process and two for the API.
  if [[ "${local_e2e}" == "true" ]]; then
    export NEWSLY_RUST_DATABASE_MAX_CONNECTIONS=2
    export NEWSLY_RUST_DATABASE_MIN_CONNECTIONS=0
    export NEWSLY_RUST_WORKER_DATABASE_MAX_CONNECTIONS=1
    export NEWSLY_RUST_WORKER_DATABASE_MIN_CONNECTIONS=0
    export NEWSLY_NEWS_ITEM_DATABASE_MAX_CONNECTIONS=1
    export NEWSLY_NEWS_ITEM_DATABASE_MIN_CONNECTIONS=0
    export NEWSLY_SUMMARIZATION_DATABASE_MAX_CONNECTIONS=1
    export NEWSLY_SUMMARIZATION_DATABASE_MIN_CONNECTIONS=0
    export NEWSLY_DISCUSSION_DATABASE_MAX_CONNECTIONS=1
    export NEWSLY_DISCUSSION_DATABASE_MIN_CONNECTIONS=0
    export NEWSLY_IMAGE_DATABASE_MAX_CONNECTIONS=1
    export NEWSLY_IMAGE_DATABASE_MIN_CONNECTIONS=0
    export NEWSLY_AUDIO_DATABASE_MAX_CONNECTIONS=1
    export NEWSLY_AUDIO_DATABASE_MIN_CONNECTIONS=0
    export NEWSLY_X_SYNC_DATABASE_MAX_CONNECTIONS=1
    export NEWSLY_X_SYNC_DATABASE_MIN_CONNECTIONS=0
    export NEWSLY_MEDIA_DATABASE_MAX_CONNECTIONS=1
    export NEWSLY_MEDIA_DATABASE_MIN_CONNECTIONS=0
    export NEWSLY_AGENT_DATA_DATABASE_MAX_CONNECTIONS=1
    export NEWSLY_AGENT_DATA_DATABASE_MIN_CONNECTIONS=0
    export NEWSLY_SCHEDULER_DATABASE_MAX_CONNECTIONS=1
    export NEWSLY_SCHEDULER_DATABASE_MIN_CONNECTIONS=0
    export NEWSLY_DELETE_WORKER_DATABASE_MAX_CONNECTIONS=1
    export NEWSLY_DELETE_WORKER_DATABASE_MIN_CONNECTIONS=0
  else
    export NEWSLY_RUST_DATABASE_MAX_CONNECTIONS="${NEWSLY_RUST_DATABASE_MAX_CONNECTIONS:-2}"
    export NEWSLY_RUST_WORKER_DATABASE_MAX_CONNECTIONS="${NEWSLY_RUST_WORKER_DATABASE_MAX_CONNECTIONS:-1}"
    export NEWSLY_NEWS_ITEM_DATABASE_MAX_CONNECTIONS="${NEWSLY_NEWS_ITEM_DATABASE_MAX_CONNECTIONS:-1}"
    export NEWSLY_SUMMARIZATION_DATABASE_MAX_CONNECTIONS="${NEWSLY_SUMMARIZATION_DATABASE_MAX_CONNECTIONS:-1}"
    export NEWSLY_DISCUSSION_DATABASE_MAX_CONNECTIONS="${NEWSLY_DISCUSSION_DATABASE_MAX_CONNECTIONS:-1}"
    export NEWSLY_IMAGE_DATABASE_MAX_CONNECTIONS="${NEWSLY_IMAGE_DATABASE_MAX_CONNECTIONS:-1}"
    export NEWSLY_AUDIO_DATABASE_MAX_CONNECTIONS="${NEWSLY_AUDIO_DATABASE_MAX_CONNECTIONS:-1}"
    export NEWSLY_X_SYNC_DATABASE_MAX_CONNECTIONS="${NEWSLY_X_SYNC_DATABASE_MAX_CONNECTIONS:-1}"
    export NEWSLY_MEDIA_DATABASE_MAX_CONNECTIONS="${NEWSLY_MEDIA_DATABASE_MAX_CONNECTIONS:-1}"
    export NEWSLY_AGENT_DATA_DATABASE_MAX_CONNECTIONS="${NEWSLY_AGENT_DATA_DATABASE_MAX_CONNECTIONS:-1}"
    export NEWSLY_SCHEDULER_DATABASE_MAX_CONNECTIONS="${NEWSLY_SCHEDULER_DATABASE_MAX_CONNECTIONS:-1}"
    export NEWSLY_DELETE_WORKER_DATABASE_MAX_CONNECTIONS="${NEWSLY_DELETE_WORKER_DATABASE_MAX_CONNECTIONS:-1}"
  fi
}

build_packages() {
  cargo build --manifest-path "${rust_manifest}" --locked "$@"
}

run_binary() {
  local binary="$1"
  shift
  exec "${rust_target}/${binary}" "$@"
}

run_migrations() {
  "${script_dir}/run_sqlx_migrations.sh"
}

prepare_extractor() {
  if ! command -v uv >/dev/null 2>&1; then
    echo "uv is required to start the isolated Crawl4AI extractor" >&2
    exit 1
  fi
  uv sync \
    --project "${repository_root}/python/document_extractor" \
    --frozen >/dev/null
  if [[ ! -x "${extractor_binary}" ]]; then
    echo "document extractor entrypoint is missing after uv sync: ${extractor_binary}" >&2
    exit 1
  fi
}

start_extractor() {
  prepare_extractor
  exec env -u DATABASE_URL -u NEWSLY_DATABASE_URL "${extractor_binary}"
}

worker_binaries=(
  newsly-worker
  media_worker
  audio_episode_worker
  image_worker
  discussion_worker
  news_item_worker
  scrape_worker
  summarization_worker
  x_sync_worker
  agent_data_worker
  feed_backfill_worker
  feed_discovery_worker
  onboarding_discovery_worker
  briefing_refresh_worker
  chat_worker
  run_llm_task_worker
  newsly-account-deletion-worker
)

supervise_children() {
  local label="$1"
  shift
  local -a child_pids=("$@")
  local shutdown_status=0

  terminate_children() {
    local pid
    for pid in "${child_pids[@]}"; do
      kill -TERM "${pid}" 2>/dev/null || true
    done
  }
  request_shutdown() {
    shutdown_status="$1"
    trap - INT TERM
    terminate_children
  }
  trap 'request_shutdown 130' INT
  trap 'request_shutdown 143' TERM

  local running_pids pid exited_pid child_status other_pid
  while [[ "${shutdown_status}" -eq 0 ]]; do
    running_pids=" $(jobs -pr | tr '\n' ' ') "
    if [[ "${shutdown_status}" -ne 0 ]]; then
      break
    fi
    exited_pid=""
    for pid in "${child_pids[@]}"; do
      if [[ "${running_pids}" != *" ${pid} "* ]]; then
        exited_pid="${pid}"
        break
      fi
    done
    if [[ -z "${exited_pid}" ]]; then
      sleep 0.25
      continue
    fi

    child_status=0
    wait "${exited_pid}" || child_status=$?
    if [[ "${shutdown_status}" -ne 0 ]]; then
      break
    fi
    if [[ "${child_status}" -eq 0 ]]; then
      child_status=1
    fi
    echo "${label} child ${exited_pid} exited unexpectedly (status ${child_status}); stopping peers" >&2
    terminate_children
    for other_pid in "${child_pids[@]}"; do
      [[ "${other_pid}" == "${exited_pid}" ]] && continue
      wait "${other_pid}" 2>/dev/null || true
    done
    trap - INT TERM
    return "${child_status}"
  done

  for pid in "${child_pids[@]}"; do
    wait "${pid}" 2>/dev/null || true
  done
  trap - INT TERM
  return "${shutdown_status}"
}

start_worker_group() {
  newsly_require_database_url
  build_packages \
    --package newsly-worker --bins \
    --package newsly-account-deletion-worker --bin newsly-account-deletion-worker

  local -a worker_pids=()
  local binary
  for binary in "${worker_binaries[@]}"; do
    echo "starting native worker: ${binary}"
    "${rust_target}/${binary}" &
    worker_pids+=("$!")
  done

  supervise_children "worker group" "${worker_pids[@]}"
}

start_all() {
  newsly_require_database_url
  if [[ "${skip_migrate}" != "true" ]]; then
    run_migrations
  fi
  build_packages \
    --package newsly-api --bin newsly-api \
    --package newsly-scheduler --bin newsly-scheduler \
    --package newsly-worker --bins \
    --package newsly-account-deletion-worker --bin newsly-account-deletion-worker

  prepare_extractor

  local -a service_pids=()
  env -u DATABASE_URL -u NEWSLY_DATABASE_URL "${extractor_binary}" &
  service_pids+=("$!")

  "${rust_target}/newsly-api" &
  service_pids+=("$!")

  "${rust_target}/newsly-scheduler" &
  service_pids+=("$!")

  local binary
  for binary in "${worker_binaries[@]}"; do
    "${rust_target}/${binary}" &
    service_pids+=("$!")
  done

  supervise_children "runtime" "${service_pids[@]}"
}

if [[ $# -eq 0 ]]; then
  usage
  exit 1
fi

command_name="$1"
shift
if [[ "${command_name}" == "-h" || "${command_name}" == "--help" ]]; then
  usage
  exit 0
fi
env_file_option=""
port="8000"
skip_migrate="false"
debug="false"
local_e2e="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file)
      env_file_option="$2"
      shift 2
      ;;
    --port)
      port="$2"
      shift 2
      ;;
    --skip-migrate)
      skip_migrate="true"
      shift
      ;;
    --local-e2e)
      local_e2e="true"
      shift
      ;;
    --debug)
      debug="true"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

env_file="$(newsly_resolve_env_file "${repository_root}" "${env_file_option}")"
load_environment "${env_file}"
if [[ "${debug}" == "true" ]]; then
  export NEWSLY_RUST_LOG_FORMAT=pretty
  export RUST_LOG="${RUST_LOG:-newsly_api=debug,newsly_worker=debug,newsly_queue=debug,newsly_scheduler=debug}"
fi
export NEWSLY_RUST_BIND_ADDR="0.0.0.0:${port}"
if [[ ! "${port}" =~ ^[1-9][0-9]*$ || "${port}" -gt 65535 ]]; then
  echo "--port must be between 1 and 65535" >&2
  exit 2
fi

cd "${repository_root}"
case "${command_name}" in
  all)
    start_all
    ;;
  server)
    newsly_require_database_url
    if [[ "${skip_migrate}" != "true" ]]; then
      run_migrations
    fi
    build_packages --package newsly-api --bin newsly-api
    run_binary newsly-api
    ;;
  workers)
    start_worker_group
    ;;
  scheduler)
    newsly_require_database_url
    build_packages --package newsly-scheduler --bin newsly-scheduler
    run_binary newsly-scheduler
    ;;
  extractor)
    start_extractor
    ;;
  migrate)
    run_migrations
    ;;
  *)
    echo "unknown command: ${command_name}" >&2
    usage >&2
    exit 1
    ;;
esac
