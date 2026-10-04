#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=scripts/lib/rust_runtime.sh
source "${repository_root}/scripts/lib/rust_runtime.sh"

env_file="$(newsly_resolve_env_file "${repository_root}" "${NEWSLY_ENV_FILE:-}")"
newsly_load_dotenv "${env_file}"
newsly_normalize_database_environment
newsly_require_database_url

for command_name in cargo createdb dropdb psql; do
  command -v "${command_name}" >/dev/null 2>&1 || {
    echo "required command not found: ${command_name}" >&2
    exit 1
  }
done

database_url_without_query="${DATABASE_URL%%\?*}"
database_query=""
if [[ "${DATABASE_URL}" == *\?* ]]; then
  database_query="?${DATABASE_URL#*\?}"
fi
database_prefix="${database_url_without_query%/*}"
fixture_database="newsbuddy_launch_fixture_check_${$}"
maintenance_url="${database_prefix}/postgres${database_query}"
fixture_url="${database_prefix}/${fixture_database}${database_query}"

cleanup() {
  dropdb --if-exists --force --maintenance-db="${maintenance_url}" "${fixture_database}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

createdb --maintenance-db="${maintenance_url}" "${fixture_database}"
SQLX_OFFLINE=true cargo run --quiet --manifest-path "${repository_root}/rust/Cargo.toml" \
  -p newsly-db -- --database-url "${fixture_url}" migrate --maintenance-barrier-confirmed
SQLX_OFFLINE=true cargo run --quiet --manifest-path "${repository_root}/rust/Cargo.toml" \
  -p newsly-admin -- --database-url "${fixture_url}" e2e seed \
  --namespace appstore-launch --confirm-local >/dev/null
psql "${fixture_url}" -v ON_ERROR_STOP=1 \
  -f "${repository_root}/scripts/fixtures/launch_briefing.sql" >/dev/null

story_count="$(psql "${fixture_url}" -Atqc "SELECT count(*) FROM contents WHERE content_metadata->>'fixture_namespace'='appstore-launch'")"
[[ "${story_count}" -ge 8 ]] || {
  echo "launch fixture check expected at least 8 stories, found ${story_count}" >&2
  exit 1
}
echo "Launch Briefing fixture schema check passed (${story_count} stories)."
