#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

ENV_FILE="${NEWSLY_ENV_FILE:-}"
BACKUP_DIR="${BACKUP_DIR:-}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"

usage() {
  cat <<'EOF'
Usage: scripts/backup_database.sh [options]

Options:
  --env-file PATH         Load environment values from PATH before running pg_dump
  --output-dir PATH       Write backup files to PATH
  --retention-days DAYS   Delete dump files older than DAYS (default: 14)
  -h, --help              Show this help

Environment:
  DATABASE_URL            PostgreSQL URL to back up
  BACKUP_DIR              Backup directory override
  BACKUP_RETENTION_DAYS   Retention override in days
  NEWSLY_ENV_FILE         Preferred env file to source
EOF
}

resolve_env_file() {
  if [[ -n "${ENV_FILE}" && -f "${ENV_FILE}" ]]; then
    printf '%s\n' "${ENV_FILE}"
    return 0
  fi

  if [[ -n "${NEWSLY_ENV_FILE:-}" && -f "${NEWSLY_ENV_FILE}" ]]; then
    printf '%s\n' "${NEWSLY_ENV_FILE}"
    return 0
  fi

  if [[ -f "${PROJECT_ROOT}/.env.racknerd" ]]; then
    printf '%s\n' "${PROJECT_ROOT}/.env.racknerd"
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

load_env_file() {
  local env_path="$1"
  if [[ -z "${env_path}" ]]; then
    return 0
  fi

  set -a
  # shellcheck source=/dev/null
  source "${env_path}"
  set +a
}

default_backup_dir() {
  if [[ -n "${BACKUP_DIR}" ]]; then
    printf '%s\n' "${BACKUP_DIR}"
    return 0
  fi

  if [[ -n "${NEWSLY_DATA_ROOT_HOST_PATH:-}" ]]; then
    printf '%s/backups\n' "${NEWSLY_DATA_ROOT_HOST_PATH%/}"
    return 0
  fi

  if [[ -n "${PGDATA:-}" ]]; then
    printf '%s/backups\n' "$(dirname "${PGDATA%/}")"
    return 0
  fi

  printf '/data/backups\n'
}

normalize_database_url() {
  local raw="$1"

  case "${raw}" in
    postgresql://*)
      printf '%s\n' "${raw}"
      ;;
    postgres://*)
      printf 'postgresql://%s\n' "${raw#postgres://}"
      ;;
    postgresql+*://*)
      printf 'postgresql://%s\n' "${raw#*://}"
      ;;
    *)
      echo "ERROR: backup_database.sh only supports PostgreSQL DATABASE_URL values." >&2
      return 1
      ;;
  esac
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file)
      ENV_FILE="$2"
      shift 2
      ;;
    --output-dir)
      BACKUP_DIR="$2"
      shift 2
      ;;
    --retention-days)
      RETENTION_DAYS="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if ! command -v pg_dump >/dev/null 2>&1; then
  echo "ERROR: pg_dump is required but was not found in PATH." >&2
  exit 1
fi

if ! [[ "${RETENTION_DAYS}" =~ ^[0-9]+$ ]]; then
  echo "ERROR: retention days must be an integer, got '${RETENTION_DAYS}'." >&2
  exit 1
fi

if [[ -z "${DATABASE_URL:-}" ]]; then
  resolved_env_file="$(resolve_env_file || true)"
  if [[ -n "${resolved_env_file}" ]]; then
    load_env_file "${resolved_env_file}"
  fi
fi

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "ERROR: DATABASE_URL is required. Set it directly or provide --env-file." >&2
  exit 1
fi

backup_dir="$(default_backup_dir)"
database_url="$(normalize_database_url "${DATABASE_URL}")"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_path="${backup_dir%/}/newsly_${timestamp}.dump"

mkdir -p "${backup_dir}"
umask 077

echo "Starting PostgreSQL backup..."
echo "Backup directory: ${backup_dir}"
echo "Retention days: ${RETENTION_DAYS}"

pg_dump \
  --format=custom \
  --compress=9 \
  --no-owner \
  --no-privileges \
  --file "${backup_path}" \
  "${database_url}"

find "${backup_dir}" -type f -name 'newsly_*.dump' -mtime +"${RETENTION_DAYS}" -delete

echo "Backup complete: ${backup_path}"
