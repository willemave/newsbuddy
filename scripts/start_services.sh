#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ALEMBIC_CONFIG_PATH="${PROJECT_ROOT}/migrations/alembic.ini"

usage() {
  cat <<'EOF'
Usage: scripts/start_services.sh <command> [options]

Commands:
  all        Start the local long-running runtime: server, workers, watchdog, scheduler
  server     Start only the API server
  workers    Start only queue workers
  scrapers   Run scrapers once
  watchdog   Start the queue watchdog loop
  scheduler  Start the cron-style scheduler loop
  migrate    Run Alembic migrations

Common options:
  --env-file PATH   Load settings from PATH instead of .env
  -h, --help        Show this help

Examples:
  scripts/start_services.sh all --env-file .env
  scripts/start_services.sh server --port 8000 --reload
  scripts/start_services.sh workers --content-workers 2 --media-workers 1
  scripts/start_services.sh migrate --env-file .env
EOF
}

resolve_env_file() {
  if [[ -n "${NEWSLY_ENV_FILE:-}" && -f "${NEWSLY_ENV_FILE}" ]]; then
    printf '%s\n' "${NEWSLY_ENV_FILE}"
    return 0
  fi

  if [[ -f "${PROJECT_ROOT}/.env" ]]; then
    printf '%s\n' "${PROJECT_ROOT}/.env"
    return 0
  fi

  if [[ -f "${PROJECT_ROOT}/.env.docker.local" ]]; then
    printf '%s\n' "${PROJECT_ROOT}/.env.docker.local"
    return 0
  fi

  if [[ -f "${PROJECT_ROOT}/.env.docker" ]]; then
    printf '%s\n' "${PROJECT_ROOT}/.env.docker"
    return 0
  fi

  return 1
}

activate_runtime() {
  cd "${PROJECT_ROOT}"

  if [[ ! -f "${ENV_FILE}" ]]; then
    echo "ERROR: env file not found at ${ENV_FILE}" >&2
    exit 1
  fi

  export NEWSLY_ENV_FILE="${ENV_FILE}"

  if [[ -f "${PROJECT_ROOT}/.venv/bin/activate" ]]; then
    # shellcheck source=/dev/null
    source "${PROJECT_ROOT}/.venv/bin/activate"
  fi
}

dotenv_get() {
  local key="$1"
  local default_value="${2:-}"

  NEWSLY_ENV_FILE="${ENV_FILE}" KEY_NAME="${key}" DEFAULT_VALUE="${default_value}" python <<'PY'
import os

from dotenv import dotenv_values

env_file = os.environ["NEWSLY_ENV_FILE"]
key_name = os.environ["KEY_NAME"]
default_value = os.environ["DEFAULT_VALUE"]

if key_name in os.environ:
    print(os.environ[key_name])
    raise SystemExit(0)

values = dotenv_values(env_file)
value = values.get(key_name, default_value)
print("" if value is None else value)
PY
}

print_database_target() {
  PROJECT_ROOT="${PROJECT_ROOT}" python <<'PY'
from app.core.settings import get_settings

settings = get_settings()
print(settings.database_url)
PY
}

check_database_connection() {
  python -c "from app.core.db import init_db; init_db()"
}

run_migrations() {
  if [[ ! -f "${ALEMBIC_CONFIG_PATH}" ]]; then
    echo "ERROR: alembic config not found at ${ALEMBIC_CONFIG_PATH}" >&2
    exit 1
  fi

  echo "Running database migrations..."
  python -m alembic -c "${ALEMBIC_CONFIG_PATH}" upgrade head
}

ensure_playwright_chromium() {
  if [[ "${SKIP_BROWSER_INSTALL:-false}" == "true" ]]; then
    return 0
  fi

  echo "Ensuring Playwright Chromium is installed..."
  python -m playwright install chromium
}

