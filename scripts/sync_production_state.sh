#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd "${script_dir}/.." && pwd)"

remote_host="${REMOTE_HOST:-news-app-server}"
remote_container="${REMOTE_CONTAINER:-newsly-workers}"
remote_data_root="/data"
env_file="${NEWSLY_ENV_FILE:-${repository_root}/.env}"
database_url="${DATABASE_URL:-}"
target_db=""
dump_path=""
reuse_dump="false"
skip_db="false"
skip_assets="false"
asset_days=30
no_force="false"
restart_server="true"
server_port=8000
screen_name="newsly-local-api"
asset_dirs=()

usage() {
  cat <<'EOF'
Usage: scripts/sync_production_state.sh [options]

Overwrite the local PostgreSQL database with a production dump, copy recent
file-backed assets, and restart only the local Rust API. Workers are never
started by this command.

Options:
  --remote-host HOST       SSH host (default: news-app-server)
  --remote-container NAME  Production container used for dump/assets
  --remote-data-root PATH  Production data root (default: /data)
  --env-file PATH          Local env file to read and update (default: .env)
  --target-db NAME         Local database name override
  --dump-path PATH         Dump path override
  --reuse-dump             Restore an existing dump instead of pulling one
  --skip-db                Sync assets only
  --skip-assets            Restore the database only
  --asset-days N           Recent asset window (default: 30)
  --asset-dir NAME         Repeat for images, media, content_bodies, personal_markdown
  --no-force               Refuse to replace an existing local database
  --no-restart-server      Leave the local API stopped
  --server-port PORT       Rust API port (default: 8000)
  --screen-name NAME       screen session name (default: newsly-local-api)
  -h, --help               Show this help
EOF
}

require_value() {
  if [[ $# -lt 2 || -z "${2:-}" || "${2:-}" == -* ]]; then
    echo "option $1 requires a value" >&2
    exit 2
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --remote-host) require_value "$@"; remote_host="$2"; shift 2 ;;
    --remote-container) require_value "$@"; remote_container="$2"; shift 2 ;;
    --remote-data-root) require_value "$@"; remote_data_root="$2"; shift 2 ;;
    --env-file) require_value "$@"; env_file="$2"; shift 2 ;;
    --target-db) require_value "$@"; target_db="$2"; shift 2 ;;
    --dump-path) require_value "$@"; dump_path="$2"; shift 2 ;;
    --reuse-dump) reuse_dump="true"; shift ;;
    --skip-db) skip_db="true"; shift ;;
    --skip-assets) skip_assets="true"; shift ;;
    --asset-days) require_value "$@"; asset_days="$2"; shift 2 ;;
    --asset-dir) require_value "$@"; asset_dirs+=("$2"); shift 2 ;;
    --no-force) no_force="true"; shift ;;
    --no-restart-server) restart_server="false"; shift ;;
    --server-port) require_value "$@"; server_port="$2"; shift 2 ;;
    --screen-name) require_value "$@"; screen_name="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "${skip_db}" == "true" && "${skip_assets}" == "true" ]]; then
  echo "--skip-db and --skip-assets cannot both be set" >&2
  exit 2
fi
if [[ ! "${asset_days}" =~ ^[1-9][0-9]*$ ]]; then
  echo "--asset-days must be a positive integer" >&2
  exit 2
fi
if [[ ! "${server_port}" =~ ^[1-9][0-9]*$ || "${server_port}" -gt 65535 ]]; then
  echo "--server-port must be between 1 and 65535" >&2
  exit 2
fi

for command_name in ssh psql pg_restore createdb dropdb tar lsof screen curl; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "required command not found: ${command_name}" >&2
    exit 1
  fi
done

read_env_value() {
  local key="$1"
  [[ -f "${env_file}" ]] || return 0
  sed -n "s/^${key}=//p" "${env_file}" | tail -n 1 | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
}

if [[ -z "${database_url}" ]]; then
  database_url="$(read_env_value DATABASE_URL)"
fi
if [[ -z "${database_url}" ]]; then
  database_url="postgresql://newsly:root@127.0.0.1:5432/newsly"
fi
database_url="${database_url/postgresql+psycopg:\/\//postgresql://}"
database_url="${database_url/postgresql+asyncpg:\/\//postgresql://}"

