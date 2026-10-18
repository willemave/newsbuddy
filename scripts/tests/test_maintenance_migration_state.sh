#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
harness_root="$(mktemp -d /tmp/newsly-maintenance-test.XXXXXX)"
trap 'rm -rf -- "${harness_root}"' EXIT
mkdir "${harness_root}/bin"

cat > "${harness_root}/bin/docker" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
[[ "$*" == *'default_transaction_read_only=on'* ]]
[[ "$*" == *'ON_ERROR_STOP=1'* ]]
if [[ "$*" == *'to_regclass'* ]]; then
  case "${PROBE_TABLE:-present}" in
    missing) printf 'f\n' ;;
    present) printf 't\n' ;;
    failed) exit 1 ;;
    *) printf 'unexpected\n' ;;
  esac
else
  [[ "${PROBE_RESULT:-none}" != failed ]] || exit 1
  printf '%s\n' "${PROBE_RESULT:-none}"
fi
EOF

cat > "${harness_root}/bin/ssh" <<'EOF'
#!/usr/bin/env bash
# Execute the actual checked-out helper from stdin, as the workflow does remotely.
set -euo pipefail
while [[ $# -gt 0 && "$1" != -s ]]; do shift; done
[[ $# -gt 0 ]] || exit 1
shift
[[ "${1:-}" == -- ]] && shift
exec bash -s -- "$@"
EOF
chmod +x "${harness_root}/bin/docker" "${harness_root}/bin/ssh"
export PATH="${harness_root}/bin:${PATH}"

versions=()
while IFS= read -r version; do versions+=("${version}"); done \
  < "${repo_root}/scripts/deploy/maintenance-migrations"
probe="${repo_root}/scripts/deploy/maintenance_migration_state.sh"

check_probe() {
  local expected="$1" table="$2" result="$3"
  local observed
  observed="$(PROBE_TABLE="${table}" PROBE_RESULT="${result}" bash "${probe}" "${versions[@]}")"
  if [[ "${observed}" != "${expected}" ]]; then
    echo "probe: expected '${expected}', got '${observed}'" >&2
    exit 1
  fi
}

check_probe fresh missing none
check_probe fresh present fresh
check_probe none present none
check_probe 'pending 20261002010000,20261018000001,20261018000002' present 'pending 20261002010000,20261018000001,20261018000002'
check_probe 'pending 20261018000002' present 'pending 20261018000002'
check_probe unknown failed none
check_probe unknown malformed none
check_probe unknown present failed
check_probe unknown present unknown
check_probe unknown present 'pending invalid'
check_probe unknown present 'pending 19990101000000'
[[ "$(bash "${probe}" '20261018000002;DROP TABLE users')" == unknown ]]
[[ "$(bash "${probe}")" == unknown ]]

# Execute the workflow's actual decision block, so dispatch behavior cannot drift
# from the local probe/script tests while merely retaining similar-looking SQL.
workflow="${repo_root}/.github/workflows/docker-racknerd-deploy.yml"
awk '
  /          maintenance_versions=\(\)/ { copy = 1 }
  copy && /          EXCLUDES=\(/ { exit }
  copy { sub(/^          /, ""); print }
' "${workflow}" > "${harness_root}/workflow-gate.sh"
[[ -s "${harness_root}/workflow-gate.sh" ]]
cat >> "${harness_root}/workflow-gate.sh" <<'EOF'
printf 'selected=%s\n' "${deploy_args[*]:-online}"
EOF

check_workflow() {
  local result="$1" requested="$2" expected_status="$3" expected_selection="$4"
  local output status
  set +e
  output="$(cd "${repo_root}" && \
    PROBE_RESULT="${result}" MAINTENANCE_CUTOVER_REQUESTED="${requested}" \
    bash -eu -c 'SSH_OPTS=(-p22); REMOTE=fixture; source "$1"' \
      sh "${harness_root}/workflow-gate.sh" 2>&1)"
  status=$?
  set -e
  if [[ "${status}" != "${expected_status}" ]]; then
    echo "workflow ${result}/${requested}: expected ${expected_status}, got ${status}: ${output}" >&2
    exit 1
  fi
  if [[ -n "${expected_selection}" && "${output}" != *"selected=${expected_selection}"* ]]; then
    echo "workflow selected unexpected mode: ${output}" >&2
    exit 1
  fi
  if [[ "${expected_selection}" == online && "${requested}" == true && "${output}" != *'::notice::'* ]]; then
    echo "stale dispatch flag did not produce its notice" >&2
    exit 1
  fi
}

check_workflow 'pending 20261018000001,20261018000002' false 1 ''
check_workflow 'pending 20261018000001,20261018000002' true 0 --maintenance-cutover
check_workflow 'pending 20261018000002' true 0 --maintenance-cutover
for state in none fresh; do
  check_workflow "${state}" false 0 online
  check_workflow "${state}" true 0 online
done
check_workflow unknown false 1 ''
check_workflow unknown true 1 ''
if grep -q 'council_maintenance_cutover\|COUNCIL_CUTOVER_REQUESTED' "${workflow}"; then
  echo "obsolete council-only dispatch input remains" >&2
  exit 1
fi

echo "Maintenance migration probe and workflow gate OK."
