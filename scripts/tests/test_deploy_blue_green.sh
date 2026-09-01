#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
harness_root="$(mktemp -d /tmp/newsly-deploy-test.XXXXXX)"

cleanup() {
  case "${harness_root}" in
    /tmp/newsly-deploy-test.*) rm -rf -- "${harness_root}" ;;
    *) echo "refusing to remove unexpected test directory: ${harness_root}" >&2 ;;
  esac
}
trap cleanup EXIT

mkdir -p "${harness_root}/bin" "${harness_root}/state"
touch "${harness_root}/.env.racknerd"
printf 'services: {}\n' > "${harness_root}/docker-compose.production.yml"

cat > "${harness_root}/bin/docker" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'docker %s\n' "$*" >> "${HARNESS_LOG}"

if [[ "$*" == *" stop workers scheduler" && "${HARNESS_STOP_FAIL}" == "true" ]]; then
  exit 1
fi

if [[ "${1:-}" == "network" && "${2:-}" == "inspect" ]]; then
  exit 0
fi
if [[ "${1:-}" == "inspect" ]]; then
  container="${@: -1}"
  case "${container}" in
    newsly-postgres | newsly-document-extractor) printf 'healthy\n' ;;
    newsly-workers | newsly-scheduler) printf 'true\n' ;;
    *) printf 'false\n' ;;
  esac
  exit 0
fi
if [[ "${1:-}" == "exec" && "${2:-}" == newsly-api-* ]]; then
  printf 'https://newsly.test\n'
  exit 0
fi
exit 0
EOF

cat > "${harness_root}/bin/curl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
url="${@: -1}"
printf 'curl %s\n' "${url}" >> "${HARNESS_LOG}"
if [[ "${url}" == "https://newsly.test/health" && "${HARNESS_PUBLIC_FAIL}" == "true" ]]; then
  exit 22
fi
EOF

cat > "${harness_root}/bin/sleep" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'sleep %s\n' "$*" >> "${HARNESS_LOG}"
EOF

cat > "${harness_root}/switch-api-slot" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'switch %s\n' "$1" >> "${HARNESS_LOG}"
if [[ "$1" == "green" && "${HARNESS_TARGET_SWITCH_FAIL}" == "true" ]]; then
  exit 1
fi
if [[ "$1" == "blue" && "${HARNESS_ROLLBACK_FAIL}" == "true" ]]; then
  exit 1
fi
printf '%s\n' "$1" > "${HARNESS_STATE_DIR}/active-api-slot"
EOF

chmod +x \
  "${harness_root}/bin/docker" \
  "${harness_root}/bin/curl" \
  "${harness_root}/bin/sleep" \
  "${harness_root}/switch-api-slot"

assert_contains() {
  local pattern="$1"
  local file="$2"
  if ! grep -Fq -- "${pattern}" "${file}"; then
    echo "missing expected command: ${pattern}" >&2
    sed -n '1,240p' "${file}" >&2
    exit 1
  fi
}

assert_not_contains() {
  local pattern="$1"
  local file="$2"
  if grep -Fq -- "${pattern}" "${file}"; then
    echo "unexpected command: ${pattern}" >&2
    sed -n '1,240p' "${file}" >&2
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
    sed -n '1,240p' "${file}" >&2
    exit 1
  fi
}

run_deploy() {
  local label="$1"
  local public_failure="$2"
  local expected_status="$3"
  local rollback_failure="${4:-false}"
  local stop_failure="${5:-false}"
  local active_slot="${6:-blue}"
  local target_switch_failure="${7:-false}"
  local log="${harness_root}/${label}.log"
  local output="${harness_root}/${label}.out"

  : > "${log}"
  if [[ "${active_slot}" == "missing" ]]; then
    rm -f "${harness_root}/state/active-api-slot"
  else
    printf '%s\n' "${active_slot}" > "${harness_root}/state/active-api-slot"
  fi
  rm -f \
    "${harness_root}/state/active-image" \
    "${harness_root}/state/active-extractor-image" \
    "${harness_root}/state/green-image"

  set +e
  env \
    PATH="${harness_root}/bin:${PATH}" \
    HARNESS_LOG="${log}" \
    HARNESS_PUBLIC_FAIL="${public_failure}" \
    HARNESS_ROLLBACK_FAIL="${rollback_failure}" \
    HARNESS_STOP_FAIL="${stop_failure}" \
    HARNESS_TARGET_SWITCH_FAIL="${target_switch_failure}" \
    HARNESS_STATE_DIR="${harness_root}/state" \
    NEWSLY_DEPLOY_COMPOSE_FILE="${harness_root}/docker-compose.production.yml" \
    NEWSLY_DEPLOY_ENV_FILE="${harness_root}/.env.racknerd" \
    NEWSLY_DEPLOY_STATE_DIR="${harness_root}/state" \
    NEWSLY_DEPLOY_SWITCH_SCRIPT="${harness_root}/switch-api-slot" \
    NEWSLY_SQLX_BASELINE_ADOPTION=false \
    NEWSLY_EXTRACTOR_IMAGE=test-extractor \
    bash "${repo_root}/scripts/deploy_blue_green.sh" test-image > "${output}" 2>&1
  local status=$?
  set -e

  if [[ "${status}" -ne "${expected_status}" ]]; then
    echo "${label}: expected status ${expected_status}, got ${status}" >&2
    sed -n '1,240p' "${output}" >&2
    sed -n '1,240p' "${log}" >&2
    exit 1
  fi
  printf '%s\n' "${log}"
}

