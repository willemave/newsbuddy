#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

POSTGRES_FORMULA="${POSTGRES_FORMULA:-postgresql@17}"
POSTGRES_HOST="${POSTGRES_HOST:-127.0.0.1}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
APP_DB="${POSTGRES_DB:-newsly}"
APP_USER="${POSTGRES_USER:-newsly}"
APP_PASSWORD="${POSTGRES_PASSWORD:-$(openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | head -c 24)}"
ENV_FILE="${NEWSLY_ENV_FILE:-${PROJECT_ROOT}/.env}"

usage() {
  cat <<EOF
Usage: scripts/setup_local_postgres.sh [options]

Options:
  --env-file PATH    Env file to update (default: .env)
  --db NAME          Database name (default: ${APP_DB})
  --user NAME        Database role (default: ${APP_USER})
  --password VALUE   Database password (default: generated)
  --host HOST        Host written into DATABASE_URL (default: ${POSTGRES_HOST})
  --port PORT        Port written into DATABASE_URL (default: ${POSTGRES_PORT})
  -h, --help         Show this help

Environment overrides:
  POSTGRES_FORMULA   Homebrew formula to install/start (default: postgresql@17)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file)
      ENV_FILE="$2"
      shift 2
      ;;
    --db)
      APP_DB="$2"
      shift 2
      ;;
    --user)
      APP_USER="$2"
      shift 2
      ;;
    --password)
      APP_PASSWORD="$2"
      shift 2
      ;;
    --host)
      POSTGRES_HOST="$2"
      shift 2
      ;;
    --port)
      POSTGRES_PORT="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if ! command -v brew >/dev/null 2>&1; then
  echo "ERROR: Homebrew is required to install local PostgreSQL." >&2
  exit 1
fi

echo "Installing ${POSTGRES_FORMULA} with Homebrew if needed..."
if ! brew list --versions "${POSTGRES_FORMULA}" >/dev/null 2>&1; then
  brew install "${POSTGRES_FORMULA}"
fi

BREW_PREFIX="$(brew --prefix "${POSTGRES_FORMULA}")"
export PATH="${BREW_PREFIX}/bin:${PATH}"

echo "Starting ${POSTGRES_FORMULA}..."
brew services start "${POSTGRES_FORMULA}" >/dev/null

echo "Waiting for PostgreSQL on ${POSTGRES_HOST}:${POSTGRES_PORT}..."
for _ in {1..30}; do
  if pg_isready -h "${POSTGRES_HOST}" -p "${POSTGRES_PORT}" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! pg_isready -h "${POSTGRES_HOST}" -p "${POSTGRES_PORT}" >/dev/null 2>&1; then
  echo "ERROR: PostgreSQL did not become ready in time." >&2
  exit 1
fi

echo "Ensuring application role and database exist..."
psql postgres -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${APP_USER}') THEN
    EXECUTE format('CREATE ROLE %I LOGIN PASSWORD %L', '${APP_USER}', '${APP_PASSWORD}');
  ELSE
    EXECUTE format('ALTER ROLE %I WITH LOGIN PASSWORD %L', '${APP_USER}', '${APP_PASSWORD}');
  END IF;
END
\$\$;
SQL

if ! psql postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '${APP_DB}'" | grep -q 1; then
  createdb -O "${APP_USER}" "${APP_DB}"
fi

DATABASE_URL="postgresql+psycopg://${APP_USER}:${APP_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${APP_DB}"
mkdir -p "$(dirname "${ENV_FILE}")"
touch "${ENV_FILE}"

tmp_env="$(mktemp)"
database_url_written="false"
environment_written="false"

while IFS= read -r line || [[ -n "${line}" ]]; do
  case "${line}" in
    DATABASE_URL=*)
      printf 'DATABASE_URL=%s\n' "${DATABASE_URL}" >> "${tmp_env}"
      database_url_written="true"
      ;;
    ENVIRONMENT=*)
      printf '%s\n' "${line}" >> "${tmp_env}"
      environment_written="true"
      ;;
    *)
      printf '%s\n' "${line}" >> "${tmp_env}"
      ;;
  esac
done < "${ENV_FILE}"

if [[ "${database_url_written}" != "true" ]]; then
  printf 'DATABASE_URL=%s\n' "${DATABASE_URL}" >> "${tmp_env}"
fi

if [[ "${environment_written}" != "true" ]]; then
  printf 'ENVIRONMENT=development\n' >> "${tmp_env}"
fi

mv "${tmp_env}" "${ENV_FILE}"

echo "Local PostgreSQL is ready."
echo "Formula: ${POSTGRES_FORMULA}"
echo "Database: ${APP_DB}"
echo "User: ${APP_USER}"
echo "Env file updated: ${ENV_FILE}"
echo "DATABASE_URL=${DATABASE_URL}"