start_server() {
  local debug_mode="false"
  local reload_mode=""
  local skip_migrate="false"
  local port_override=""

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --env-file)
        ENV_FILE="$2"
        shift 2
        ;;
      --debug)
        debug_mode="true"
        shift
        ;;
      --reload)
        reload_mode="true"
        shift
        ;;
      --no-reload)
        reload_mode="false"
        shift
        ;;
      --skip-migrate)
        skip_migrate="true"
        shift
        ;;
      --port)
        port_override="$2"
        shift 2
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        echo "Unknown server option: $1" >&2
        exit 1
        ;;
    esac
  done

  activate_runtime

  if [[ "${debug_mode}" == "true" ]]; then
    export LOG_LEVEL=DEBUG
  fi

  local database_target
  database_target="$(print_database_target)"
  echo "Database target: ${database_target}"

  if [[ "${skip_migrate}" != "true" ]]; then
    run_migrations
  fi

  local port="${port_override:-$(dotenv_get PORT 8000)}"
  local environment_name
  environment_name="$(dotenv_get ENVIRONMENT development)"
  local -a server_args=(
    python -m uvicorn app.main:app
    --host 0.0.0.0
    --port "${port}"
    --no-access-log
  )

  if [[ "${reload_mode}" == "true" || ( -z "${reload_mode}" && "${environment_name}" == "development" ) ]]; then
    server_args+=(--reload)
  fi

  if [[ "${debug_mode}" == "true" ]]; then
    server_args+=(--log-level debug)
  fi

  exec "${server_args[@]}"
}

start_workers() {
  local debug_enabled="false"
  local max_tasks=""
  local stats_interval="30"
  local content_workers=""
  local media_workers=""
  local onboarding_workers=""
  local twitter_workers=""
  local chat_workers=""

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --env-file)
        ENV_FILE="$2"
        shift 2
        ;;
      --debug)
        debug_enabled="true"
        shift
        ;;
      --max-tasks)
        max_tasks="$2"
        shift 2
        ;;
      --stats-interval)
        stats_interval="$2"
        shift 2
        ;;
      --content-workers)
        content_workers="$2"
        shift 2
        ;;
      --media-workers|--transcribe-workers)
        media_workers="$2"
        shift 2
        ;;
      --onboarding-workers)
        onboarding_workers="$2"
        shift 2
        ;;
      --twitter-workers)
        twitter_workers="$2"
        shift 2
        ;;
      --chat-workers)
        chat_workers="$2"
        shift 2
        ;;
      --no-stats)
        stats_interval="0"
        shift
        ;;
      --skip-browser-install)
        SKIP_BROWSER_INSTALL="true"
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        echo "Unknown workers option: $1" >&2
        exit 1
        ;;
    esac
  done

  activate_runtime

  content_workers="${content_workers:-$(dotenv_get CONTENT_WORKER_PROCS 2)}"
  media_workers="${media_workers:-$(dotenv_get MEDIA_WORKER_PROCS "$(dotenv_get TRANSCRIBE_WORKER_PROCS 1)")}"
  onboarding_workers="${onboarding_workers:-$(dotenv_get ONBOARDING_WORKER_PROCS 1)}"
  twitter_workers="${twitter_workers:-$(dotenv_get TWITTER_WORKER_PROCS 1)}"
  chat_workers="${chat_workers:-$(dotenv_get CHAT_WORKER_PROCS 1)}"

  local database_target
  database_target="$(print_database_target)"
  echo "Database target: ${database_target}"

  echo "Checking database connection..."
  check_database_connection

  ensure_playwright_chromium

  if ! [[ "${content_workers}" =~ ^[0-9]+$ ]] || \
     ! [[ "${media_workers}" =~ ^[0-9]+$ ]] || \
     ! [[ "${onboarding_workers}" =~ ^[0-9]+$ ]] || \
     ! [[ "${twitter_workers}" =~ ^[0-9]+$ ]] || \
     ! [[ "${chat_workers}" =~ ^[0-9]+$ ]]; then
    echo "ERROR: worker counts must be non-negative integers" >&2
    exit 1
  fi

  local total_workers=$((content_workers + media_workers + onboarding_workers + twitter_workers + chat_workers))
  if [[ "${total_workers}" -le 0 ]]; then
    echo "ERROR: at least one worker must be enabled" >&2
    exit 1
  fi

  local -a pids=()

  launch_worker_pool() {
    local queue="$1"
    local count="$2"
    local slot=1

    while [[ "${slot}" -le "${count}" ]]; do
      local -a cmd=(
        python scripts/run_workers.py
        --queue "${queue}"
        --worker-slot "${slot}"
        --stats-interval "${stats_interval}"
      )

      if [[ "${debug_enabled}" == "true" ]]; then
        cmd+=(--debug)
      fi
      if [[ -n "${max_tasks}" ]]; then
        cmd+=(--max-tasks "${max_tasks}")
      fi

      echo "Launching ${queue} worker ${slot}: ${cmd[*]}"
      "${cmd[@]}" &
      pids+=("$!")
      slot=$((slot + 1))
    done
  }

  trap 'for pid in "${pids[@]}"; do kill -TERM "$pid" 2>/dev/null || true; done; wait || true; exit 0' INT TERM

  launch_worker_pool content "${content_workers}"
  launch_worker_pool media "${media_workers}"
  launch_worker_pool onboarding "${onboarding_workers}"
  launch_worker_pool twitter "${twitter_workers}"
  launch_worker_pool chat "${chat_workers}"

  local exit_code=0
  for pid in "${pids[@]}"; do
    if ! wait "${pid}"; then
      exit_code=1
    fi
  done

  exit "${exit_code}"
}