success_log="$(run_deploy success false 0)"
assert_contains "NEWSLY_MAINTENANCE_BARRIER_CONFIRMED=false migrate" "${success_log}"
assert_contains "curl http://127.0.0.1:8002/health" "${success_log}"
assert_contains " stop workers scheduler" "${success_log}"
assert_contains "switch green" "${success_log}"
assert_contains "curl https://newsly.test/health" "${success_log}"
assert_contains " up -d --no-deps workers scheduler" "${success_log}"
assert_not_contains " stop api_blue" "${success_log}"
assert_not_contains " stop api_green" "${success_log}"
assert_not_contains "docker start newsly-workers" "${success_log}"
assert_before "NEWSLY_MAINTENANCE_BARRIER_CONFIRMED=false migrate" "curl http://127.0.0.1:8002/health" "${success_log}"
assert_before "curl http://127.0.0.1:8002/health" " stop workers scheduler" "${success_log}"
assert_before " stop workers scheduler" "switch green" "${success_log}"
assert_before "switch green" "curl https://newsly.test/health" "${success_log}"
assert_before "curl https://newsly.test/health" " up -d --no-deps workers scheduler" "${success_log}"

failure_log="$(run_deploy public-failure true 1)"
assert_contains " stop workers scheduler" "${failure_log}"
assert_contains "switch green" "${failure_log}"
assert_contains "switch blue" "${failure_log}"
assert_not_contains "docker start newsly-workers" "${failure_log}"
assert_not_contains "docker start newsly-scheduler" "${failure_log}"
assert_not_contains " up -d --no-deps workers scheduler" "${failure_log}"
assert_before " stop workers scheduler" "switch green" "${failure_log}"
assert_before "switch green" "switch blue" "${failure_log}"

rollback_failure_log="$(run_deploy rollback-failure true 1 true)"
assert_contains "switch green" "${rollback_failure_log}"
assert_contains "switch blue" "${rollback_failure_log}"
assert_not_contains "docker start newsly-workers" "${rollback_failure_log}"
assert_not_contains "docker start newsly-scheduler" "${rollback_failure_log}"
assert_not_contains " up -d --no-deps workers scheduler" "${rollback_failure_log}"

missing_active_log="$(run_deploy missing-active true 1 false false missing)"
assert_contains "switch blue" "${missing_active_log}"
assert_not_contains "docker start newsly-workers" "${missing_active_log}"
assert_not_contains "docker start newsly-scheduler" "${missing_active_log}"
assert_not_contains " up -d --no-deps workers scheduler" "${missing_active_log}"

target_switch_failure_log="$(run_deploy target-switch-failure false 1 false false blue true)"
assert_contains "switch green" "${target_switch_failure_log}"
assert_not_contains "docker start newsly-workers" "${target_switch_failure_log}"
assert_not_contains "docker start newsly-scheduler" "${target_switch_failure_log}"
assert_not_contains "curl https://newsly.test/health" "${target_switch_failure_log}"

stop_failure_log="$(run_deploy stop-failure false 1 false true)"
assert_contains " stop workers scheduler" "${stop_failure_log}"
assert_contains "docker start newsly-workers" "${stop_failure_log}"
assert_contains "docker start newsly-scheduler" "${stop_failure_log}"
assert_not_contains "switch green" "${stop_failure_log}"

echo "Blue-green deploy ordering guard OK."