database_url_without_query="${database_url%%\?*}"
database_query=""
if [[ "${database_url}" == *\?* ]]; then
  database_query="?${database_url#*\?}"
fi
database_prefix="${database_url_without_query%/*}"
database_name_from_url="${database_url_without_query##*/}"
if [[ -z "${target_db}" ]]; then
  target_db="${database_name_from_url:-newsly}"
fi
if [[ ! "${target_db}" =~ ^[A-Za-z_][A-Za-z0-9_-]*$ ]]; then
  echo "unsupported local database name: ${target_db}" >&2
  exit 2
fi
target_database_url="${database_prefix}/${target_db}${database_query}"
admin_database_url="${database_prefix}/postgres${database_query}"

if [[ -z "${dump_path}" ]]; then
  dump_directory="${repository_root}/.local_dumps"
  if [[ "${reuse_dump}" == "true" ]]; then
    dump_path="$(find "${dump_directory}" -maxdepth 1 -type f -name 'newsly_prod_*.dump' -print 2>/dev/null | sort | tail -n 1)"
  else
    dump_path="${dump_directory}/newsly_prod_$(date -u +%Y%m%dT%H%M%SZ).dump"
  fi
fi

remote_exec() {
  local remote_script="$1"
  local quoted_container quoted_script
  printf -v quoted_container '%q' "${remote_container}"
  printf -v quoted_script '%q' "${remote_script}"
  ssh "${remote_host}" \
    "sudo docker exec ${quoted_container} bash -lc ${quoted_script}"
}

stop_local_api() {
  screen -S "${screen_name}" -X quit >/dev/null 2>&1 || true
  local pid command_line unsafe="false"
  while IFS= read -r pid; do
    [[ -n "${pid}" ]] || continue
    command_line="$(ps -p "${pid}" -o command= 2>/dev/null || true)"
    if [[ "${command_line}" != *"${repository_root}"* || \
          ( "${command_line}" != *"newsly-api"* && "${command_line}" != *"start_services.sh"* ) ]]; then
      echo "port ${server_port} is occupied by a non-Newsly process: ${pid}: ${command_line}" >&2
      unsafe="true"
      continue
    fi
    kill -TERM "${pid}" 2>/dev/null || true
  done < <(lsof -tiTCP:"${server_port}" -sTCP:LISTEN 2>/dev/null || true)
  [[ "${unsafe}" == "false" ]] || exit 1
  for _ in {1..40}; do
    if ! lsof -tiTCP:"${server_port}" -sTCP:LISTEN >/dev/null 2>&1; then
      return
    fi
    sleep 0.25
  done
  echo "local Rust API did not release port ${server_port}" >&2
  exit 1
}

restart_local_api() {
  mkdir -p "${repository_root}/logs"
  local log_path="${repository_root}/logs/local-api.log"
  local quoted_root quoted_env quoted_log
  printf -v quoted_root '%q' "${repository_root}"
  printf -v quoted_env '%q' "${env_file}"
  printf -v quoted_log '%q' "${log_path}"
  screen -dmS "${screen_name}" /bin/zsh -lc \
    "cd ${quoted_root} && exec scripts/start_services.sh server --env-file ${quoted_env} --port ${server_port} >${quoted_log} 2>&1"
  for _ in {1..60}; do
    if curl -fsS "http://127.0.0.1:${server_port}/health" >/dev/null 2>&1; then
      echo "Local Rust API is healthy at http://127.0.0.1:${server_port}"
      return
    fi
    sleep 1
  done
  echo "WARNING: local Rust API did not become healthy; inspect ${log_path}" >&2
}

write_database_url() {
  mkdir -p "$(dirname "${env_file}")"
  touch "${env_file}"
  local temporary_file
  temporary_file="$(mktemp "${env_file}.XXXXXX")"
  awk -v value="${target_database_url}" '
    BEGIN { written = 0 }
    /^DATABASE_URL=/ { if (!written) { print "DATABASE_URL=" value; written = 1 }; next }
    { print }
    END { if (!written) print "DATABASE_URL=" value }
  ' "${env_file}" >"${temporary_file}"
  mv "${temporary_file}" "${env_file}"
}

