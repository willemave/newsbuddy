#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
harness_root="$(mktemp -d /tmp/newsly-sync-state-test.XXXXXX)"

cleanup() {
  case "${harness_root}" in
    /tmp/newsly-sync-state-test.*) rm -rf -- "${harness_root}" ;;
    *) echo "refusing to remove unexpected test directory: ${harness_root}" >&2 ;;
  esac
}
trap cleanup EXIT

mkdir -p "${harness_root}/bin" "${harness_root}/dumps"
printf 'DATABASE_URL=postgresql://newsly:root@127.0.0.1:5432/newsly_test\n' > "${harness_root}/.env"

cat > "${harness_root}/bin/pg_restore" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'pg_restore %s\n' "$*" >> "${HARNESS_LOG}"
if [[ "${1:-}" == "--list" ]]; then
  [[ -s "${2:-}" ]] && grep -Fxq 'valid dump fixture' "${2}"
fi
EOF

cat > "${harness_root}/bin/psql" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'psql %s\n' "$*" >> "${HARNESS_LOG}"
sql="$*"
if [[ "$*" == *"-f -"* ]]; then
  sql="$(cat)"
  printf 'psql stdin %s\n' "${sql}" >> "${HARNESS_LOG}"
fi
if [[ "${sql}" == *"SELECT 1 FROM pg_database"* ]]; then
  printf '1\n'
fi
EOF

cat > "${harness_root}/bin/dropdb" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'dropdb %s\n' "$*" >> "${HARNESS_LOG}"
EOF

cat > "${harness_root}/bin/createdb" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'createdb %s\n' "$*" >> "${HARNESS_LOG}"
EOF

cat > "${harness_root}/bin/ssh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'ssh %s\n' "$*" >> "${HARNESS_LOG}"
printf 'valid dump fixture\n'
EOF

for command_name in tar lsof screen curl; do
  cat > "${harness_root}/bin/${command_name}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s %s\n' "$(basename "$0")" "$*" >> "${HARNESS_LOG}"
EOF
done
chmod +x "${harness_root}/bin/"*

assert_contains() {
  local pattern="$1"
  local file="$2"
  grep -Fq -- "${pattern}" "${file}" || {
    echo "missing expected command: ${pattern}" >&2
    sed -n '1,200p' "${file}" >&2
    exit 1
  }
}

assert_not_contains() {
  local pattern="$1"
  local file="$2"
  if grep -Fq -- "${pattern}" "${file}"; then
    echo "unexpected command: ${pattern}" >&2
    sed -n '1,200p' "${file}" >&2
    exit 1
  fi
}

line_number() {
  local pattern="$1"
  local file="$2"
  local match
  match="$(grep -Fnm 1 -- "${pattern}" "${file}")" || {
    echo "missing command while checking order: ${pattern}" >&2
    exit 1
  }
  printf '%s\n' "${match%%:*}"
}

assert_before() {
  local first="$1"
  local second="$2"
  local file="$3"
  if (( $(line_number "${first}" "${file}") >= $(line_number "${second}" "${file}") )); then
    echo "command order is unsafe: ${first} must precede ${second}" >&2
    sed -n '1,200p' "${file}" >&2
    exit 1
  fi
}

run_sync() {
  local label="$1"
  local dump_path="$2"
  local reuse_dump="$3"
  local expected_status="$4"
  local log="${harness_root}/${label}.log"
  local output="${harness_root}/${label}.out"
  local -a command=(
    bash "${repo_root}/scripts/sync_production_state.sh"
    --env-file "${harness_root}/.env"
    --dump-path "${dump_path}"
    --skip-assets
    --no-restart-server
  )
  if [[ "${reuse_dump}" == "true" ]]; then
    command+=(--reuse-dump)
  fi

  : > "${log}"
  set +e
  env PATH="${harness_root}/bin:${PATH}" HARNESS_LOG="${log}" "${command[@]}" > "${output}" 2>&1
  local status=$?
  set -e
  if [[ "${status}" -ne "${expected_status}" ]]; then
    echo "${label}: expected status ${expected_status}, got ${status}" >&2
    sed -n '1,200p' "${output}" >&2
    sed -n '1,200p' "${log}" >&2
    exit 1
  fi
  printf '%s\n' "${log}"
}

zero_dump="${harness_root}/dumps/zero.dump"
: > "${zero_dump}"
zero_log="$(run_sync zero-reuse "${zero_dump}" true 1)"
assert_not_contains "psql " "${zero_log}"
assert_not_contains "dropdb " "${zero_log}"
assert_not_contains "createdb " "${zero_log}"

invalid_dump="${harness_root}/dumps/invalid.dump"
printf 'invalid dump fixture\n' > "${invalid_dump}"
invalid_log="$(run_sync invalid-reuse "${invalid_dump}" true 1)"
assert_contains "pg_restore --list ${invalid_dump}" "${invalid_log}"
assert_not_contains "psql " "${invalid_log}"
assert_not_contains "dropdb " "${invalid_log}"
assert_not_contains "createdb " "${invalid_log}"

valid_dump="${harness_root}/dumps/valid.dump"
printf 'valid dump fixture\n' > "${valid_dump}"
valid_log="$(run_sync valid-reuse "${valid_dump}" true 0)"
assert_contains "pg_restore --list ${valid_dump}" "${valid_log}"
assert_contains "dropdb " "${valid_log}"
assert_contains "createdb " "${valid_log}"
assert_before "pg_restore --list ${valid_dump}" "dropdb " "${valid_log}"

fresh_dump="${harness_root}/dumps/fresh.dump"
fresh_log="$(run_sync fresh-dump "${fresh_dump}" false 0)"
assert_contains "docker exec newsly-postgres sh -c" "${fresh_log}"
assert_contains "pg_restore --list ${fresh_dump}.partial" "${fresh_log}"
assert_before "pg_restore --list ${fresh_dump}.partial" "dropdb " "${fresh_log}"
[[ -s "${fresh_dump}" ]]
[[ ! -e "${fresh_dump}.partial" ]]

asset_log="${harness_root}/assets.log"
printf 'IMAGES_BASE_DIR=%s/custom-images\n' "${harness_root}" >> "${harness_root}/.env"
env PATH="${harness_root}/bin:${PATH}" HARNESS_LOG="${asset_log}" \
  bash "${repo_root}/scripts/sync_production_state.sh" \
  --env-file "${harness_root}/.env" --skip-db --asset-dir images \
  --no-restart-server > "${harness_root}/assets.out" 2>&1
assert_contains "tar -xf - -C ${harness_root}/custom-images" "${asset_log}"
assert_not_contains "command not found" "${harness_root}/assets.out"

worker_list="$(sed -n '/^worker_processes=(/,/^)/p' "${repo_root}/scripts/start_services.sh")"
grep -Eq '^[[:space:]]+news_recluster$' <<< "${worker_list}" || {
  echo "local worker list is missing news_recluster" >&2
  exit 1
}

echo "Production state sync safety guard OK."
