#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
harness_root="$(mktemp -d /tmp/newsly-postgres-setup-test.XXXXXX)"

cleanup() {
  case "${harness_root}" in
    /tmp/newsly-postgres-setup-test.*) rm -rf -- "${harness_root}" ;;
    *) echo "refusing to remove unexpected test directory: ${harness_root}" >&2 ;;
  esac
}
trap cleanup EXIT

mkdir -p "${harness_root}/bin"

cat > "${harness_root}/bin/brew" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'brew %s\n' "$*" >> "${HARNESS_LOG}"
if [[ "${1:-}" == "--prefix" ]]; then
  printf '%s\n' "${HARNESS_ROOT}"
fi
EOF

cat > "${harness_root}/bin/pg_isready" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF

cat > "${harness_root}/bin/psql" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
input="$(cat)"
printf 'psql %s\n%s\n' "$*" "${input}" >> "${HARNESS_LOG}"
if [[ "${input}" == *"SELECT EXISTS"* ]]; then
  printf '%s\n' "${HARNESS_ROLE_EXISTS}"
elif [[ "${input}" == *"SELECT 1 FROM pg_database"* ]]; then
  printf '1\n'
fi
EOF

cat > "${harness_root}/bin/createdb" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'createdb %s\n' "$*" >> "${HARNESS_LOG}"
EOF

cat > "${harness_root}/bin/openssl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'generated-password\n'
EOF

cat > "${harness_root}/bin/sleep" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF

chmod +x "${harness_root}/bin/"*

assert_contains() {
  local pattern="$1"
  local file="$2"
  grep -Fq -- "${pattern}" "${file}" || {
    echo "missing expected text: ${pattern}" >&2
    sed -n '1,200p' "${file}" >&2
    exit 1
  }
}

assert_not_contains() {
  local pattern="$1"
  local file="$2"
  if grep -Fq -- "${pattern}" "${file}"; then
    echo "unexpected text: ${pattern}" >&2
    sed -n '1,200p' "${file}" >&2
    exit 1
  fi
}

run_setup() {
  local label="$1"
  local role_exists="$2"
  local initial_url="$3"
  local explicit_password="$4"
  local env_file="${harness_root}/${label}.env"
  local log="${harness_root}/${label}.log"
  printf 'DATABASE_URL=%s\nENVIRONMENT=development\n' "${initial_url}" > "${env_file}"
  : > "${log}"
  local -a command=(
    env
    -u POSTGRES_PASSWORD
    PATH="${harness_root}/bin:${PATH}"
    HARNESS_ROOT="${harness_root}"
    HARNESS_LOG="${log}"
    HARNESS_ROLE_EXISTS="${role_exists}"
    NEWSLY_ENV_FILE="${env_file}"
  )
  if [[ -n "${explicit_password}" ]]; then
    command+=(POSTGRES_PASSWORD="${explicit_password}")
  fi
  command+=(bash "${repo_root}/scripts/setup_local_postgres.sh")
  "${command[@]}" > "${harness_root}/${label}.out" 2>&1
  printf '%s\t%s\n' "${log}" "${env_file}"
}

IFS=$'\t' read -r existing_log existing_env < <(
  run_setup existing-role t postgresql://newsly:preserved@127.0.0.1:5432/newsly ""
)
assert_not_contains "ALTER ROLE" "${existing_log}"
assert_not_contains "CREATE ROLE" "${existing_log}"
assert_contains "DATABASE_URL=postgresql://newsly:preserved@127.0.0.1:5432/newsly" "${existing_env}"

IFS=$'\t' read -r rotated_log rotated_env < <(
  run_setup explicit-password t postgresql://newsly:old@127.0.0.1:5432/newsly replacement
)
assert_contains "ALTER ROLE" "${rotated_log}"
assert_contains "DATABASE_URL=postgresql://newsly:replacement@127.0.0.1:5432/newsly" "${rotated_env}"

IFS=$'\t' read -r created_log created_env < <(
  run_setup missing-role f "" ""
)
assert_contains "CREATE ROLE" "${created_log}"
assert_not_contains "ALTER ROLE" "${created_log}"
assert_contains "DATABASE_URL=postgresql://newsly:generated-password@127.0.0.1:5432/newsly" "${created_env}"

echo "Local PostgreSQL setup safety guard OK."
