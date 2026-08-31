#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd "${script_dir}/.." && pwd)"
# shellcheck source=scripts/lib/rust_runtime.sh
source "${script_dir}/lib/rust_runtime.sh"

usage() {
  cat <<'EOF'
Usage: scripts/run_sqlx_migrations.sh

Apply the embedded SQLx migrations using the current checkout's newsly-db
binary. Set NEWSLY_ENV_FILE to load a non-default environment file.
EOF
}

case "${1:-}" in
  "") ;;
  -h|--help)
    usage
    exit 0
    ;;
  *)
    echo "unknown option: $1" >&2
    usage >&2
    exit 2
    ;;
esac

if [[ -z "${DATABASE_URL:-}" ]]; then
  env_file="${NEWSLY_ENV_FILE:-}"
  if [[ -z "${env_file}" ]]; then
    env_file="$(newsly_resolve_env_file "${repository_root}" "")"
  fi
  if [[ -n "${env_file}" && -f "${env_file}" ]]; then
    newsly_load_dotenv "${env_file}"
  fi
fi
newsly_normalize_database_environment
newsly_require_database_url

database_command=(migrate)
if [[ "${NEWSLY_SQLX_BASELINE_ADOPTION:-false}" == "true" ]]; then
  if [[ "${NEWSLY_MAINTENANCE_BARRIER_CONFIRMED:-false}" != "true" ]]; then
    echo "SQLx baseline adoption requires NEWSLY_MAINTENANCE_BARRIER_CONFIRMED=true" >&2
    exit 1
  fi
  database_command=(baseline --maintenance-barrier-confirmed)
fi

if ! command -v cargo >/dev/null 2>&1; then
  echo "cargo is required to run the checkout's newsly-db migration binary" >&2
  exit 1
fi
exec cargo run \
  --manifest-path "${repository_root}/rust/Cargo.toml" \
  --locked \
  --package newsly-db \
  -- "${database_command[@]}"