start_scrapers() {
  local debug_flag=""
  local stats_flag=""
  local -a scraper_names=()

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --env-file)
        ENV_FILE="$2"
        shift 2
        ;;
      --debug)
        debug_flag="--debug"
        shift
        ;;
      --show-stats)
        stats_flag="--show-stats"
        shift
        ;;
      --scrapers)
        shift
        while [[ $# -gt 0 && ! "$1" =~ ^-- ]]; do
          scraper_names+=("$1")
          shift
        done
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        echo "Unknown scrapers option: $1" >&2
        exit 1
        ;;
    esac
  done

  activate_runtime

  local database_target
  database_target="$(print_database_target)"
  echo "Database target: ${database_target}"

  echo "Checking database connection..."
  check_database_connection

  local -a cmd=(python scripts/run_scrapers.py)
  if [[ -n "${debug_flag}" ]]; then
    cmd+=("${debug_flag}")
  fi
  if [[ -n "${stats_flag}" ]]; then
    cmd+=("${stats_flag}")
  fi
  if [[ "${#scraper_names[@]}" -gt 0 ]]; then
    cmd+=(--scrapers "${scraper_names[@]}")
  fi

  exec "${cmd[@]}"
}

start_watchdog() {
  local interval_seconds="300"

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --env-file)
        ENV_FILE="$2"
        shift 2
        ;;
      --interval-seconds)
        interval_seconds="$2"
        shift 2
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        echo "Unknown watchdog option: $1" >&2
        exit 1
        ;;
    esac
  done

  activate_runtime
  exec python scripts/watchdog_queue_recovery.py --loop --interval-seconds "${interval_seconds}"
}

start_scheduler() {
  local crontab_path="${PROJECT_ROOT}/docker/crontab"

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --env-file)
        ENV_FILE="$2"
        shift 2
        ;;
      --crontab)
        crontab_path="$2"
        shift 2
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        echo "Unknown scheduler option: $1" >&2
        exit 1
        ;;
    esac
  done

  activate_runtime
  exec python docker/supercronic.py "${crontab_path}"
}