restore_database() {
  if [[ "${reuse_dump}" == "true" ]]; then
    if [[ -z "${dump_path}" || ! -f "${dump_path}" ]]; then
      echo "--reuse-dump requires an existing --dump-path or prior dump" >&2
      exit 1
    fi
    echo "Reusing production dump: ${dump_path}"
  else
    mkdir -p "$(dirname "${dump_path}")"
    echo "Pulling production PostgreSQL dump to ${dump_path}"
    remote_exec 'set -euo pipefail; PGPASSWORD="${POSTGRES_PASSWORD:?}" pg_dump --format=custom --compress=6 --no-owner --no-privileges -h 127.0.0.1 -U "${POSTGRES_USER:?}" -d "${POSTGRES_DB:?}"' >"${dump_path}"
  fi

  local existing
  existing="$(psql "${admin_database_url}" -X -At -v ON_ERROR_STOP=1 -v target_db="${target_db}" \
    -c "SELECT 1 FROM pg_database WHERE datname = :'target_db'" | tr -d '[:space:]')"
  if [[ -n "${existing}" && "${no_force}" == "true" ]]; then
    echo "database ${target_db} exists; omit --no-force to replace it" >&2
    exit 1
  fi
  if [[ -n "${existing}" ]]; then
    psql "${admin_database_url}" -X -v ON_ERROR_STOP=1 -v target_db="${target_db}" \
      -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :'target_db' AND pid <> pg_backend_pid()" >/dev/null
    dropdb --maintenance-db="${admin_database_url}" --if-exists --force "${target_db}"
  fi
  createdb --maintenance-db="${admin_database_url}" "${target_db}"
  pg_restore --no-owner --no-privileges --jobs=4 --dbname="${target_database_url}" "${dump_path}"
  write_database_url
  echo "Restored production PostgreSQL into ${target_db}; updated ${env_file}"
  psql "${target_database_url}" -X -At -v ON_ERROR_STOP=1 \
    -c "SELECT 'users=' || count(*) FROM users UNION ALL SELECT 'contents=' || count(*) FROM contents"
}

asset_destination() {
  local name="$1" key fallback configured
  case "${name}" in
    images) key="IMAGES_BASE_DIR"; fallback="${repository_root}/data/images" ;;
    media) key="MEDIA_BASE_DIR"; fallback="${repository_root}/data/media" ;;
    content_bodies) key="CONTENT_BODY_LOCAL_ROOT"; fallback="${repository_root}/data/content_bodies" ;;
    personal_markdown) key="PERSONAL_MARKDOWN_ROOT"; fallback="${repository_root}/data/personal_markdown" ;;
    *) echo "unsupported asset directory: ${name}" >&2; exit 2 ;;
  esac
  configured="$(read_env_value "${key}")"
  if [[ -z "${configured}" ]]; then
    printf '%s\n' "${fallback}"
  elif [[ "${configured}" == /data/* ]]; then
    printf '%s\n' "${repository_root}/data/${configured#/data/}"
  elif [[ "${configured}" == /* ]]; then
    printf '%s\n' "${configured}"
  else
    printf '%s\n' "${repository_root}/${configured#./}"
  fi
}

sync_assets() {
  if [[ ${#asset_dirs[@]} -eq 0 ]]; then
    asset_dirs=(images media content_bodies personal_markdown)
  fi
  local name remote_path local_path remote_script
  for name in "${asset_dirs[@]}"; do
    local_path="$(asset_destination "${name}")"
    remote_path="${remote_data_root%/}/${name}"
    mkdir -p "${local_path}"
    printf -v remote_script \
      'set -euo pipefail; path=%q; days=%q; if [[ -d "$path" ]]; then cd "$path"; find . -type f -mtime "-$days" -print0 | tar --null --files-from=- -cf -; else tar -cf - --files-from /dev/null; fi' \
      "${remote_path}" "${asset_days}"
    echo "Syncing recent ${name} assets (${asset_days} days)"
    remote_exec "${remote_script}" | tar -xf - -C "${local_path}"
  done
}

if [[ "${restart_server}" == "true" ]]; then
  stop_local_api
fi
if [[ "${skip_db}" != "true" ]]; then
  restore_database
fi
if [[ "${skip_assets}" != "true" ]]; then
  sync_assets
fi
if [[ "${restart_server}" == "true" ]]; then
  restart_local_api
fi

echo "Production state sync complete."