start_all() {
  local debug_mode="false"
  local reload_mode=""
  local port_override=""
  local interval_seconds="300"
  local crontab_path="${PROJECT_ROOT}/docker/crontab"
  local content_workers=""
  local media_workers=""
  local onboarding_workers=""
  local twitter_workers=""
  local chat_workers=""
  local stats_interval="30"
  local max_tasks=""
  local skip_browser_install="false"

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --env-file)
        ENV_FILE="$2"
        shift 2
        ;;
      --debug)
        debug_mode="true"
        shift
        ;;
      --reload)
        reload_mode="true"
        shift
        ;;
      --no-reload)
        reload_mode="false"
        shift
        ;;
      --port)
        port_override="$2"
        shift 2
        ;;
      --interval-seconds)
        interval_seconds="$2"
        shift 2
        ;;
      --crontab)
        crontab_path="$2"
        shift 2
        ;;
      --content-workers)
        content_workers="$2"
        shift 2
        ;;
      --media-workers|--transcribe-workers)
        media_workers="$2"
        shift 2
        ;;
      --onboarding-workers)
        onboarding_workers="$2"
        shift 2
        ;;
      --twitter-workers)
        twitter_workers="$2"
        shift 2
        ;;
      --chat-workers)
        chat_workers="$2"
        shift 2
        ;;
      --stats-interval)
        stats_interval="$2"
        shift 2
        ;;
      --max-tasks)
        max_tasks="$2"
        shift 2
        ;;
      --skip-browser-install)
        skip_browser_install="true"
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        echo "Unknown all option: $1" >&2
        exit 1
        ;;
    esac
  done

  activate_runtime

  content_workers="${content_workers:-$(dotenv_get CONTENT_WORKER_PROCS 2)}"
  media_workers="${media_workers:-$(dotenv_get MEDIA_WORKER_PROCS "$(dotenv_get TRANSCRIBE_WORKER_PROCS 1)")}"
  onboarding_workers="${onboarding_workers:-$(dotenv_get ONBOARDING_WORKER_PROCS 1)}"
  twitter_workers="${twitter_workers:-$(dotenv_get TWITTER_WORKER_PROCS 1)}"
  chat_workers="${chat_workers:-$(dotenv_get CHAT_WORKER_PROCS 1)}"

  local database_target
  database_target="$(print_database_target)"
  echo "Database target: ${database_target}"

  echo "Checking database connection..."
  check_database_connection

  run_migrations

  local -a pids=()
  local -a names=()

  launch_service() {
    local name="$1"
    shift
    (
      "$@" 2>&1 | while IFS= read -r line; do
        printf '[%s] [%s] %s\n' "$(date '+%H:%M:%S')" "${name}" "${line}"
      done
    ) &
    pids+=("$!")
    names+=("${name}")
  }

  local -a server_cmd=("${SCRIPT_DIR}/start_services.sh" server --env-file "${ENV_FILE}" --skip-migrate)
  if [[ "${debug_mode}" == "true" ]]; then
    server_cmd+=(--debug)
  fi
  if [[ "${reload_mode}" == "true" ]]; then
    server_cmd+=(--reload)
  elif [[ "${reload_mode}" == "false" ]]; then
    server_cmd+=(--no-reload)
  fi
  if [[ -n "${port_override}" ]]; then
    server_cmd+=(--port "${port_override}")
  fi

  local -a workers_cmd=(
    "${SCRIPT_DIR}/start_services.sh" workers
    --env-file "${ENV_FILE}"
    --content-workers "${content_workers}"
    --media-workers "${media_workers}"
    --onboarding-workers "${onboarding_workers}"
    --twitter-workers "${twitter_workers}"
    --chat-workers "${chat_workers}"
    --stats-interval "${stats_interval}"
  )
  if [[ "${debug_mode}" == "true" ]]; then
    workers_cmd+=(--debug)
  fi
  if [[ -n "${max_tasks}" ]]; then
    workers_cmd+=(--max-tasks "${max_tasks}")
  fi
  if [[ "${skip_browser_install}" == "true" ]]; then
    workers_cmd+=(--skip-browser-install)
  fi

  local -a watchdog_cmd=(
    "${SCRIPT_DIR}/start_services.sh" watchdog
    --env-file "${ENV_FILE}"
    --interval-seconds "${interval_seconds}"
  )
  local -a scheduler_cmd=(
    "${SCRIPT_DIR}/start_services.sh" scheduler
    --env-file "${ENV_FILE}"
    --crontab "${crontab_path}"
  )

  launch_service server "${server_cmd[@]}"
  launch_service workers "${workers_cmd[@]}"
  launch_service watchdog "${watchdog_cmd[@]}"
  launch_service scheduler "${scheduler_cmd[@]}"

  cleanup_children() {
    for pid in "${pids[@]}"; do
      kill -TERM "${pid}" 2>/dev/null || true
    done
    wait || true
  }

  trap 'cleanup_children; exit 0' INT TERM

  while true; do
    local idx=0
    for pid in "${pids[@]}"; do
      if ! kill -0 "${pid}" 2>/dev/null; then
        local name="${names[$idx]}"
        local exit_code=0
        if ! wait "${pid}"; then
          exit_code=$?
        fi
        echo "Service exited: ${name} (exit=${exit_code})" >&2
        cleanup_children
        exit "${exit_code}"
      fi
      idx=$((idx + 1))
    done
    sleep 2
  done
}

COMMAND="${1:-}"
if [[ -z "${COMMAND}" || "${COMMAND}" == "-h" || "${COMMAND}" == "--help" ]]; then
  usage
  exit 0
fi
shift

if ! ENV_FILE="$(resolve_env_file)"; then
  echo "ERROR: no env file found. Create .env for native local Postgres, .env.docker.local for Docker, or pass --env-file PATH." >&2
  exit 1
fi

case "${COMMAND}" in
  all)
    start_all "$@"
    ;;
  server)
    start_server "$@"
    ;;
  workers)
    start_workers "$@"
    ;;
  scrapers)
    start_scrapers "$@"
    ;;
  watchdog)
    start_watchdog "$@"
    ;;
  scheduler)
    start_scheduler "$@"
    ;;
  migrate)
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --env-file)
          ENV_FILE="$2"
          shift 2
          ;;
        -h|--help)
          usage
          exit 0
          ;;
        *)
          echo "Unknown migrate option: $1" >&2
          exit 1
          ;;
      esac
    done
    activate_runtime
    echo "Database target: $(print_database_target)"
    run_migrations
    ;;
  *)
    echo "Unknown command: ${COMMAND}" >&2
    usage
    exit 1
    ;;
esac
